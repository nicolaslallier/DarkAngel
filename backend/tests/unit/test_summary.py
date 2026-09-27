import base64
import io
import json
import uuid
import zipfile
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from app import summary
from app.core.config import get_settings
from app.main import app
from tests.conftest import token

client = TestClient(app)


def pdf(text: str) -> bytes:
    """A one-page PDF showing `text`, with a correct xref table."""
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n%s\nendstream" % (len(stream), stream),
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj%s endobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


PDF = pdf("Hello PDF")

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def zipped(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


DOCX = zipped(
    {
        "word/document.xml": f"<w:document {W}><w:body>"
        "<w:p><w:r><w:t>Rapport </w:t></w:r><w:r><w:t>annuel</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Ventes en hausse.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    }
)
XLSX = zipped(
    {
        "xl/sharedStrings.xml": f"<sst {S}><si><t>Produit</t></si><si><t>Pommes</t></si></sst>",
        "xl/worksheets/sheet1.xml": f"<worksheet {S}><sheetData>"
        '<row><c t="s"><v>0</v></c><c t="inlineStr"><is><t>Prix</t></is></c></row>'
        '<row><c t="s"><v>1</v></c><c><v>2.5</v></c><c t="s"/></row>'
        "</sheetData></worksheet>",
    }
)


def auth():
    return {"Authorization": f"Bearer {token()}"}


def upload(name="notes.txt", data=b"hello", content_type="text/plain"):
    return client.post("/api/files", headers=auth(), files={"file": (name, data, content_type)})


# --- extraction ---


def test_text_of_reads_utf8_text():
    assert summary.text_of("Bonjour à tous".encode()) == "Bonjour à tous"


def test_text_of_tolerates_a_character_split_by_the_head_cut():
    # "é" is two bytes; the cut keeps only the first.
    assert summary.text_of("café".encode()[:-1]) == "caf"


def test_text_of_refuses_binary():
    assert summary.text_of(b"%PDF-1.7\n\xe2\xe3\xcf\xd3\xff\xfe") is None
    assert summary.text_of(b"\x89PNG\r\n\x1a\n\x00\x00") is None
    assert summary.text_of(b"   \n") is None


def test_pdf_text():
    assert summary.pdf_text(PDF).strip() == "Hello PDF"


def test_docx_text_keeps_paragraphs_and_joins_runs():
    assert summary.docx_text(DOCX) == "Rapport annuel\nVentes en hausse."


def test_xlsx_text_resolves_shared_inline_and_raw_cells():
    assert summary.xlsx_text(XLSX) == "## sheet1\nProduit\tPrix\nPommes\t2.5\t"


def test_an_oversized_zip_member_is_not_parsed(monkeypatch):
    monkeypatch.setattr(summary, "MAX_MEMBER_BYTES", 10)
    with pytest.raises(ValueError, match="too big"):
        summary.docx_text(DOCX)


# --- what the model is shown ---


def stored(store, key, data, content_type="application/octet-stream"):
    store.objects[key] = (data, content_type)
    return key


def test_material_for_text_and_markdown(store):
    for name in ("notes.txt", "README.md"):
        key = stored(store, f"u/{name}", b"# Plan\nShip it.", "text/markdown")
        prompt, images = summary.material(name, "text/markdown", key, 15)
        assert prompt.endswith("# Plan\nShip it.") and images == []


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        ("report.pdf", PDF, "Hello PDF"),
        ("report.docx", DOCX, "Ventes en hausse."),
        ("prices.xlsx", XLSX, "Pommes\t2.5"),
    ],
)
def test_material_for_documents(store, name, data, expected):
    key = stored(store, f"u/{name}", data)
    prompt, images = summary.material(name, "application/octet-stream", key, len(data))
    assert expected in prompt and images == []


def test_material_for_an_image_is_the_image_as_a_jpeg(store, monkeypatch):
    key = stored(store, "u/cat.webp", b"RIFF-webp-bytes", "image/webp")
    seen = {}

    def jpeg(source, *, data=None, at=None):
        seen.update(source=source, data=data, at=at)
        return b"JPEG"

    monkeypatch.setattr(summary, "_jpeg", jpeg)

    prompt, images = summary.material("cat.webp", "image/webp", key, 15)

    assert images == [b"JPEG"]
    assert seen == {"source": "pipe:0", "data": b"RIFF-webp-bytes", "at": None}
    assert "image" in prompt


def test_material_for_a_video_is_stills_through_it(monkeypatch):
    monkeypatch.setattr(summary, "_video_url", lambda key: f"http://s3/{key}?signed")
    monkeypatch.setattr(summary, "_duration", lambda url: 100.0)
    taken = []

    def jpeg(source, *, data=None, at=None):
        taken.append((source, at))
        return b"JPEG"

    monkeypatch.setattr(summary, "_jpeg", jpeg)

    # 5 GiB: a video is never read whole, so its size is no reason to skip it.
    prompt, images = summary.material("trip.mov", "video/quicktime", "u/k", 5 * 1024**3)

    assert taken == [("http://s3/u/k?signed", at) for at in (10.0, 35.0, 60.0, 85.0)]
    assert images == [b"JPEG"] * 4
    assert "4 stills" in prompt


def test_nothing_for_binary_unknowns_or_oversized_documents(store):
    key = stored(store, "u/blob.bin", b"\x00\x01\x02")
    assert summary.material("blob.bin", "application/octet-stream", key, 3) is None
    too_big = summary.MAX_READ_BYTES + 1
    assert summary.material("big.pdf", "application/pdf", key, too_big) is None


def test_nothing_for_a_pdf_without_text(store):
    key = stored(store, "u/scan.pdf", pdf("   "))
    assert summary.material("scan.pdf", "application/pdf", key, len(PDF)) is None


# --- asking Ollama ---


def test_ask_ollama_sends_images_and_drops_the_thinking(monkeypatch):
    monkeypatch.setattr(get_settings(), "ollama_url", "http://ollama:11434/")
    sent = {}

    def urlopen(request, timeout):
        sent["url"], sent["body"] = request.full_url, json.loads(request.data)
        reply = {"response": "<think>hmm</think>\n  A cat on a sofa.  "}
        return nullcontext(io.BytesIO(json.dumps(reply).encode()))

    monkeypatch.setattr(summary.urllib.request, "urlopen", urlopen)

    assert summary.ask_ollama("Describe", [b"JPEG"]) == "A cat on a sofa."
    assert sent["url"] == "http://ollama:11434/api/generate"
    assert sent["body"]["stream"] is False
    assert sent["body"]["images"] == [base64.b64encode(b"JPEG").decode()]


class Recorder:
    def __init__(self, _db=None):
        Recorder.calls = []

    def set_summary(self, owner_sub, file_id, text):
        Recorder.calls.append((owner_sub, file_id, text))


def summarize_with(monkeypatch, found, ask):
    monkeypatch.setattr(summary, "session_factory", lambda: lambda: nullcontext())
    monkeypatch.setattr(summary, "FileRepository", Recorder)
    monkeypatch.setattr(summary, "material", lambda *args: found)
    monkeypatch.setattr(summary, "ask_ollama", ask)
    file_id = uuid.uuid4()
    summary.summarize("user-1", file_id, "a.txt", "text/plain", "k", 4)
    return Recorder.calls, file_id


def test_summarize_stores_the_answer(monkeypatch):
    calls, file_id = summarize_with(monkeypatch, ("prompt", []), lambda _p, _i: "A list.")
    assert calls == [("user-1", file_id, "A list.")]


def test_summarize_stores_none_when_ollama_fails(monkeypatch):
    def down(_prompt, _images):
        raise OSError("connection refused")

    calls, file_id = summarize_with(monkeypatch, ("prompt", []), down)
    assert calls == [("user-1", file_id, None)]


def test_summarize_never_asks_about_what_it_cannot_read(monkeypatch):
    def never(_prompt, _images):
        raise AssertionError("asked about an unreadable file")

    calls, file_id = summarize_with(monkeypatch, None, never)
    assert calls == [("user-1", file_id, None)]


# --- the route ---


def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(summary, "summarize", lambda *args: calls.append(args))
    return calls


def test_upload_queues_a_summary_of_the_stored_file(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "ollama_url", "http://ollama:11434")
    calls = queued(monkeypatch)

    response = upload(name="report.pdf", data=PDF, content_type="application/pdf")

    assert response.status_code == 201
    assert response.json()["summary"] is None  # it lands after the response
    [(sub, file_id, name, content_type, key, size)] = calls
    assert (sub, str(file_id), name) == ("user-1", response.json()["id"], "report.pdf")
    assert (content_type, size) == ("application/pdf", len(PDF))
    assert store.objects[key][0] == PDF


def test_a_new_version_is_summarized_again(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "ollama_url", "http://ollama:11434")
    calls = queued(monkeypatch)

    upload(data=b"one")
    upload(data=b"three")

    assert [size for *_, size in calls] == [3, 5]


def test_no_summary_without_an_ollama_url(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "ollama_url", "")
    calls = queued(monkeypatch)

    assert upload().status_code == 201
    assert calls == []
