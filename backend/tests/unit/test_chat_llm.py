import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

from app.chat.llm import LlmClient, LlmError, llm_client
from app.core.config import get_settings

LLM = "http://llm"
EXO = "http://192.168.2.35:52415"
GLM = "mlx-community/GLM-4.7-Flash-8bit"
QWEN = "mlx-community/Qwen3.6-35B-A3B-8bit"

V1_MODELS = {
    "data": [
        {"id": GLM, "object": "model", "max_input_tokens": 202752, "max_output_tokens": 1},
        {"id": QWEN, "object": "model"},
        {"id": "other", "object": "model"},
    ],
    "object": "list",
}
MODEL_INFO = {
    "data": [
        {
            "model_name": GLM,
            "litellm_params": {"model": f"openai/{GLM}", "api_base": "http://127.0.0.1:52415/v1"},
            "model_info": {},
        },
        {
            "model_name": QWEN,
            "litellm_params": {"model": f"openai/{QWEN}", "api_base": "http://127.0.0.1:52415/v1"},
        },
        {
            "model_name": "other",
            "litellm_params": {"model": "openai/other", "api_base": "http://gpu:8000/v1"},
        },
    ]
}
EXO_STATE = {
    "instances": {
        "u1": {
            "MlxRingInstance": {
                "instanceId": "u1",
                "shardAssignments": {"modelId": QWEN},
            }
        }
    }
}
EXO_MODELS = {"object": "list", "data": [{"id": QWEN, "context_length": 262144}]}


def run(coro):
    return asyncio.run(coro)


def client(handler, exo=EXO):
    return LlmClient(LLM, "k", exo, 5, transport=httpx.MockTransport(handler))


def router(seen=None, exo_up=True):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        url = str(request.url)
        if url == f"{LLM}/v1/models":
            return httpx.Response(200, json=V1_MODELS)
        if url == f"{LLM}/model/info":
            return httpx.Response(200, json=MODEL_INFO)
        if not exo_up:
            raise httpx.ConnectError("down")
        if url == f"{EXO}/state":
            return httpx.Response(200, json=EXO_STATE)
        return httpx.Response(200, json=EXO_MODELS)

    return handler


def test_models_joins_litellm_and_exo_loaded_state():
    seen = []
    models = run(client(router(seen)).models())
    assert [(m.id, m.loaded, m.context_length) for m in models] == [
        (GLM, False, 202752),  # on exo, not loaded, LiteLLM context
        (QWEN, True, 262144),  # loaded, context from exo
        ("other", None, 32768),  # api_base on another port: unknown, default context
    ]
    assert all(r.headers["authorization"] == "Bearer k" for r in seen if "llm" in r.url.host)


def test_models_loaded_is_none_when_exo_is_down():
    models = run(client(router(exo_up=False)).models())
    assert [m.loaded for m in models] == [None, None, None]
    assert models[0].context_length == 202752


def test_models_loaded_is_none_without_exo_url():
    assert {m.loaded for m in run(client(router(), exo="").models())} == {None}


def test_models_is_cached_for_ten_seconds():
    seen = []
    c = client(router(seen))
    run(c.models())
    n = len(seen)
    run(c.models())
    assert len(seen) == n


def sse(*lines):
    return httpx.Response(200, content="\n".join(lines) + "\n")


def chunk(**delta):
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta}]})


def collect(c, model=GLM):
    async def go():
        return [e async for e in c.stream(model, [{"role": "user", "content": "hi"}])]

    return run(go())


def test_stream_splits_reasoning_content_and_usage():
    usage = {"prompt_tokens": 16, "completion_tokens": 400, "total_tokens": 416}
    resp = sse(
        ": keep-alive",
        chunk(role="assistant", content=None, reasoning_content="Here"),
        chunk(content="Hi"),
        "data: " + json.dumps({"choices": [], "usage": usage}),
        "data: [DONE]",
    )
    events = collect(client(lambda r: resp))
    assert events == [("reasoning", "Here"), ("content", "Hi"), ("usage", usage)]


def test_stream_never_sends_max_tokens():
    seen = []

    def handler(request):
        seen.append(request)
        return sse("data: [DONE]")

    collect(client(handler))
    body = json.loads(seen[0].content)
    assert "max_tokens" not in body
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert str(seen[0].url) == f"{LLM}/v1/chat/completions"


def test_stream_splits_inline_think_tags_across_deltas():
    resp = sse(chunk(content="<thi"), chunk(content="nk>plan</think>Hi"), "data: [DONE]")
    events = collect(client(lambda r: resp))
    text = lambda kind: "".join(e[1] for e in events if e[0] == kind)  # noqa: E731
    assert text("reasoning") == "plan"
    assert text("content") == "Hi"


def test_stream_reports_prefill_progress():
    p = {"PrefillProgressChunk": {"model": GLM, "processed_tokens": 14, "total_tokens": 15}}
    resp = sse(": prefill_progress " + json.dumps(p), "data: [DONE]")
    assert collect(client(lambda r: resp)) == [("progress", 14, 15)]


def test_no_instance_found_becomes_not_loaded_error():
    resp = httpx.Response(404, text=f"No instance found for model {GLM}")
    with pytest.raises(LlmError, match="is not loaded on the cluster") as exc:
        collect(client(lambda r: resp))
    assert str(exc.value) == f"{GLM} is not loaded on the cluster"


def test_cooldown_becomes_not_loaded_error():
    resp = httpx.Response(429, text='{"error":"all_deployments_in_cooldown"}')
    with pytest.raises(LlmError, match="is not loaded on the cluster"):
        collect(client(lambda r: resp))


def test_other_failures_become_generic_error():
    with pytest.raises(LlmError, match="The model did not answer"):
        collect(client(lambda r: httpx.Response(500, text="boom")))

    def down(request):
        raise httpx.ConnectError("down")

    with pytest.raises(LlmError, match="The model did not answer"):
        collect(client(down))
    with pytest.raises(LlmError, match="The model did not answer"):
        collect(client(lambda r: sse("data: {not json")))


def test_complete_strips_thinking():
    body = {"choices": [{"message": {"content": "<think>hmm</think>\n Title here \n"}}]}
    c = client(lambda r: httpx.Response(200, json=body))
    assert run(c.complete(GLM, [{"role": "user", "content": "x"}])) == "Title here"


def test_llm_client_dependency_is_503_when_unconfigured(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_url", "")
    with pytest.raises(HTTPException) as exc:
        llm_client()
    assert exc.value.status_code == 503
    assert exc.value.detail == "Chat is not configured"


def test_error_body_is_never_logged(caplog):
    body = {
        "error": {"type": "auth_error", "code": "401", "message": "key k, prompt SECRET-PROMPT"}
    }
    with caplog.at_level("WARNING"), pytest.raises(LlmError) as exc:
        collect(client(lambda r: httpx.Response(401, json=body)))
    assert str(exc.value) == "The model did not answer"
    assert "auth_error/401" in caplog.text
    assert "SECRET-PROMPT" not in caplog.text
    assert "key k" not in caplog.text


def test_non_object_chunk_becomes_generic_error():
    with pytest.raises(LlmError, match="The model did not answer"):
        collect(client(lambda r: sse("data: []")))


def test_empty_api_key_sends_no_authorization_header():
    seen = []
    c = LlmClient(LLM, "", EXO, 5, transport=httpx.MockTransport(router(seen)))
    run(c.models())

    def handler(request):
        seen.append(request)
        return sse("data: [DONE]")

    c.transport = httpx.MockTransport(handler)
    collect(c)
    assert seen
    assert all("authorization" not in r.headers for r in seen)


def test_malformed_prefill_progress_line_is_skipped():
    resp = sse(": prefill_progress {broken", ": prefill_progress []", chunk(content="Hi"))
    assert collect(client(lambda r: resp)) == [("content", "Hi")]


def test_in_stream_error_chunk_is_an_upstream_failure(caplog):
    secret = {"error": {"type": "x", "code": "500", "message": "SECRET-PROMPT"}}
    with caplog.at_level("WARNING"), pytest.raises(LlmError, match="The model did not answer"):
        collect(client(lambda r: sse(chunk(content="par"), "data: " + json.dumps(secret))))
    assert "SECRET-PROMPT" not in caplog.text

    gone = {"error": f"No instance found for model {GLM}"}
    with pytest.raises(LlmError, match="is not loaded on the cluster"):
        collect(client(lambda r: sse("data: " + json.dumps(gone))))


def test_malformed_exo_url_yields_unknown_loaded_state():
    models = run(client(router(), exo="http://exo:notaport").models())
    assert [m.loaded for m in models] == [None, None, None]
