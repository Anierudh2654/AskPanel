import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import current_user, owned_conversation
from app.models import Conversation, Event, User

router = APIRouter(prefix="/api/study", tags=["study"])


class ConsentBody(BaseModel):
    accepted: bool
    demographics: dict = Field(default_factory=dict)


class SurveyBody(BaseModel):
    instrument: str
    responses: dict


@router.get("/state")
async def participant_state(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    participant = await session.scalar(select(Participant).where(Participant.user_id == user.id))
    if participant is None:
        return {"consented": False, "sessions": []}
    rows = (await session.scalars(select(StudySession).where(
        StudySession.participant_id == participant.id
    ).order_by(StudySession.order_index))).all()
    result = []
    for row in rows:
        task = await session.get(Task, row.task_id)
        conversation = await session.scalar(select(Conversation).where(Conversation.study_session_id == row.id))
        result.append({
            "id": str(row.id), "task_id": str(row.task_id), "condition": row.condition,
            "order_index": row.order_index, "status": row.status,
            "title": task.title if task else "Study task",
            "kind": task.kind if task else "unknown",
            "material_md": task.material_md if task else "",
            "quiz": task.quiz if task else [],
            "time_limit_s": task.time_limit_s if task else 0,
            "conversation_id": str(conversation.id) if conversation else None,
        })
    return {"consented": participant.consent_at is not None, "consent_at": participant.consent_at.isoformat() if participant.consent_at else None, "sessions": result}


@router.get("/session/current")
async def current_session(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    sessions = (await session.scalars(select(StudySession).where(StudySession.participant_id == user.id).order_by(StudySession.order_index))).all()
    return [{"id": str(item.id), "task_id": str(item.task_id), "condition": item.condition, "order_index": item.order_index, "status": item.status} for item in sessions]


@router.post("/consent")
async def record_consent(body: ConsentBody, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    participant = await session.scalar(select(Participant).where(Participant.user_id == user.id))
    if participant is None:
        participant = Participant(user_id=user.id)
        session.add(participant)
        await session.flush()
    if not body.accepted:
        raise HTTPException(status_code=400, detail="Consent is required before study participation")
    participant.consent_at = datetime.now(timezone.utc)
    participant.demographics = body.demographics
    await session.commit()
    return {"consented": True, "consent_at": participant.consent_at.isoformat()}


@router.post("/session/{session_id}/start")
async def start_session(session_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    study = await session.get(StudySession, session_id)
    participant = await session.scalar(select(Participant).where(Participant.user_id == user.id))
    if not participant or not participant.consent_at or not study or study.participant_id != participant.id:
        raise HTTPException(status_code=404, detail="Consented study session not found")
    study.status = "active"
    study.started_at = datetime.now(timezone.utc)
    await session.commit()
    return {"id": str(study.id), "status": study.status, "started_at": study.started_at.isoformat()}


@router.post("/session/{session_id}/conversation")
async def create_study_conversation(session_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    study = await session.get(StudySession, session_id)
    participant = await session.scalar(select(Participant).where(Participant.user_id == user.id))
    if not participant or not participant.consent_at or not study or study.participant_id != participant.id:
        raise HTTPException(status_code=404, detail="Consented study session not found")
    if study.status != "active":
        raise HTTPException(status_code=409, detail="Start the study session before opening its conversation")
    existing = await session.scalar(select(Conversation).where(Conversation.study_session_id == study.id))
    if existing:
        return {"id": str(existing.id), "condition": existing.condition}
    task = await session.get(Task, study.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Study task not found")
    conversation = Conversation(
        user_id=user.id, title=task.title, condition=study.condition,
        study_session_id=study.id, system_context=task.material_md,
    )
    session.add(conversation)
    await session.commit()
    await session.refresh(conversation)
    return {"id": str(conversation.id), "condition": conversation.condition}


@router.post("/session/{session_id}/end-chat")
async def end_chat(session_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    study = await session.get(StudySession, session_id)
    participant = await session.scalar(select(Participant).where(Participant.user_id == user.id))
    if not study or not participant or study.participant_id != participant.id:
        raise HTTPException(status_code=404, detail="Study session not found")
    study.status = "quiz"
    study.chat_ended_at = datetime.now(timezone.utc)
    await session.commit()
    return {"status": study.status}


@router.post("/quiz")
async def submit_quiz(body: dict, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    study = await _owned_study(body.get("study_session_id"), user, session)
    if study.status != "quiz":
        raise HTTPException(status_code=409, detail="Quiz is not available")
    submitted = body.get("responses", {})
    task = await session.get(Task, study.task_id)
    quiz = task.quiz if task else []
    for item in quiz:
        response = submitted.get(item.get("id"))
        correct = item.get("answer")
        score = 1 if response == correct else 0 if item.get("type") == "mcq" else None
        session.add(QuizResponse(study_session_id=study.id, question_id=item.get("id", ""), answer=str(response or ""), auto_score=score))
    study.status = "survey"
    await session.commit()
    return {"status": study.status, "answered": len(submitted)}


@router.post("/surveys")
async def submit_survey(body: SurveyBody, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if body.instrument not in {"sus", "tlx", "tam", "pre", "post"}:
        raise HTTPException(status_code=422, detail="Unknown survey instrument")
    participant = await session.scalar(select(Participant).where(Participant.user_id == user.id))
    if participant is None or participant.consent_at is None:
        raise HTTPException(status_code=403, detail="Consent required")
    session.add(Survey(participant_id=participant.id, instrument=body.instrument, responses=body.responses, scores=score_survey(body.instrument, body.responses)))
    await session.commit()
    return {"submitted": True, "instrument": body.instrument}


@router.post("/session/{session_id}/finish")
async def finish_session(session_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    study = await session.get(StudySession, session_id)
    participant = await session.scalar(select(Participant).where(Participant.user_id == user.id))
    if not study or not participant or study.participant_id != participant.id:
        raise HTTPException(status_code=404, detail="Study session not found")
    study.status = "done"
    study.ended_at = datetime.now(timezone.utc)
    await session.commit()
    return {"status": study.status}


async def _owned_study(raw_id: str | None, user: User, session: AsyncSession):
    try:
        session_id = uuid.UUID(str(raw_id))
    except ValueError:
        raise HTTPException(status_code=422, detail="Valid study_session_id required") from None
    study = await session.get(StudySession, session_id)
    participant = await session.scalar(select(Participant).where(Participant.user_id == user.id))
    if not study or not participant or study.participant_id != participant.id:
        raise HTTPException(status_code=404, detail="Study session not found")
    return study


def score_survey(instrument: str, responses: dict) -> dict:
    values = {key: int(value) for key, value in responses.items()}
    if instrument == "sus" and len(values) == 10:
        def item_order(key: str) -> tuple[int, int | str]:
            digits = "".join(character for character in key if character.isdigit())
            return (0, int(digits)) if digits else (1, key)

        ordered_values = [values[key] for key in sorted(values, key=item_order)]
        total = sum(value - 1 if index % 2 == 0 else 5 - value for index, value in enumerate(ordered_values))
        return {"sus": total * 2.5}
    if instrument == "tlx" and len(values) == 6:
        return {"tlx_raw": sum(values.values()) / 6}
    if instrument == "tam":
        return {"tam_mean": sum(values.values()) / len(values)} if values else {}
    return {}

from app.models import Participant, QuizResponse, StudySession, Survey, Task
