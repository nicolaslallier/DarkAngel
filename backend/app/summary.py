"""The AI summary of an upload, asked of Ollama after the response is sent.

Best effort by design: whatever goes wrong here, the upload has already
succeeded, and the worst outcome is a file with no summary.

What the model is shown depends on the file:
- text (txt, md, csv, code...): the first TEXT_CHARS characters;
- pdf, docx, xlsx: their text, extracted here;
- images: the image, shrunk to a JPEG by ffmpeg;
- videos: FRAMES stills grabbed by ffmpeg. No sound: Ollama cannot hear.
"""

import base64
import codecs
import io
import json
import logging
import re
import subprocess
import urllib.request
import uuid
import zipfile
from collections.abc import Sequence
from datetime import timedelta

import pypdf
from defusedxml import ElementTree

from app.core.config import get_settings
from app.core.db import session_factory
from app.repositories.files import FileRepository

log = logging.getLogger(__name__)

# About 8k tokens: enough to say what a document is about.
TEXT_CHARS = 32 * 1024
# Documents and images are read whole, into memory; past this, no summary.
MAX_READ_BYTES = 50 * 1024 * 1024
# A zip member bigger than this uncompressed is not parsed (zip bomb guard).
# The XML itself goes through defusedxml: no DTDs, no entities, whatever
# the expat version underneath.
MAX_MEMBER_BYTES = 200 * 1024 * 1024
# Where in a video the stills are taken, as fractions of its duration.
FRAMES = (0.1, 0.35, 0.6, 0.85)
# FilePatch caps description at 2000; the summary is shown in the same table.
MAX_SUMMARY = 2000
# Reasoning models prefix their answer with their thinking.
_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)

VIDEO = {"mp4", "mov", "m4v", "mkv", "webm", "avi", "mpg", "mpeg", "wmv", "3gp"}
IMAGE = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "heic", "heif"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def text_of(head: bytes) -> str | None:
    """The head as text, or None for anything binary. final=False, because the
    cut may split a multi-byte character, which is not a sign of binary data."""
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except UnicodeDecodeError:
        return None
    return text if text.strip() and "\x00" not in text else None


def pdf_text(data: bytes) -> str:
    parts: list[str] = []
    for page in pypdf.PdfReader(io.BytesIO(data)).pages:
        parts.append(page.extract_text() or "")
        if sum(map(len, parts)) >= TEXT_CHARS:
            break
    return "\n".join(parts)


def _member(archive: zipfile.ZipFile, name: str):
    if archive.getinfo(name).file_size > MAX_MEMBER_BYTES:
        raise ValueError(f"{name} is too big to parse")
    return ElementTree.fromstring(archive.read(name))


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        body = _member(archive, "word/document.xml")
    return "\n".join(
        "".join(t.text or "" for t in paragraph.iter(f"{_W}t")) for paragraph in body.iter(f"{_W}p")
    )


def xlsx_text(data: bytes) -> str:
    """Every sheet as tab-separated rows. Cells hold either an index into the
    shared strings, an inline string, or a raw value (numbers, formulas'
    last results)."""
    lines: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            table = _member(archive, "xl/sharedStrings.xml")
            shared = ["".join(t.text or "" for t in si.iter(f"{_S}t")) for si in table]
        sheets = [n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml")]
        for sheet in sorted(sheets):
            lines.append(f"## {sheet.rsplit('/', 1)[1].removesuffix('.xml')}")
            for row in _member(archive, sheet).iter(f"{_S}row"):
                cells = []
                for cell in row.iter(f"{_S}c"):
                    value = cell.find(f"{_S}v")
                    if cell.get("t") == "inlineStr":
                        cells.append("".join(t.text or "" for t in cell.iter(f"{_S}t")))
                    elif value is None or value.text is None:
                        cells.append("")
                    elif cell.get("t") == "s":
                        cells.append(shared[int(value.text)])
                    else:
                        cells.append(value.text)
                lines.append("\t".join(cells))
                if sum(map(len, lines)) >= TEXT_CHARS:
                    return "\n".join(lines)
    return "\n".join(lines)


def _jpeg(source: str, *, data: bytes | None = None, at: float | None = None) -> bytes:
    """One still as a JPEG at most 1024 px wide: the whole of an image, or the
    frame of a video `at` seconds in. ffmpeg reads the many formats Ollama
    does not (webp, heic, tiff...), and a phone photo shrinks from megabytes."""
    seek = ["-ss", f"{at:.2f}"] if at is not None else []
    command = [
        "ffmpeg", "-v", "error", *seek, "-i", source, "-frames:v", "1",
        "-vf", "scale='min(1024,iw)':-2", "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1",
    ]  # fmt: skip
    done = subprocess.run(command, input=data, capture_output=True, timeout=120, check=True)
    return done.stdout


def _duration(url: str) -> float | None:
    command = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0"]
    done = subprocess.run([*command, url], capture_output=True, timeout=60, check=True)
    try:
        return float(done.stdout)
    except ValueError:
        return None  # a live-style stream with no duration


def _read(object_key: str, limit: int) -> bytes:
    # Imported here: the route module imports this one.
    from app.api.routes.files import s3_client

    obj = s3_client().get_object(get_settings().s3_bucket, object_key)
    try:
        data = bytearray()
        for chunk in obj.stream(64 * 1024):
            data += chunk
            if len(data) >= limit:
                break
        return bytes(data[:limit])
    finally:
        obj.close()
        obj.release_conn()


def _video_url(object_key: str) -> str:
    """ffmpeg seeks with HTTP range requests, so a 5 GiB video is never
    downloaded whole: only the few megabytes around each still."""
    from app.api.routes.files import s3_client

    bucket = get_settings().s3_bucket
    return s3_client().presigned_get_object(bucket, object_key, expires=timedelta(hours=1))


def _document_prompt(name: str, text: str) -> str:
    return (
        f"Summarize the file below, named {name!r}, in three to five sentences: "
        "what it is and what it says. Answer in the language of the file, "
        "with the summary only.\n\n"
        f"{text[:TEXT_CHARS]}"
    )


def material(
    name: str, content_type: str, object_key: str, size: int
) -> tuple[str, list[bytes]] | None:
    """The prompt and the images to show the model, or None: not a kind of
    file it can say anything about, or too big to read."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    if content_type.startswith("video/") or ext in VIDEO:
        url = _video_url(object_key)
        duration = _duration(url)
        stills = [_jpeg(url, at=duration * f if duration else None) for f in FRAMES]
        stills = [s for s in stills if s]
        prompt = (
            f"These are {len(stills)} stills taken in order through a video named {name!r}. "
            "Describe in three to five sentences what the video shows. Summary only."
        )
        return (prompt, stills) if stills else None

    if size > MAX_READ_BYTES:
        return None

    if content_type.startswith("image/") or ext in IMAGE:
        still = _jpeg("pipe:0", data=_read(object_key, MAX_READ_BYTES))
        prompt = (
            f"Describe the image named {name!r} in three to five sentences: what it "
            "shows, and any text written in it. Summary only."
        )
        return prompt, [still]

    extract = {"pdf": pdf_text, "docx": docx_text, "xlsx": xlsx_text}.get(ext)
    if extract is not None:
        text = extract(_read(object_key, MAX_READ_BYTES))
    else:
        # Four bytes a character at worst, so the head always holds TEXT_CHARS.
        text = text_of(_read(object_key, TEXT_CHARS * 4))
    return (_document_prompt(name, text), []) if text and text.strip() else None


def ask_ollama(prompt: str, images: Sequence[bytes] = ()) -> str:
    settings = get_settings()
    body = {"model": settings.ollama_model, "prompt": prompt, "stream": False}
    if images:
        body["images"] = [base64.b64encode(image).decode() for image in images]
    request = urllib.request.Request(
        f"{settings.ollama_url.rstrip('/')}/api/generate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    # A big model on a Mac takes its time; this only ever holds a background task.
    with urllib.request.urlopen(request, timeout=600) as response:
        reply = json.load(response)["response"]
    return _THINKING.sub("", reply).strip()[:MAX_SUMMARY]


def summarize(
    owner_sub: str, file_id: uuid.UUID, name: str, content_type: str, object_key: str, size: int
) -> None:
    """Runs as a BackgroundTask: the request's session is closed by now, so
    this opens its own. A new version re-runs it, so a stale summary of the
    previous bytes is overwritten -- with None if the new ones say nothing."""
    summary = None
    try:
        if (found := material(name, content_type, object_key, size)) is not None:
            summary = ask_ollama(*found) or None
    except Exception:
        log.exception("summary of file %s failed", file_id)
    with session_factory()() as db:
        FileRepository(db).set_summary(owner_sub, file_id, summary)
