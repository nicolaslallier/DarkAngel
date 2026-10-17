from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.chat import Message

# ponytail: rough chars-per-token estimate, use a real tokenizer if it misjudges
CHARS_PER_TOKEN = 4
# Leave a quarter of the window for the answer, reasoning included.
CONTEXT_SHARE = 0.75


def system_prompt(now: datetime) -> str:
    local = now.astimezone(ZoneInfo("America/Toronto"))
    return (
        "You are a helpful, concise assistant. "
        "Answer in the language the user writes in. "
        f"The current date and time is {local:%A} {local.day} {local:%B %Y}, "
        f"{local:%H:%M} (America/Toronto)."
    )


def build_context(history: Sequence[Message], context_length: int, now: datetime) -> list[dict]:
    system = system_prompt(now)
    msgs = [
        {"role": m.role, "content": m.content}
        for m in history
        if m.status != "error" and (m.role != "assistant" or m.content)
    ]
    budget = int(context_length * CONTEXT_SHARE) * CHARS_PER_TOKEN
    total = len(system) + sum(len(m["content"]) for m in msgs)
    while len(msgs) > 1 and total > budget:
        total -= len(msgs.pop(0)["content"])
    return [{"role": "system", "content": system}, *msgs]


def fallback_title(text: str) -> str:
    return " ".join(text.split())[:60] or "New chat"
