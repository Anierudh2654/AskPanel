import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


JSON_VALUE = JSON().with_variant(JSONB, "postgresql")
UUID_VALUE = UUID(as_uuid=True)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    role: Mapped[str] = mapped_column(String(20), default="participant")
    code: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(240), default="New conversation")
    condition: Mapped[str] = mapped_column(String(1), default="A")
    study_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID_VALUE, nullable=True)
    system_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "seq", "active", name="uq_message_seq_active"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    quote: Mapped[dict | None] = mapped_column(JSON_VALUE, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="complete")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class MessageRevision(Base):
    __tablename__ = "message_revisions"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    revised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Thread(Base):
    __tablename__ = "threads"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    parent_message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), index=True)
    anchor_text: Mapped[str] = mapped_column(Text)
    start_offset: Mapped[int] = mapped_column(Integer)
    end_offset: Mapped[int] = mapped_column(Integer)
    prefix: Mapped[str] = mapped_column(Text, default="")
    suffix: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThreadMessage(Base):
    __tablename__ = "thread_messages"
    __table_args__ = (UniqueConstraint("thread_id", "seq", name="uq_thread_message_seq"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("threads.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="complete")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    purpose: Mapped[str] = mapped_column(String(20), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID_VALUE, nullable=True, index=True)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(UUID_VALUE, nullable=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID_VALUE, nullable=True)
    thread_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID_VALUE, nullable=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(160))
    params: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    prompt_version: Mapped[str] = mapped_column(String(40))
    assembled_prompt: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    response_text: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ttft_ms: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID_VALUE, nullable=True, index=True)
    study_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID_VALUE, nullable=True, index=True)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(UUID_VALUE, nullable=True)
    type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    client_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    server_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    kind: Mapped[str] = mapped_column(String(40))
    material_md: Mapped[str] = mapped_column(Text, default="")
    quiz: Mapped[list] = mapped_column(JSON_VALUE, default=list)
    time_limit_s: Mapped[int] = mapped_column(Integer, default=900)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Participant(Base):
    __tablename__ = "participants"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    demographics: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    order_group: Mapped[str] = mapped_column(String(80), default="ABC")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class StudySession(Base):
    __tablename__ = "study_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"), index=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), index=True)
    condition: Mapped[str] = mapped_column(String(1))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chat_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuizResponse(Base):
    __tablename__ = "quiz_responses"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    study_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("study_sessions.id"), index=True)
    question_id: Mapped[str] = mapped_column(String(120))
    answer: Mapped[str] = mapped_column(Text)
    auto_score: Mapped[float | None] = mapped_column(nullable=True)
    judge_score: Mapped[float | None] = mapped_column(nullable=True)
    human_score: Mapped[float | None] = mapped_column(nullable=True)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Survey(Base):
    __tablename__ = "surveys"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    study_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID_VALUE, nullable=True, index=True)
    participant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("participants.id"), index=True)
    instrument: Mapped[str] = mapped_column(String(20))
    responses: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    scores: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Rubric(Base):
    __tablename__ = "rubrics"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    spec: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    rubric_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rubrics.id"), index=True)
    judge_model: Mapped[str] = mapped_column(String(160))
    config: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalScore(Base):
    __tablename__ = "eval_scores"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    eval_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_runs.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(60))
    target_id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, index=True)
    scores: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID_VALUE, primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
