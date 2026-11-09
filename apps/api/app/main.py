import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import Base, engine, get_session
from app.models import Conversation, Event, Message, MessageRevision, Thread, ThreadMessage, User
from app.services.generation import generate

@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="AskPanel API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ParticipantLogin(BaseModel):
    code: str = Field(min_length=1, max_length=120)


class DeviceLogin(BaseModel):
    device_id: uuid.UUID


class AdminLogin(BaseModel):
    password: str


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    condition: str = Field(default="A", pattern="^[ABC]$")


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    quote: dict | None = None


class MessageEdit(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class Anchor(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    prefix: str = Field(default="", max_length=40)
    suffix: str = Field(default="", max_length=40)


class ThreadCreate(BaseModel):
    message_id: uuid.UUID
    anchor: Anchor
    content: str = Field(min_length=1, max_length=20000)


class EventInput(BaseModel):
    type: str = Field(min_length=1, max_length=80)
    payload: dict = Field(default_factory=dict)
    client_ts: datetime | None = None
    conversation_id: uuid.UUID | None = None
    thread_id: uuid.UUID | None = None


class EventBatch(BaseModel):
    events: list[EventInput] = Field(min_length=1, max_length=500)


def issue_token(user_id: uuid.UUID, role: str) -> str:
    return jwt.encode({"sub": str(user_id), "role": role}, settings.jwt_secret, algorithm="HS256")


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    token = request.cookies.get("askpanel_session")
    if not token:
        raise HTTPException(status_code=401, detail="Login required")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        user_id = uuid.UUID(payload["sub"])
    except (JWTError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid session") from None
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid session")
    return user


def user_payload(user: User) -> dict:
    return {"id": str(user.id), "role": user.role, "code": user.code, "is_synthetic": user.is_synthetic}


def message_payload(message: Message | ThreadMessage) -> dict:
    return {
        "id": str(message.id), "seq": message.seq, "role": message.role,
        "content": message.content, "quote": getattr(message, "quote", None),
        "status": message.status, "created_at": message.created_at.isoformat() if message.created_at else None,
    }


async def owned_conversation(session: AsyncSession, user: User, conversation_id: uuid.UUID) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.deleted_at is not None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


async def stream_response(queue: asyncio.Queue, initial: list[tuple[str, dict]] = []):
    async def events():
        for name, data in initial:
            import json
            yield f"event: {name}\ndata: {json.dumps(data)}\n\n"
        while True:
            item = await queue.get()
            if item is None:
                break
            import json
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"
    })


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/auth/participant")
async def participant_login(body: ParticipantLogin, response: Response, session: AsyncSession = Depends(get_session)):
    code = body.code.strip()
    user = await session.scalar(select(User).where(User.code == code))
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid study code")
    response.set_cookie("askpanel_session", issue_token(user.id, user.role), httponly=True, samesite="lax")
    return user_payload(user)


@app.post("/api/auth/device")
async def device_login(body: DeviceLogin, response: Response, session: AsyncSession = Depends(get_session)):
    device_code = f"device-{body.device_id}"
    user = await session.scalar(select(User).where(User.code == device_code))
    if user is None:
        user = User(code=device_code, role="participant", is_synthetic=False)
        session.add(user)
        try:
            await session.commit()
            await session.refresh(user)
        except IntegrityError:
            await session.rollback()
            user = await session.scalar(select(User).where(User.code == device_code))
            if user is None:
                raise
    response.set_cookie("askpanel_session", issue_token(user.id, user.role), httponly=True, samesite="lax")
    return user_payload(user)


@app.post("/api/auth/admin")
async def admin_login(body: AdminLogin, response: Response, session: AsyncSession = Depends(get_session)):
    if not settings.admin_password or body.password != settings.admin_password:
        raise HTTPException(status_code=401, detail="Invalid admin password")
    user = await session.scalar(select(User).where(User.code == "__admin__"))
    if user is None:
        user = User(code="__admin__", role="admin")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    response.set_cookie("askpanel_session", issue_token(user.id, user.role), httponly=True, samesite="lax")
    return user_payload(user)


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie("askpanel_session")
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(user: User = Depends(current_user)):
    return user_payload(user)


@app.get("/api/conversations")
async def list_conversations(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Conversation).where(
        Conversation.user_id == user.id, Conversation.deleted_at.is_(None)
    ).order_by(Conversation.updated_at.desc()))).all()
    return [{"id": str(row.id), "title": row.title, "condition": row.condition, "updated_at": row.updated_at.isoformat()} for row in rows]


@app.post("/api/conversations")
async def create_conversation(body: ConversationCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    conversation = Conversation(user_id=user.id, title=body.title or "New conversation", condition=body.condition)
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return {"id": str(conversation.id), "title": conversation.title, "condition": conversation.condition}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    conversation = await owned_conversation(session, user, conversation_id)
    messages = (await session.scalars(select(Message).where(
        Message.conversation_id == conversation.id, Message.active.is_(True)
    ).order_by(Message.seq))).all()
    threads = (await session.scalars(select(Thread).where(
        Thread.conversation_id == conversation.id, Thread.active.is_(True)
    ))).all()
    thread_results = []
    for thread in threads:
        count = await session.scalar(select(func.count()).select_from(ThreadMessage).where(ThreadMessage.thread_id == thread.id))
        thread_results.append({
            "id": str(thread.id), "parent_message_id": str(thread.parent_message_id),
            "anchor": {"text": thread.anchor_text, "start": thread.start_offset, "end": thread.end_offset,
                       "prefix": thread.prefix, "suffix": thread.suffix}, "message_count": count or 0,
        })
    return {"id": str(conversation.id), "title": conversation.title, "condition": conversation.condition,
            "system_context": conversation.system_context, "messages": [message_payload(item) for item in messages],
            "threads": thread_results}


@app.patch("/api/conversations/{conversation_id}")
async def rename_conversation(conversation_id: uuid.UUID, body: dict, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    conversation = await owned_conversation(session, user, conversation_id)
    title = str(body.get("title", "")).strip()
    if not title or len(title) > 240:
        raise HTTPException(status_code=422, detail="Title must be 1 to 240 characters")
    conversation.title = title
    conversation.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {"id": str(conversation.id), "title": title}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    conversation = await owned_conversation(session, user, conversation_id)
    conversation.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True}


@app.post("/api/conversations/{conversation_id}/messages")
async def send_message(conversation_id: uuid.UUID, body: MessageCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    conversation = await owned_conversation(session, user, conversation_id)
    if conversation.condition == "C" and body.quote and (not body.quote.get("text") or not body.quote.get("source_message_id")):
        raise HTTPException(status_code=422, detail="Quote requires text and source_message_id")
    if conversation.condition != "C" and body.quote:
        raise HTTPException(status_code=422, detail="Quotes are available only in condition C")
    max_seq = await session.scalar(select(func.max(Message.seq)).where(Message.conversation_id == conversation.id, Message.active.is_(True))) or 0
    user_message = Message(conversation_id=conversation.id, seq=max_seq + 1, role="user", content=body.content, quote=body.quote)
    assistant = Message(conversation_id=conversation.id, seq=max_seq + 2, role="assistant", content="", status="streaming")
    session.add_all([user_message, assistant])
    conversation.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(user_message)
    await session.refresh(assistant)
    generation_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    asyncio.create_task(generate(generation_id, conversation.id, assistant.id, queue, upto_seq=assistant.seq - 1))
    return await stream_response(queue, [("meta", {
        "user_message_id": str(user_message.id), "assistant_message_id": str(assistant.id), "generation_id": generation_id
    })])


@app.put("/api/messages/{message_id}")
async def edit_message(message_id: uuid.UUID, body: MessageEdit, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    message = await session.get(Message, message_id)
    if not message or not message.active or message.role != "user":
        raise HTTPException(status_code=404, detail="Editable user message not found")
    conversation = await owned_conversation(session, user, message.conversation_id)
    pending = await session.scalar(select(func.count()).select_from(Message).where(
        Message.conversation_id == conversation.id, Message.active.is_(True), Message.status == "streaming"
    ))
    if pending:
        raise HTTPException(status_code=409, detail="Cancel the active generation before editing")
    session.add(MessageRevision(message_id=message.id, content=message.content))
    message.content = body.content
    message.quote = None
    later = list((await session.scalars(select(Message).where(
        Message.conversation_id == conversation.id, Message.active.is_(True), Message.seq > message.seq
    ))).all())
    later_ids = [item.id for item in later]
    for item in later:
        item.active = False
    threads = []
    if later_ids:
        threads = list((await session.scalars(select(Thread).where(
            Thread.parent_message_id.in_(later_ids), Thread.active.is_(True)
        ))).all())
        for thread in threads:
            thread.active = False
    await session.commit()
    await session.refresh(message)
    assistant = Message(conversation_id=conversation.id, seq=message.seq + 1, role="assistant", content="", status="streaming")
    session.add(assistant)
    await session.commit()
    await session.refresh(assistant)
    queue: asyncio.Queue = asyncio.Queue()
    generation_id = str(uuid.uuid4())
    asyncio.create_task(generate(generation_id, conversation.id, assistant.id, queue, upto_seq=message.seq))
    return await stream_response(queue, [
        ("truncated", {"removed_messages": len(later), "removed_threads": len(threads)}),
        ("meta", {"user_message_id": str(message.id), "assistant_message_id": str(assistant.id), "generation_id": generation_id}),
    ])


@app.post("/api/threads")
async def create_thread(body: ThreadCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    parent = await session.get(Message, body.message_id)
    if not parent or not parent.active or parent.role != "assistant" or parent.status != "complete":
        raise HTTPException(status_code=422, detail="Thread parent must be an active completed assistant message")
    conversation = await owned_conversation(session, user, parent.conversation_id)
    anchor = body.anchor
    if anchor.end <= anchor.start or anchor.end - anchor.start != len(anchor.text):
        raise HTTPException(status_code=422, detail="Anchor offsets do not match selected text length")
    duplicate = await session.scalar(select(Thread).where(
        Thread.parent_message_id == parent.id, Thread.start_offset == anchor.start, Thread.end_offset == anchor.end,
        Thread.active.is_(True)
    ))
    if duplicate:
        return await thread_reply(duplicate, body.content, session, user, created=False)
    thread = Thread(
        conversation_id=conversation.id, parent_message_id=parent.id, anchor_text=anchor.text,
        start_offset=anchor.start, end_offset=anchor.end, prefix=anchor.prefix, suffix=anchor.suffix,
    )
    session.add(thread)
    await session.flush()
    user_message = ThreadMessage(thread_id=thread.id, seq=1, role="user", content=body.content)
    assistant = ThreadMessage(thread_id=thread.id, seq=2, role="assistant", content="", status="streaming")
    session.add_all([user_message, assistant])
    await session.commit()
    await session.refresh(thread)
    await session.refresh(assistant)
    queue: asyncio.Queue = asyncio.Queue()
    generation_id = str(uuid.uuid4())
    asyncio.create_task(generate(generation_id, conversation.id, assistant.id, queue, thread_id=thread.id, thread_message_id=user_message.id))
    return await stream_response(queue, [
        ("thread_created", {"thread_id": str(thread.id)}),
        ("meta", {"user_message_id": str(user_message.id), "assistant_message_id": str(assistant.id), "generation_id": generation_id}),
    ])


async def thread_reply(thread: Thread, content: str, session: AsyncSession, user: User, created: bool = True):
    conversation = await owned_conversation(session, user, thread.conversation_id)
    if not thread.active:
        raise HTTPException(status_code=404, detail="Thread not found")
    pending = await session.scalar(select(func.count()).select_from(ThreadMessage).where(
        ThreadMessage.thread_id == thread.id, ThreadMessage.status == "streaming"
    ))
    if pending:
        raise HTTPException(status_code=409, detail="Thread generation already in progress")
    max_seq = await session.scalar(select(func.max(ThreadMessage.seq)).where(ThreadMessage.thread_id == thread.id)) or 0
    user_message = ThreadMessage(thread_id=thread.id, seq=max_seq + 1, role="user", content=content)
    assistant = ThreadMessage(thread_id=thread.id, seq=max_seq + 2, role="assistant", content="", status="streaming")
    session.add_all([user_message, assistant])
    await session.commit()
    await session.refresh(user_message)
    await session.refresh(assistant)
    queue: asyncio.Queue = asyncio.Queue()
    generation_id = str(uuid.uuid4())
    asyncio.create_task(generate(generation_id, conversation.id, assistant.id, queue, thread_id=thread.id, thread_message_id=user_message.id))
    initial = []
    if created:
        initial.append(("thread_created", {"thread_id": str(thread.id)}))
    initial.append(("meta", {"user_message_id": str(user_message.id), "assistant_message_id": str(assistant.id), "generation_id": generation_id}))
    return await stream_response(queue, initial)


@app.get("/api/threads/{thread_id}")
async def get_thread(thread_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    thread = await session.get(Thread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    await owned_conversation(session, user, thread.conversation_id)
    rows = (await session.scalars(select(ThreadMessage).where(ThreadMessage.thread_id == thread.id).order_by(ThreadMessage.seq))).all()
    return {"id": str(thread.id), "parent_message_id": str(thread.parent_message_id), "anchor_text": thread.anchor_text,
            "messages": [message_payload(item) for item in rows]}


@app.post("/api/threads/{thread_id}/messages")
async def post_thread_message(thread_id: uuid.UUID, body: MessageCreate, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    thread = await session.get(Thread, thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    return await thread_reply(thread, body.content, session, user)


@app.post("/api/events")
async def receive_events(body: EventBatch, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    for item in body.events:
        if item.conversation_id:
            await owned_conversation(session, user, item.conversation_id)
        session.add(Event(user_id=user.id, conversation_id=item.conversation_id, thread_id=item.thread_id,
                          type=item.type, payload=item.payload, client_ts=item.client_ts))
    await session.commit()
    return {"accepted": len(body.events)}


from app.study import router as study_router
from app.admin import router as admin_router

app.include_router(study_router)
app.include_router(admin_router)
