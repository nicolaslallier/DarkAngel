"""Reading an uploaded invoice PDF with Ollama, after the response is sent.

Best effort, like app.summary: whatever goes wrong the invoice is still there,
marked `failed`, and the person fills it in by hand. The model's answer is never
trusted: every field is cleaned on its own, a bad one is dropped, and nothing
becomes a validated invoice without a human pressing the button.
"""

import json
import logging
import re
import unicodedata
import uuid
from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app import summary
from app.core.db import session_factory
from app.models.files import File
from app.repositories.invoices import InvoiceRepository
from app.repositories.providers import ProviderRepository

log = logging.getLogger(__name__)

PROMPT = (
    "You read a household service invoice (utility, telecom, insurance...). "
    "Reply with ONE JSON object and nothing else, using exactly these keys, "
    "with null for anything the invoice does not state: "
    '"provider" (the company), "service" (what is billed, e.g. Internet or Electricity), '
    '"total" (the amount due, a number), "issued_on", "due_on", "period_start", '
    '"period_end" (dates as YYYY-MM-DD), "invoice_number", "consumption_qty" '
    '(a number), "consumption_unit" (e.g. kWh, GB), and "taxes" '
    '(a list of {"name", "amount"}). The invoice text follows.\n\n'
)

GENERIC_ERROR = "The invoice could not be read"


class ReadError(Exception):
    """A failure this module raises on purpose; its message is safe to show."""


Provider = tuple[uuid.UUID, str]
ServiceRow = tuple[uuid.UUID, uuid.UUID, str, str]  # id, provider_id, name, category


# --- cleaning ---------------------------------------------------------------


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:limit] or None


def _money(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = Decimal(str(value))
    elif isinstance(value, str):
        # Drops spaces, non-breaking spaces and currency signs. The last separator
        # is the decimal mark, unless it repeats (then it is thousands): the other
        # kind is always a thousands separator.
        if re.search(r"\d[eE][+-]?\d", value):  # 1e30 is not "130"
            return None
        text = re.sub(r"[^\d,.\-]", "", value)
        mark = max(text.rfind(","), text.rfind("."))
        if mark >= 0 and text.count(text[mark]) > 1:
            text = re.sub(r"[,.]", "", text)
        elif mark >= 0:
            text = re.sub(r"[,.]", "", text[:mark]) + "." + text[mark + 1 :]
        try:
            result = Decimal(text)
        except InvalidOperation:
            return None
    else:
        return None
    return result if result.is_finite() else None


def _bounded(value: Any, limit: int, places: str) -> Decimal | None:
    """The API's own limits: non-negative, and small enough for the NUMERIC column."""
    number = _money(value)
    if number is None or number < 0 or number >= limit:
        return None
    return number.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def clean(raw: dict) -> dict:
    taxes = []
    for line in raw.get("taxes") if isinstance(raw.get("taxes"), list) else []:
        if (
            not isinstance(line, dict)
            or (amount := _bounded(line.get("amount"), 10**10, "0.01")) is None
        ):
            continue
        taxes.append({"name": _text(line.get("name"), 100) or "Taxe", "amount": amount})
    return {
        "provider": _text(raw.get("provider"), 200),
        "service": _text(raw.get("service"), 200),
        "total": _bounded(raw.get("total"), 10**10, "0.01"),
        "issued_on": _date(raw.get("issued_on")),
        "due_on": _date(raw.get("due_on")),
        "period_start": _date(raw.get("period_start")),
        "period_end": _date(raw.get("period_end")),
        "invoice_number": _text(raw.get("invoice_number"), 100),
        "consumption_qty": _bounded(raw.get("consumption_qty"), 10**11, "0.001"),
        "consumption_unit": _text(raw.get("consumption_unit"), 30),
        "taxes": taxes[:20],
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (date, Decimal)):
        return value.isoformat() if isinstance(value, date) else str(value)
    return value


# --- matching ---------------------------------------------------------------


def normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


def _match(name: str | None, rows: Sequence[tuple[uuid.UUID, str]]):
    """The one row whose name is, or contains, or is contained in `name`."""
    wanted = normalize(name or "")
    if not wanted:
        return None
    exact = [r for r in rows if normalize(r[1]) == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(wanted) < 3:
        return None
    loose = [
        r
        for r in rows
        if len(normalize(r[1])) >= 3 and (normalize(r[1]) in wanted or wanted in normalize(r[1]))
    ]
    return loose[0] if len(loose) == 1 else None


def build(
    raw: dict,
    providers: Sequence[Provider],
    services: Sequence[ServiceRow],
    preselect_provider_id: uuid.UUID | None = None,
    preselect_service_id: uuid.UUID | None = None,
) -> tuple[dict, dict]:
    data = clean(raw)
    chosen_service = next((s for s in services if s[0] == preselect_service_id), None)
    if chosen_service is not None:
        provider = next((p for p in providers if p[0] == chosen_service[1]), None)
    elif preselect_provider_id is not None:
        provider = next((p for p in providers if p[0] == preselect_provider_id), None)
    else:
        provider = _match(data["provider"], providers)

    pool = [s for s in services if provider is not None and s[1] == provider[0]]
    if chosen_service is None:
        found = _match(data["service"], [(s[0], s[2]) for s in pool]) or _match(
            data["service"], [(s[0], s[3]) for s in pool]
        )
        chosen_service = next((s for s in pool if found and s[0] == found[0]), None)
        if chosen_service is None and len(pool) == 1 and not data["service"]:
            chosen_service = pool[0]

    taxes = data.pop("taxes")
    provider_name, service_name = data.pop("provider"), data.pop("service")
    fields = {k: v for k, v in data.items() if v is not None}
    extraction = {
        "raw": _jsonable(
            {**data, "provider": provider_name, "service": service_name, "taxes": taxes}
        ),
        "provider_id": str(preselect_provider_id) if preselect_provider_id else None,
        "candidates": {
            "provider": {"id": str(provider[0]), "name": provider[1]} if provider else None,
            "service": (
                {"id": str(chosen_service[0]), "name": chosen_service[2]}
                if chosen_service
                else None
            ),
            "new_provider": provider_name if provider is None else None,
            "new_service": service_name if chosen_service is None and service_name else None,
        },
    }
    return fields, extraction


# --- the background job -------------------------------------------------------


def _pdf_text(object_key: str, size: int) -> str:
    if size > summary.MAX_READ_BYTES:
        raise ReadError("the PDF is too big to read")
    return summary.pdf_text(summary._read(object_key, summary.MAX_READ_BYTES))


def _ask(text: str) -> dict:
    answer = json.loads(summary.ask_ollama(PROMPT + text[: summary.TEXT_CHARS], as_json=True))
    if not isinstance(answer, dict):
        raise ReadError("the model did not answer with a JSON object")
    return answer


def extract(invoice_id: uuid.UUID) -> None:
    """Runs as a BackgroundTask: the request's session is closed by now, so
    this opens its own. Never raises."""
    try:
        with session_factory()() as db:
            invoices = InvoiceRepository(db)
            invoice = invoices.by_id(invoice_id)
            if invoice is None:
                return
            invoices.set_status(invoice, "extracting")
            try:
                file_row = db.get(File, invoice.file_id) if invoice.file_id else None
                if file_row is None or file_row.deleted_at is not None:
                    raise ReadError("the PDF is missing")
                text = _pdf_text(file_row.object_key, file_row.size_bytes)
                if not text.strip():
                    raise ReadError("no readable text: a scanned PDF?")
                raw = _ask(text)

                catalog = ProviderRepository(db)
                providers = [(p.id, p.name) for p in catalog.list_providers(invoice.household_id)]
                services = [
                    (s.id, s.provider_id, s.name, s.category)
                    for s in catalog.services(invoice.household_id)
                ]
                preselect = (invoice.extraction or {}).get("provider_id")
                fields, extraction = build(
                    raw,
                    providers,
                    services,
                    preselect_provider_id=uuid.UUID(preselect) if preselect else None,
                    preselect_service_id=invoice.service_id,
                )
                number = fields.get("invoice_number")
                if (
                    invoice.service_id
                    and number
                    and invoices.find_duplicate(invoice.service_id, number, exclude_id=invoice.id)
                ):
                    del fields["invoice_number"]  # the person types it; validate re-checks
                invoices.finish(invoice, "to_validate", extraction=extraction, fields=fields)
            except Exception as e:
                log.exception("reading invoice %s failed", invoice_id)
                db.rollback()
                invoices.finish(
                    invoice, "failed", error=str(e) if isinstance(e, ReadError) else GENERIC_ERROR
                )
    except Exception:
        log.exception("could not record the outcome for invoice %s", invoice_id)
