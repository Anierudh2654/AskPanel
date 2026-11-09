import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import current_user
from app.models import Conversation, EvalRun, Event, Job, LLMCall, Participant, StudySession, Survey, Task, Thread, User

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


class ParticipantImport(BaseModel):
    codes: list[str] = Field(min_length=1, max_length=500)
    synthetic: bool = False


class EvalRequest(BaseModel):
    rubric: str
    scope: dict = Field(default_factory=dict)


class TaskInput(BaseModel):
    slug: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    kind: str = Field(pattern="^(paper_section|code_debug)$")
    material_md: str
    quiz: list[dict] = Field(default_factory=list)
    time_limit_s: int = Field(default=900, ge=60, le=14400)


class StudyAssignment(BaseModel):
    code: str
    task_ids: list[uuid.UUID] = Field(min_length=3, max_length=3)
    order_group: str = Field(pattern="^(ABC|ACB|BAC|BCA|CAB|CBA)$")


@router.get("/tasks")
async def list_tasks(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Task).order_by(Task.slug))).all()
    return [{"id": str(row.id), "slug": row.slug, "title": row.title, "kind": row.kind, "version": row.version, "time_limit_s": row.time_limit_s} for row in rows]


@router.post("/tasks")
async def upsert_task(body: TaskInput, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    task = await session.scalar(select(Task).where(Task.slug == body.slug))
    if task is None:
        task = Task(slug=body.slug, title=body.title, kind=body.kind, material_md=body.material_md, quiz=body.quiz, time_limit_s=body.time_limit_s)
        session.add(task)
    else:
        task.title = body.title
        task.kind = body.kind
        task.material_md = body.material_md
        task.quiz = body.quiz
        task.time_limit_s = body.time_limit_s
        task.version += 1
    await session.commit()
    await session.refresh(task)
    return {"id": str(task.id), "slug": task.slug, "version": task.version}


@router.post("/study/assign")
async def assign_study(body: StudyAssignment, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    participant_user = await session.scalar(select(User).where(User.code == body.code.strip(), User.role == "participant"))
    if participant_user is None:
        raise HTTPException(status_code=404, detail="Participant code not found")
    participant = await session.scalar(select(Participant).where(Participant.user_id == participant_user.id))
    if participant is None:
        participant = Participant(user_id=participant_user.id, order_group=body.order_group)
        session.add(participant)
        await session.flush()
    existing = await session.scalar(select(func.count()).select_from(StudySession).where(StudySession.participant_id == participant.id))
    if existing:
        raise HTTPException(status_code=409, detail="Participant already has assigned study sessions")
    tasks = []
    for task_id in body.task_ids:
        task = await session.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        tasks.append(task)
    participant.order_group = body.order_group
    conditions = list(body.order_group)
    created = []
    for index, (task, condition) in enumerate(zip(tasks, conditions, strict=True)):
        study = StudySession(participant_id=participant.id, task_id=task.id, condition=condition, order_index=index)
        session.add(study)
        await session.flush()
        created.append({"id": str(study.id), "task_id": str(task.id), "condition": condition, "order_index": index})
    await session.commit()
    return {"code": participant_user.code, "order_group": body.order_group, "sessions": created}


@router.get("/overview")
async def overview(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    enrolled = await session.scalar(select(func.count()).select_from(Participant)) or 0
    completed = await session.scalar(select(func.count()).select_from(StudySession).where(StudySession.status == "done")) or 0
    sessions = (await session.execute(select(StudySession.condition, func.count()).group_by(StudySession.condition))).all()
    spend = await session.scalar(select(func.coalesce(func.sum(LLMCall.cost_usd), 0))) or 0
    errors = await session.scalar(select(func.count()).select_from(LLMCall).where(LLMCall.error.is_not(None))) or 0
    return {"participants_enrolled": enrolled, "sessions_completed": completed, "sessions_by_condition": {condition: count for condition, count in sessions}, "completion_rate": completed / enrolled if enrolled else 0, "spend_usd": float(spend), "llm_errors": errors}


@router.get("/participants")
async def participants(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(Participant).order_by(Participant.id))).all()
    result = []
    for item in rows:
        user = await session.get(User, item.user_id)
        sessions = (await session.scalars(select(StudySession).where(StudySession.participant_id == item.id))).all()
        result.append({"id": str(item.id), "code": user.code if user else None, "order_group": item.order_group, "consent": item.consent_at.isoformat() if item.consent_at else None, "sessions": [{"id": str(row.id), "condition": row.condition, "status": row.status} for row in sessions]})
    return result


@router.post("/participants/import")
async def import_participants(body: ParticipantImport, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    added = []
    for raw_code in body.codes:
        code = raw_code.strip()
        if not code:
            continue
        user = await session.scalar(select(User).where(User.code == code))
        if user is None:
            user = User(code=code, role="participant", is_synthetic=body.synthetic)
            session.add(user)
            await session.flush()
        participant = await session.scalar(select(Participant).where(Participant.user_id == user.id))
        if participant is None:
            participant = Participant(user_id=user.id)
            session.add(participant)
        added.append(code)
    await session.commit()
    return {"created_or_found": added}


@router.get("/sessions")
async def sessions(condition: str | None = None, task_id: uuid.UUID | None = None, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    query = select(StudySession)
    if condition:
        query = query.where(StudySession.condition == condition)
    if task_id:
        query = query.where(StudySession.task_id == task_id)
    rows = (await session.scalars(query.order_by(StudySession.started_at.desc()))).all()
    return [{"id": str(row.id), "participant_id": str(row.participant_id), "task_id": str(row.task_id), "condition": row.condition, "order_index": row.order_index, "status": row.status, "started_at": row.started_at.isoformat() if row.started_at else None, "ended_at": row.ended_at.isoformat() if row.ended_at else None} for row in rows]


@router.get("/llm-calls")
async def llm_calls(include_prompt: bool = False, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(LLMCall).order_by(LLMCall.created_at.desc()).limit(200))).all()
    fields = ["id", "purpose", "conversation_id", "thread_id", "message_id", "provider", "model", "params", "prompt_version", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "ttft_ms", "latency_ms", "cost_usd", "error", "created_at"]
    result = [{field: (getattr(row, field).isoformat() if field == "created_at" and getattr(row, field) else str(getattr(row, field)) if field in {"id", "conversation_id", "thread_id", "message_id", "cost_usd"} and getattr(row, field) is not None else getattr(row, field)) for field in fields} for row in rows]
    if include_prompt:
        for item, row in zip(result, rows, strict=True):
            item["assembled_prompt"] = row.assembled_prompt
    return result


@router.get("/stats/compare")
async def compare(metric: str = "tokens_total", conditions: str = "A,B", _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    selected = [value.strip() for value in conditions.split(",") if value.strip() in {"A", "B", "C"}]
    calls = (await session.scalars(select(LLMCall).where(LLMCall.purpose.in_(["main", "thread"])))).all()
    per_conversation: dict[uuid.UUID, dict[str, float]] = {}
    for call in calls:
        if call.conversation_id is None:
            continue
        bucket = per_conversation.setdefault(call.conversation_id, {"tokens_total": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0, "ttft_ms_mean": 0, "latency_ms_mean": 0, "n": 0})
        bucket["tokens_total"] += call.input_tokens + call.output_tokens
        bucket["tokens_in"] += call.input_tokens
        bucket["tokens_out"] += call.output_tokens
        bucket["cost_usd"] += float(call.cost_usd or 0)
        bucket["ttft_ms_mean"] += call.ttft_ms
        bucket["latency_ms_mean"] += call.latency_ms
        bucket["n"] += 1
    rows = (await session.scalars(select(Conversation).where(Conversation.condition.in_(selected), Conversation.deleted_at.is_(None)))).all() if selected else []
    grouped: dict[str, list[float]] = {condition: [] for condition in selected}
    for row in rows:
        values = per_conversation.get(row.id)
        if values and metric in values:
            grouped[row.condition].append(values[metric] / values["n"] if metric.endswith("_mean") and values["n"] else values[metric])
    summary = {}
    for condition, values in grouped.items():
        ordered = sorted(values)
        count = len(ordered)
        summary[condition] = {"n": count, "mean": sum(ordered) / count if count else None, "median": ordered[count // 2] if count else None, "values": ordered}
    return {"metric": metric, "conditions": selected, "groups": summary, "analysis": "Descriptive MVP. Inferential tests and mixed models are not yet enabled."}


@router.get("/export")
async def export_data(dataset: str = "sessions", _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    if dataset == "sessions":
        rows = (await session.scalars(select(StudySession).order_by(StudySession.created_at if hasattr(StudySession, "created_at") else StudySession.id))).all()
        fields = ["id", "participant_id", "task_id", "condition", "order_index", "status", "started_at", "chat_ended_at", "ended_at"]
        records = [{field: getattr(row, field, None) for field in fields} for row in rows]
    elif dataset == "events":
        rows = (await session.scalars(select(Event))).all()
        fields = ["id", "user_id", "conversation_id", "study_session_id", "thread_id", "type", "payload", "client_ts", "server_ts"]
        records = [{field: getattr(row, field, None) for field in fields} for row in rows]
    elif dataset == "llm_calls":
        rows = (await session.scalars(select(LLMCall))).all()
        fields = ["id", "purpose", "conversation_id", "provider", "model", "input_tokens", "output_tokens", "ttft_ms", "latency_ms", "cost_usd", "created_at"]
        records = [{field: getattr(row, field, None) for field in fields} for row in rows]
    else:
        raise HTTPException(status_code=422, detail="Dataset must be sessions, events, or llm_calls")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for record in records:
        writer.writerow({key: value.isoformat() if isinstance(value, datetime) else str(value) if value is not None else "" for key, value in record.items()})
    return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=askpanel-{dataset}.csv"})


@router.post("/eval/run")
async def run_eval(body: EvalRequest, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    from app.models import Rubric
    rubric = await session.scalar(select(Rubric).where(Rubric.slug == body.rubric).order_by(Rubric.version.desc()))
    if not rubric:
        raise HTTPException(status_code=404, detail="Rubric not found. Seed the researcher-authored rubric first.")
    run = EvalRun(rubric_id=rubric.id, judge_model="unconfigured", config={"scope": body.scope}, status="queued")
    job = Job(kind="eval", payload={"eval_run_id": str(run.id), "rubric_id": str(rubric.id), "scope": body.scope}, status="pending")
    session.add_all([run, job])
    await session.commit()
    return {"eval_run_id": str(run.id), "job_id": str(job.id), "status": "queued", "note": "Judge worker and separate provider credentials must be configured."}


@router.get("/eval/runs")
async def eval_runs(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(EvalRun).order_by(EvalRun.created_at.desc()))).all()
    return [{"id": str(row.id), "rubric_id": str(row.rubric_id), "judge_model": row.judge_model, "status": row.status, "created_at": row.created_at.isoformat() if row.created_at else None} for row in rows]
