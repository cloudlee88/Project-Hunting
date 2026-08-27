"""CRUD API cho Platform Q&A Library."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.deps import get_current_user
from app.models import User
from app.models.platform_qa import PlatformQAEntry, ScriptLearningLog
from app.services.qa_match import _q_hash

router = APIRouter(prefix="/api/qa-library", tags=["qa-library"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class QAEntryOut(BaseModel):
    id: int
    platform: str
    category: Optional[str]
    program_id: Optional[int]
    question_pattern: str
    answer_template: str
    answer_type: str
    required: bool
    confidence: float
    usage_count: int
    success_count: int
    failure_count: int
    consecutive_failures: int
    status: str
    source: str
    note: Optional[str]
    created_at: str
    updated_at: str


class QAEntryCreate(BaseModel):
    platform: str
    category: Optional[str] = None
    program_id: Optional[int] = None
    question_pattern: str
    answer_template: str
    answer_type: str = "static"
    required: bool = True
    note: Optional[str] = None


class QAEntryUpdate(BaseModel):
    question_pattern: Optional[str] = None
    answer_template: Optional[str] = None
    category: Optional[str] = None
    note: Optional[str] = None
    status: Optional[str] = None
    confidence: Optional[float] = None


class QAStatsOut(BaseModel):
    platform: str
    total: int
    active: int
    needs_review: int
    archived: int


class LearningLogOut(BaseModel):
    id: int
    job_id: Optional[int]
    program_id: Optional[int]
    platform: Optional[str]
    event_type: str
    question: Optional[str]
    answer_used: Optional[str]
    tier_used: Optional[int]
    token_cost: Optional[float]
    outcome: Optional[str]
    created_at: str


def _to_out(e: PlatformQAEntry) -> QAEntryOut:
    return QAEntryOut(
        id=e.id,
        platform=e.platform,
        category=e.category,
        program_id=e.program_id,
        question_pattern=e.question_pattern,
        answer_template=e.answer_template,
        answer_type=e.answer_type,
        required=bool(e.required),
        confidence=round(e.confidence or 0.0, 3),
        usage_count=e.usage_count or 0,
        success_count=e.success_count or 0,
        failure_count=e.failure_count or 0,
        consecutive_failures=e.consecutive_failures or 0,
        status=e.status,
        source=e.source or "manual",
        note=e.note,
        created_at=e.created_at.isoformat() + "Z" if e.created_at else "",
        updated_at=e.updated_at.isoformat() + "Z" if e.updated_at else "",
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=List[QAEntryOut])
async def list_qa(
    platform: str = "",
    category: str = "",
    status: str = "",
    search: str = "",
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    q = select(PlatformQAEntry)
    if platform:
        q = q.where(PlatformQAEntry.platform == platform.lower())
    if category:
        q = q.where(PlatformQAEntry.category == category)
    if status:
        q = q.where(PlatformQAEntry.status == status)
    if search:
        q = q.where(PlatformQAEntry.question_pattern.ilike(f"%{search}%"))
    q = q.order_by(PlatformQAEntry.platform, PlatformQAEntry.id)
    rows = (await session.execute(q)).scalars().all()
    return [_to_out(r) for r in rows]


@router.get("/stats", response_model=List[QAStatsOut])
async def get_stats(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Thống kê số lượng Q&A theo platform."""
    rows = (
        await session.execute(
            select(
                PlatformQAEntry.platform,
                PlatformQAEntry.status,
                sqlfunc.count().label("cnt"),
            ).group_by(PlatformQAEntry.platform, PlatformQAEntry.status)
        )
    ).all()

    # Tổng hợp theo platform
    agg: Dict[str, Dict] = {}
    for plat, st, cnt in rows:
        if plat not in agg:
            agg[plat] = {"total": 0, "active": 0, "needs_review": 0, "archived": 0}
        agg[plat]["total"] += cnt
        if st in agg[plat]:
            agg[plat][st] = cnt

    return [
        QAStatsOut(platform=p, **v)
        for p, v in sorted(agg.items())
    ]


@router.post("", response_model=QAEntryOut)
async def create_qa(
    body: QAEntryCreate,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    now = datetime.utcnow()
    entry = PlatformQAEntry(
        platform=body.platform.lower(),
        category=body.category,
        program_id=body.program_id,
        question_pattern=body.question_pattern,
        question_hash=_q_hash(body.question_pattern),
        answer_template=body.answer_template,
        answer_type=body.answer_type,
        required=body.required,
        confidence=0.95,
        status="active",
        source="manual",
        note=body.note,
        created_at=now,
        updated_at=now,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return _to_out(entry)


@router.patch("/{entry_id}", response_model=QAEntryOut)
async def update_qa(
    entry_id: int,
    body: QAEntryUpdate,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    entry = await _get_entry(entry_id, session)
    if body.question_pattern is not None:
        entry.question_pattern = body.question_pattern
        entry.question_hash = _q_hash(body.question_pattern)
    if body.answer_template is not None:
        entry.answer_template = body.answer_template
    if body.category is not None:
        entry.category = body.category or None
    if body.note is not None:
        entry.note = body.note or None
    if body.status is not None:
        if body.status not in ("active", "needs_review", "archived"):
            raise HTTPException(400, "Status không hợp lệ")
        entry.status = body.status
        if body.status == "active":
            entry.consecutive_failures = 0
    if body.confidence is not None:
        entry.confidence = max(0.0, min(1.0, body.confidence))
    entry.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(entry)
    return _to_out(entry)


@router.delete("/{entry_id}", status_code=204)
async def archive_qa(
    entry_id: int,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    entry = await _get_entry(entry_id, session)
    entry.status = "archived"
    entry.updated_at = datetime.utcnow()
    await session.commit()


@router.post("/{entry_id}/restore", response_model=QAEntryOut)
async def restore_qa(
    entry_id: int,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    entry = await _get_entry(entry_id, session)
    entry.status = "active"
    entry.consecutive_failures = 0
    entry.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(entry)
    return _to_out(entry)


@router.post("/seed")
async def trigger_seed(
    force: bool = False,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Re-seed từ qa_library.json.
    - Mặc định: chỉ seed nếu bảng trống.
    - force=true: xoá toàn bộ seed entries rồi seed lại.
    """
    from app.services.qa_match import seed_qa_library_if_empty
    from sqlalchemy import delete as sql_delete

    if force:
        # Xoá tất cả entries có source="seed" rồi seed lại
        await session.execute(
            sql_delete(PlatformQAEntry).where(PlatformQAEntry.source == "seed")
        )
        await session.commit()

    seeded = await seed_qa_library_if_empty(force=force)
    return {"ok": True, "seeded": seeded}


# ── Learning log ───────────────────────────────────────────────────────────────

@router.get("/learning-log", response_model=List[LearningLogOut])
async def list_learning_log(
    job_id: Optional[int] = None,
    platform: str = "",
    event_type: str = "",
    tier_used: Optional[int] = None,
    limit: int = Query(default=100, le=500),
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    q = select(ScriptLearningLog)
    if job_id:
        q = q.where(ScriptLearningLog.job_id == job_id)
    if platform:
        q = q.where(ScriptLearningLog.platform == platform)
    if event_type:
        q = q.where(ScriptLearningLog.event_type == event_type)
    if tier_used:
        q = q.where(ScriptLearningLog.tier_used == tier_used)
    q = q.order_by(ScriptLearningLog.created_at.desc()).limit(limit)
    rows = (await session.execute(q)).scalars().all()
    return [
        LearningLogOut(
            id=r.id,
            job_id=r.job_id,
            program_id=r.program_id,
            platform=r.platform,
            event_type=r.event_type,
            question=r.question,
            answer_used=r.answer_used,
            tier_used=r.tier_used,
            token_cost=r.token_cost,
            outcome=r.outcome,
            created_at=r.created_at.isoformat() + "Z" if r.created_at else "",
        )
        for r in rows
    ]


# ── Helper ─────────────────────────────────────────────────────────────────────

async def _get_entry(entry_id: int, session: AsyncSession) -> PlatformQAEntry:
    entry = (
        await session.execute(select(PlatformQAEntry).where(PlatformQAEntry.id == entry_id))
    ).scalar_one_or_none()
    if not entry:
        raise HTTPException(404, "Q&A entry không tồn tại")
    return entry
