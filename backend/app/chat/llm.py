from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from app.core.config import get_settings

log = logging.getLogger(__name__)

# ("reasoning", text) | ("content", text) | ("usage", dict) | ("progress", done, total)
Event = tuple[str, ...]

_DEFAULT_CONTEXT = 32768
_CACHE_SECONDS = 10
_OPEN, _CLOSE = "<think>", "</think>"
_NOT_LOADED_MARKERS = ("No instance found", "all_deployments_in_cooldown")


class LlmError(Exception):
    """Any failure talking to the model. The message is safe to show."""


@dataclass(frozen=True)
class ModelInfo:
    id: str
    loaded: bool | None
    context_length: int


class _ThinkSplitter:
    """Routes content deltas to reasoning/content, honouring a leading <think>…</think>."""

    def __init__(self) -> None:
        self.state = "start"  # start -> thinking -> plain, or start -> plain
        self.buf = ""

    def feed(self, text: str) -> list[Event]:
        self.buf += text
        out: list[Event] = []
        if self.state == "start":
            if _OPEN.startswith(self.buf) and len(self.buf) < len(_OPEN):
                return out
            if self.buf.startswith(_OPEN):
                self.buf = self.buf[len(_OPEN) :]
                self.state = "thinking"
            else:
                self.state = "plain"
        if self.state == "thinking":
            end = self.buf.find(_CLOSE)
            if end >= 0:
                if end:
                    out.append(("reasoning", self.buf[:end]))
                self.buf = self.buf[end + len(_CLOSE) :].lstrip()
                self.state = "plain"
            else:
                # Keep a tail that could be the start of "</think>".
                keep = len(_CLOSE) - 1
                if len(self.buf) > keep:
                    out.append(("reasoning", self.buf[:-keep]))
                    self.buf = self.buf[-keep:]
                return out
        if self.buf:
            out.append(("content", self.buf))
            self.buf = ""
        return out

    def flush(self) -> list[Event]:
        kind = "reasoning" if self.state == "thinking" else "content"
        out: list[Event] = [(kind, self.buf)] if self.buf else []
        self.buf = ""
        return out


class LlmClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        exo_url: str,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.exo_url = exo_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self._cache: tuple[float, list[ModelInfo]] | None = None

    def _http(self, timeout: httpx.Timeout | float) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)

    @property
    def _auth(self) -> dict[str, str]:
        # An empty key would send "Bearer " with nothing after it, which httpx rejects.
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    async def models(self) -> list[ModelInfo]:
        if self._cache and time.monotonic() - self._cache[0] < _CACHE_SECONDS:
            return self._cache[1]
        try:
            async with self._http(10) as http:
                listed = await http.get(f"{self.base_url}/v1/models", headers=self._auth)
                info = await http.get(f"{self.base_url}/model/info", headers=self._auth)
                listed.raise_for_status()
                info.raise_for_status()
                context = {m["id"]: m.get("max_input_tokens") for m in listed.json()["data"]}
                params = {d["model_name"]: d["litellm_params"] for d in info.json()["data"]}
                loaded, exo_context = await self._exo(http)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("llm model listing failed: %s", type(exc).__name__)
            raise LlmError("The model did not answer") from None
        exo_port = _port(self.exo_url)
        result = []
        for model_id, max_input in context.items():
            p = params.get(model_id, {})
            exo_id = p.get("model", model_id).removeprefix("openai/")
            is_exo = (
                loaded is not None
                and exo_port is not None
                and _port(p.get("api_base", "")) == exo_port
            )
            result.append(
                ModelInfo(
                    id=model_id,
                    loaded=(exo_id in loaded) if is_exo else None,
                    context_length=max_input or exo_context.get(exo_id) or _DEFAULT_CONTEXT,
                )
            )
        self._cache = (time.monotonic(), result)
        return result

    async def _exo(self, http: httpx.AsyncClient) -> tuple[set[str] | None, dict[str, int]]:
        """Loaded model ids and context lengths from exo; (None, {}) when exo is unusable."""
        if not self.exo_url:
            return None, {}
        try:
            state = await http.get(f"{self.exo_url}/state")
            state.raise_for_status()
            models = await http.get(f"{self.exo_url}/v1/models")
            models.raise_for_status()
            loaded = {
                shard["shardAssignments"]["modelId"]
                for instance in state.json()["instances"].values()
                for shard in instance.values()
            }
            return loaded, {m["id"]: m["context_length"] for m in models.json()["data"]}
        except (
            httpx.HTTPError,
            httpx.InvalidURL,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
        ) as exc:
            log.warning("exo status failed: %s", type(exc).__name__)
            return None, {}

    def _payload(self, model: str, messages: list[dict], stream: bool) -> dict:
        # No max_tokens: the router's own limit applies.
        body: dict = {"model": model, "messages": messages, "stream": stream}
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    def _fail(self, model: str, status: int, body: str) -> LlmError:
        # Never log the body: it can echo the prompt or the key.
        detail = ""
        try:
            err = json.loads(body)["error"]
            detail = f" ({err.get('type')}/{err.get('code')})"
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
        log.warning("llm answered %s%s", status, detail)
        if any(marker in body for marker in _NOT_LOADED_MARKERS):
            return LlmError(f"{model} is not loaded on the cluster")
        return LlmError("The model did not answer")

    async def stream(  # noqa: C901 -- one SSE parse loop, one branch per line kind
        self, model: str, messages: list[dict]
    ) -> AsyncIterator[Event]:
        timeout = httpx.Timeout(self.timeout, connect=5)
        splitter = _ThinkSplitter()
        try:
            async with (
                self._http(timeout) as http,
                http.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    headers=self._auth,
                    json=self._payload(model, messages, True),
                ) as response,
            ):
                if response.status_code >= 400:
                    body = (await response.aread()).decode(errors="replace")
                    raise self._fail(model, response.status_code, body)
                async for line in response.aiter_lines():
                    if line.startswith(": prefill_progress"):
                        try:
                            chunk = json.loads(line.removeprefix(": prefill_progress"))
                            p = chunk["PrefillProgressChunk"]
                            progress = ("progress", p["processed_tokens"], p["total_tokens"])
                        except (ValueError, KeyError, TypeError):
                            continue  # a progress hint only: never worth losing the reply
                        yield progress
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if "error" in chunk:
                        raise self._fail(model, response.status_code, data)
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("reasoning_content"):
                            yield ("reasoning", delta["reasoning_content"])
                        if delta.get("content"):
                            for event in splitter.feed(delta["content"]):
                                yield event
                    if chunk.get("usage"):
                        yield ("usage", chunk["usage"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            log.warning("llm stream failed: %s", type(exc).__name__)
            raise LlmError("The model did not answer") from None
        for event in splitter.flush():
            yield event

    async def complete(self, model: str, messages: list[dict]) -> str:
        try:
            async with self._http(httpx.Timeout(self.timeout, connect=5)) as http:
                response = await http.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers=self._auth,
                    json=self._payload(model, messages, False),
                )
            if response.status_code >= 400:
                raise self._fail(model, response.status_code, response.text)
            text = response.json()["choices"][0]["message"]["content"] or ""
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
        ) as exc:
            log.warning("llm completion failed: %s", type(exc).__name__)
            raise LlmError("The model did not answer") from None
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _port(url: str) -> int | None:
    try:
        return urlparse(url).port
    except ValueError:
        return None


@lru_cache
def _build(url: str, key: str, exo_url: str, timeout: float) -> LlmClient:
    return LlmClient(url, key, exo_url, timeout)


def llm_client() -> LlmClient:
    s = get_settings()
    if not s.llm_url:
        raise HTTPException(503, "Chat is not configured")
    return _build(s.llm_url, s.llm_api_key.get_secret_value(), s.exo_url, s.llm_timeout_seconds)
