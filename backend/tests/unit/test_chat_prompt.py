from datetime import UTC, datetime

from app.chat.prompt import build_context, fallback_title, system_prompt
from app.models.chat import Message

NOW = datetime(2026, 10, 17, 11, 42, tzinfo=UTC)


def _msg(role: str, content: str, status: str = "complete", reasoning: str | None = None):
    return Message(role=role, content=content, status=status, reasoning=reasoning)


def test_system_prompt_has_toronto_date():
    text = system_prompt(NOW)
    assert "17 October 2026" in text
    assert "07:42" in text


def test_context_skips_errors_and_never_sends_reasoning():
    history = [
        _msg("user", "hi"),
        _msg("assistant", "partial", status="error"),
        _msg("assistant", ""),
        _msg("user", "again"),
        _msg("assistant", "hello", reasoning="SECRET THOUGHTS"),
    ]
    ctx = build_context(history, 10_000, NOW)
    assert [m["role"] for m in ctx] == ["system", "user", "user", "assistant"]
    assert ctx[-1] == {"role": "assistant", "content": "hello"}
    assert "SECRET THOUGHTS" not in str(ctx)


def test_context_drops_oldest_first_and_keeps_newest():
    sys_len = len(system_prompt(NOW))
    assert sys_len < 200
    pad = 200 - sys_len
    history = [_msg("user", "a" * pad), _msg("assistant", "b" * 10), _msg("user", "c" * 5)]
    ctx = build_context(history, 50, NOW)
    assert [m["content"][0] for m in ctx[1:]] == ["b", "c"]
    # newest survives even when it alone busts the budget
    ctx = build_context([_msg("user", "z" * 1000)], 50, NOW)
    assert len(ctx) == 2 and ctx[1]["content"] == "z" * 1000


def test_fallback_title_collapses_and_truncates():
    assert fallback_title("  hello \n\n  world ") == "hello world"
    assert fallback_title("x" * 100) == "x" * 60
    assert fallback_title("  \n ") == "New chat"
