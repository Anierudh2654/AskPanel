import json

import httpx
import pytest

from app.config import settings
from app.services.context import Prompt
from app.services.llm import provider


@pytest.mark.asyncio
async def test_nebius_openai_compatible_stream_uses_token_and_reports_usage(monkeypatch):
    captured = {}
    chunks = [
        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1, "model": "test-model", "choices": [{"index": 0, "delta": {"content": "Hi"}, "finish_reason": None}]},
        {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1, "model": "test-model", "choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}},
    ]
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        provider.httpx,
        "AsyncClient",
        lambda **kwargs: original_async_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(settings, "llm_provider", "nebius")
    monkeypatch.setattr(settings, "nebius_api_key", "test-nebius-token")
    monkeypatch.setattr(settings, "nebius_base_url", "https://api.studio.nebius.ai/v1/")
    monkeypatch.setattr(settings, "llm_model", "test-model")

    result = [item async for item in provider.stream_completion(Prompt(
        system="system rules",
        messages=[{"role": "user", "content": "hello"}],
    ))]

    assert captured["url"] == "https://api.studio.nebius.ai/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-nebius-token"
    assert captured["payload"]["model"] == "test-model"
    assert captured["payload"]["messages"] == [
        {"role": "system", "content": "system rules"},
        {"role": "user", "content": "hello"},
    ]
    assert result == [
        {"type": "delta", "text": "Hi"},
        {"type": "usage", "input_tokens": 9, "output_tokens": 1},
    ]


@pytest.mark.asyncio
async def test_nebius_requires_api_token(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "nebius")
    monkeypatch.setattr(settings, "nebius_api_key", "")
    with pytest.raises(RuntimeError, match="NEBIUS_API_KEY"):
        await anext(provider.stream_completion(Prompt(system="", messages=[])))
