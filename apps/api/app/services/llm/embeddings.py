from collections.abc import Sequence

import httpx

from app.config import settings


async def embed_texts(texts: Sequence[str]) -> tuple[list[list[float]], int]:
    """Embed a small batch of texts and return vectors plus provider input-token usage."""
    if settings.llm_provider == "nebius":
        api_key = settings.nebius_api_key
        base_url = settings.nebius_base_url
        key_name = "NEBIUS_API_KEY"
    elif settings.llm_provider == "openai":
        api_key = settings.openai_api_key
        base_url = settings.openai_base_url or "https://api.openai.com/v1/"
        key_name = "OPENAI_API_KEY"
    else:
        raise RuntimeError("Semantic context requires an embeddings-capable Nebius or OpenAI provider")
    if not api_key:
        raise RuntimeError(f"Set {key_name} to use semantic context embeddings")
    if not texts:
        return [], 0

    request = {
        "model": settings.embedding_model,
        "input": list(texts),
        "dimensions": settings.embedding_dimensions,
        "encoding_format": "float",
    }
    endpoint = f"{base_url.rstrip('/')}/embeddings"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=request,
        )
    if response.is_error:
        detail = response.text[:1000] or response.reason_phrase
        raise RuntimeError(f"Embedding API returned HTTP {response.status_code}: {detail}")
    payload = response.json()
    data = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
    if len(data) != len(texts):
        raise RuntimeError(f"Embedding API returned {len(data)} vectors for {len(texts)} inputs")
    usage = payload.get("usage") or {}
    return [item["embedding"] for item in data], int(usage.get("prompt_tokens", 0) or 0)
