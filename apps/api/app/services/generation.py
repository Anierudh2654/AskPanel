import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import Conversation, LLMCall, Message, Thread, ThreadMessage
from app.services.context import build_main_context, build_semantic_thread_context, build_thread_context
from app.services.llm.provider import stream_completion

logger = logging.getLogger(__name__)


async def generate(
    generation_id: str,
    conversation_id: uuid.UUID,
    assistant_id: uuid.UUID,
    queue: asyncio.Queue,
    *,
    upto_seq: int | None = None,
    thread_id: uuid.UUID | None = None,
    thread_message_id: uuid.UUID | None = None,
) -> None:
    started = time.perf_counter()
    first_token_ms = 0
    response_text = ""
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    prompt_data = {"system": "", "messages": []}
    provider_name = settings.llm_provider
    purpose = "thread" if thread_id else "main"
    embedding_input_tokens = 0
    try:
        async with SessionLocal() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None:
                raise RuntimeError("Conversation not found")
            main_messages = list((await session.scalars(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.active.is_(True),
                    Message.seq <= (upto_seq if upto_seq is not None else 2**31 - 1),
                ).order_by(Message.seq)
            )).all())
            if thread_id:
                thread = await session.get(Thread, thread_id)
                thread_history = list((await session.scalars(
                    select(ThreadMessage).where(
                        ThreadMessage.thread_id == thread_id,
                        ThreadMessage.seq <= (await session.get(ThreadMessage, thread_message_id)).seq,
                    ).order_by(ThreadMessage.seq)
                )).all())
                if settings.context_strategy == "semantic":
                    prompt, embedding_input_tokens = await build_semantic_thread_context(
                        thread,
                        conversation,
                        main_messages,
                        thread_history,
                        context_token_budget=settings.context_token_budget,
                    )
                else:
                    prompt = build_thread_context(
                        thread,
                        conversation,
                        main_messages,
                        thread_history,
                        context_strategy=settings.context_strategy,
                        context_token_budget=settings.context_token_budget,
                    )
            else:
                prompt = build_main_context(conversation, main_messages, upto_seq or 2**31 - 1)
            prompt_data = {"system": prompt.system, "messages": prompt.messages}
            async for item in stream_completion(prompt):
                if item["type"] == "delta":
                    if not first_token_ms:
                        first_token_ms = round((time.perf_counter() - started) * 1000)
                    response_text += item["text"]
                    await queue.put({"event": "delta", "data": {"text": item["text"]}})
                else:
                    usage.update(item)
            if thread_id:
                assistant = await session.get(ThreadMessage, assistant_id)
            else:
                assistant = await session.get(Message, assistant_id)
            assistant.content = response_text
            assistant.status = "complete"
            latency_ms = round((time.perf_counter() - started) * 1000)
            session.add(LLMCall(
                purpose=purpose,
                conversation_id=conversation_id,
                thread_id=thread_id,
                message_id=None if thread_id else assistant_id,
                thread_message_id=assistant_id if thread_id else None,
                provider=provider_name,
                model=settings.llm_model if provider_name != "mock" else "mock-local",
                params={
                    "temperature": settings.llm_temperature,
                    "max_tokens": settings.llm_max_tokens,
                    "context_strategy": settings.context_strategy if thread_id else "full",
                    "embedding_model": settings.embedding_model if embedding_input_tokens else None,
                    "embedding_input_tokens": embedding_input_tokens,
                },
                prompt_version=settings.prompt_version,
                assembled_prompt=prompt_data,
                response_text=response_text,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                cache_read_tokens=usage["cache_read_tokens"],
                cache_write_tokens=usage["cache_write_tokens"],
                ttft_ms=first_token_ms,
                latency_ms=latency_ms,
            ))
            await session.commit()
            await queue.put({"event": "done", "data": {
                "usage": {"input": usage["input_tokens"], "output": usage["output_tokens"]},
                "latency_ms": latency_ms,
                "ttft_ms": first_token_ms,
            }})
    except Exception as exc:
        logger.error("LLM generation failed (%s/%s): %s", provider_name, settings.llm_model, exc)
        async with SessionLocal() as session:
            assistant = await session.get(ThreadMessage if thread_id else Message, assistant_id)
            if assistant:
                assistant.status = "error"
                assistant.content = response_text
            session.add(LLMCall(
                purpose=purpose,
                conversation_id=conversation_id,
                thread_id=thread_id,
                message_id=None if thread_id else assistant_id,
                thread_message_id=assistant_id if thread_id else None,
                provider=provider_name,
                model=settings.llm_model if provider_name != "mock" else "mock-local",
                params={
                    "temperature": settings.llm_temperature,
                    "max_tokens": settings.llm_max_tokens,
                    "context_strategy": settings.context_strategy if thread_id else "full",
                    "embedding_model": settings.embedding_model if embedding_input_tokens else None,
                    "embedding_input_tokens": embedding_input_tokens,
                },
                prompt_version=settings.prompt_version,
                assembled_prompt=prompt_data,
                response_text=response_text,
                error=str(exc),
                ttft_ms=first_token_ms,
                latency_ms=round((time.perf_counter() - started) * 1000),
            ))
            await session.commit()
        await queue.put({"event": "error", "data": {"message": "Generation failed. Please try again."}})
    finally:
        await queue.put(None)
