"""The AI summary of an upload, asked of Ollama after the response is sent.

Best effort by design: whatever goes wrong here, the upload has already
succeeded, and the worst outcome is a file with no summary.
"""

import codecs
import json
import logging
import re
import urllib.request
import uuid

from app.core.config import get_settings
from app.core.db import session_factory
from app.repositories.files import FileRepository

log = logging.getLogger(__name__)

# Only the head of the file is read: about 8k tokens, enough to say what a
# document is about, and a 5 GiB upload costs no more than a small one.
# ponytail: head only, text only; add pypdf for PDFs if they need summaries
HEAD_BYTES = 32 * 1024
# FilePatch caps description at 2000; the summary is shown in the same table.
MAX_SUMMARY = 2000
# Reasoning models (qwen3) prefix their answer with their thinking.
_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)


def text_of(head: bytes) -> str | None:
    """The head as text, or None for anything binary. final=False, because the
    HEAD_BYTES cut may split a multi-byte character, which is not a sign of
    binary data."""
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except UnicodeDecodeError:
        return None
    return text if text.strip() and "\x00" not in text else None


def ask_ollama(name: str, text: str) -> str:
    settings = get_settings()
    prompt = (
        f"Summarize the file below, named {name!r}, in three to five sentences: "
        "what it is and what it says. Answer in the language of the file, "
        "with the summary only.\n\n"
        f"{text}"
    )
    body = {"model": settings.ollama_model, "prompt": prompt, "stream": False}
    request = urllib.request.Request(
        f"{settings.ollama_url.rstrip('/')}/api/generate",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    # A 27B model on a Mac takes its time; this only ever holds a background task.
    with urllib.request.urlopen(request, timeout=300) as response:
        reply = json.load(response)["response"]
    return _THINKING.sub("", reply).strip()[:MAX_SUMMARY]


def summarize(owner_sub: str, file_id: uuid.UUID, name: str, head: bytes) -> None:
    """Runs as a BackgroundTask: the request's session is closed by now, so
    this opens its own. A new version re-runs it, so a stale summary of the
    previous bytes is overwritten -- with None if the new ones are not text."""
    summary = None
    if (text := text_of(head)) is not None:
        try:
            summary = ask_ollama(name, text) or None
        except Exception:
            log.exception("summary of file %s failed", file_id)
    with session_factory()() as db:
        FileRepository(db).set_summary(owner_sub, file_id, summary)
