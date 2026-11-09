import asyncio
from collections.abc import AsyncIterator
import json

import anthropic
import httpx

from app.config import settings
from app.services.context import Prompt


async def stream_completion(prompt: Prompt) -> AsyncIterator[dict]:
    if settings.llm_provider == "mock":
        answer = (
            "This is a local development response. Connect a server-side model provider in "
            "`apps/api/.env` for study-quality answers.\n\n"
            f"I received your message: {prompt.messages[-1]['content'] if prompt.messages else ''}"
        )
        for start in range(0, len(answer), 18):
            await asyncio.sleep(0.025)
            yield {"type": "delta", "text": answer[start : start + 18]}
        yield {"type": "usage", "input_tokens": max(1, len(str(prompt)) // 4), "output_tokens": max(1, len(answer) // 4)}
        return

    if settings.llm_provider in {"nebius", "openai"}:
        if settings.llm_provider == "nebius":
            api_key = settings.nebius_api_key
            base_url = settings.nebius_base_url
            key_name = "NEBIUS_API_KEY"
        else:
            api_key = settings.openai_api_key
            base_url = settings.openai_base_url or None
            key_name = "OPENAI_API_KEY"
        if not api_key:
            raise RuntimeError(f"Set {key_name} in apps/api/.env to use {settings.llm_provider}")

        input_tokens = output_tokens = 0
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        request = {
            "model": settings.llm_model,
            "messages": [{"role": "system", "content": prompt.system}, *prompt.messages],
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=request,
            ) as response:
                if response.is_error:
                    error_body = (await response.aread()).decode("utf-8", errors="replace")
                    detail = error_body[:1000] or response.reason_phrase
                    raise RuntimeError(f"Nebius API returned HTTP {response.status_code}: {detail}")
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    choices = chunk.get("choices", [])
                    if choices:
                        text = choices[0].get("delta", {}).get("content")
                        if text:
                            yield {"type": "delta", "text": text}
                    usage = chunk.get("usage")
                    if usage:
                        input_tokens = usage.get("prompt_tokens", 0)
                        output_tokens = usage.get("completion_tokens", 0)
        yield {"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens}
        return

    if settings.llm_provider != "anthropic" or not settings.anthropic_api_key:
        raise RuntimeError("Set ANTHROPIC_API_KEY and LLM_PROVIDER=anthropic, or use mock/nebius/openai")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    async with client.messages.stream(
        model=settings.llm_model,
        system=prompt.system,
        messages=prompt.messages,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    ) as stream:
        async for text in stream.text_stream:
            yield {"type": "delta", "text": text}
        response = await stream.get_final_message()
        yield {
            "type": "usage",
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            "cache_write_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        }
