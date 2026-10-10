"""Cost rules, kept pure so they are tested without a database.

Nothing here is stored: the paid state of an auto-pay invoice and every alert
are computed at read time, so correcting a service's `auto_pay`, expected cost
or threshold corrects the whole history at once.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

WINDOW = 6  # invoices averaged for the reference
MIN_HISTORY = 3  # fewer than this and the expected cost is the reference
CENTS = Decimal("0.01")


def is_paid(paid_at: date | None, auto_pay: bool, due_on: date | None, today: date) -> bool:
    if paid_at is not None:
        return True
    return auto_pay and due_on is not None and due_on <= today


def reference(previous: Sequence[Decimal], expected: Decimal | None) -> Decimal | None:
    """`previous` is oldest first."""
    window = previous[-WINDOW:]
    if len(window) >= MIN_HISTORY:
        return (sum(window) / len(window)).quantize(CENTS)
    return expected


def is_flagged(total: Decimal, ref: Decimal | None, threshold_pct: int) -> bool:
    return ref is not None and total > ref * (1 + Decimal(threshold_pct) / 100)


@dataclass(frozen=True)
class Point:
    invoice_id: uuid.UUID
    when: date
    total: Decimal
    reference: Decimal | None
    flagged: bool


def series(
    invoices: Iterable[tuple[uuid.UUID, date, Decimal]],
    expected: Decimal | None,
    threshold_pct: int,
) -> list[Point]:
    ordered = sorted(invoices, key=lambda row: (row[1], str(row[0])))
    points: list[Point] = []
    for index, (invoice_id, when, total) in enumerate(ordered):
        ref = reference([row[2] for row in ordered[:index]], expected)
        points.append(Point(invoice_id, when, total, ref, is_flagged(total, ref, threshold_pct)))
    return points


def month_of(when: date) -> str:
    return f"{when.year:04d}-{when.month:02d}"


def monthly_totals(rows: Iterable[tuple[date, Decimal]]) -> list[tuple[str, Decimal]]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for when, total in rows:
        totals[month_of(when)] += total
    return sorted(totals.items())
