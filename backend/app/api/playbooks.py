"""CRUD API cho SignupPlaybook + approve LLM fallback."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.deps import get_current_user
from app.models import User
from app.models.signup_playbook import SignupPlaybook

router = APIRouter(prefix="/api/playbooks", tags=["playbooks"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class PlaybookOut(BaseModel):
    id: int
    name: str
    platform: Optional[str]
    category: Optional[str]
    program_id: Optional[int]
    program_name: Optional[str]
    signup_url: Optional[str]
    status: str
    pending_llm_approval: bool
    pending_review: bool
    source: Optional[str]
    version: int
    steps: List[Dict[str, Any]]
    consecutive_fails: int
    total_runs: int
    success_runs: int
    fail_runs: int
    success_rate: float
    created_from_job_id: Optional[int]
    last_used_at: Optional[str]
    created_at: str
    updated_at: str


class PlaybookCreate(BaseModel):
    name: str
    platform: Optional[str] = None
    category: Optional[str] = None
    program_id: Optional[int] = None
    program_name: Optional[str] = None
    signup_url: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None


class PlaybookUpdate(BaseModel):
    name: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None


def _to_out(pb: SignupPlaybook) -> PlaybookOut:
    total = pb.total_runs or 0
    rate = round(pb.success_runs / total * 100, 1) if total else 0.0
    return PlaybookOut(
        id=pb.id,
        name=pb.name,
        platform=pb.platform,
        category=pb.category,
        program_id=pb.program_id,
        program_name=pb.program_name,
        signup_url=pb.signup_url,
        status=pb.status,
        pending_llm_approval=pb.pending_llm_approval,
        pending_review=bool(pb.pending_review),
        source=pb.source or "manual",
        version=pb.version or 1,
        steps=json.loads(pb.steps_json or "[]"),
        consecutive_fails=pb.consecutive_fails,
        total_runs=total,
        success_runs=pb.success_runs or 0,
        fail_runs=pb.fail_runs or 0,
        success_rate=rate,
        created_from_job_id=pb.created_from_job_id,
        last_used_at=pb.last_used_at.isoformat() + "Z" if pb.last_used_at else None,
        created_at=pb.created_at.isoformat() + "Z" if pb.created_at else "",
        updated_at=pb.updated_at.isoformat() + "Z" if pb.updated_at else "",
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[PlaybookOut])
async def list_playbooks(
    status: str = "",
    platform: str = "",
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    q = select(SignupPlaybook).where(SignupPlaybook.user_id == user.id)
    if status:
        q = q.where(SignupPlaybook.status == status)
    if platform:
        q = q.where(SignupPlaybook.platform == platform)
    q = q.order_by(SignupPlaybook.updated_at.desc())
    rows = (await session.execute(q)).scalars().all()
    return [_to_out(r) for r in rows]


@router.get("/by-platform", response_model=List[PlaybookOut])
async def list_playbooks_by_platform(
    platform: str = "",
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Trả về tất cả active playbooks cho 1 platform — dùng cho dropdown chọn script thủ công."""
    q = select(SignupPlaybook).where(
        SignupPlaybook.user_id == user.id,
        SignupPlaybook.status != "archived",
    )
    if platform:
        q = q.where(SignupPlaybook.platform == platform.lower())
    q = q.order_by(SignupPlaybook.updated_at.desc())
    rows = (await session.execute(q)).scalars().all()
    return [_to_out(r) for r in rows]


@router.get("/for-program/{program_id}", response_model=Optional[PlaybookOut])
async def get_playbook_for_program(
    program_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Tìm playbook active cho một program cụ thể."""
    pb = (
        await session.execute(
            select(SignupPlaybook).where(
                SignupPlaybook.user_id == user.id,
                SignupPlaybook.program_id == program_id,
                SignupPlaybook.status != "archived",
            )
        )
    ).scalar_one_or_none()
    return _to_out(pb) if pb else None


@router.post("", response_model=PlaybookOut)
async def create_playbook(
    body: PlaybookCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Tạo playbook thủ công (admin)."""
    from datetime import datetime
    pb = SignupPlaybook(
        user_id=user.id,
        name=body.name.strip(),
        platform=body.platform,
        category=body.category,
        program_id=body.program_id,
        program_name=body.program_name,
        signup_url=body.signup_url,
        status="active",
        steps_json=json.dumps(body.steps or [], ensure_ascii=False),
        consecutive_fails=0,
    )
    session.add(pb)
    await session.commit()
    await session.refresh(pb)
    return _to_out(pb)


def _affiliate_platform(signup_url: str) -> str:
    """Detect affiliate network platform từ signup URL.
    Dùng để matching playbook generic cho programs khác nguồn nhưng cùng nền tảng.
    """
    if not signup_url:
        return ""
    u = signup_url.lower()
    if "firstpromoter.com" in u:
        return "firstpromoter"
    if "everflowclient.io" in u or "everflow.com" in u:
        return "everflow"
    if "partnerstack.com" in u or "growsumo.com" in u:
        return "partnerstack"
    if "impact.com" in u or "impactradius.com" in u:
        return "impact"
    if "shareasale.com" in u:
        return "shareasale"
    if "cj.com" in u or "cjaffiliate.com" in u:
        return "cj"
    if "rakuten" in u or "linkshare.com" in u:
        return "rakuten"
    if "awin.com" in u:
        return "awin"
    if "refersion.com" in u:
        return "refersion"
    if "tapfiliate.com" in u:
        return "tapfiliate"
    if "rewardful.com" in u:
        return "rewardful"
    if "getambassador.com" in u:
        return "ambassador"
    if "affiliatewp.com" in u:
        return "affiliatewp"
    if "post.affiliate" in u:
        return "postaffiliatepro"
    return ""


@router.post("/batch-status")
async def batch_playbook_status(
    program_ids: List[int],
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Trả về playbook tốt nhất cho từng program_id theo 3 tầng ưu tiên:
      1. Khớp chính xác theo program_id
      2. Khớp theo affiliate_platform + category (script tái sử dụng, khác nguồn OK)
      3. Khớp theo affiliate_platform only

    Affiliate platform được detect từ signup_url (firstpromoter/everflow/partnerstack…)
    nên script Submagic (firstpromoter) có thể dùng cho mọi program trên firstpromoter.
    Trả về thêm match_type để frontend phân biệt khớp chính xác vs tương thích.
    """
    if not program_ids:
        return {}

    from app.models.affiliate_program import AffiliateProgram

    # 1. Load tất cả active playbooks của user (1 query)
    all_pbs = (
        await session.execute(
            select(SignupPlaybook).where(
                SignupPlaybook.user_id == user.id,
                SignupPlaybook.status != "archived",
            )
        )
    ).scalars().all()

    # Index — tầng 1: exact by program_id
    by_program: dict[int, SignupPlaybook] = {}
    # Tầng 2: affiliate_platform + category
    by_plat_cat: dict[str, SignupPlaybook] = {}   # "{aff_platform}_{category_lower}"
    # Tầng 3: category IS NULL (generic)
    by_plat_generic: dict[str, SignupPlaybook] = {}  # "{aff_platform}"
    # Tầng 4: affiliate_platform only (khác category — dùng tạm, playbook_fallback=True)
    by_platform: dict[str, SignupPlaybook] = {}   # "{aff_platform}"

    for pb in all_pbs:
        if pb.program_id and pb.program_id not in by_program:
            by_program[pb.program_id] = pb
        aff_p = (pb.platform or "").lower()
        cat = (pb.category or "").lower()
        if aff_p:
            k2 = f"{aff_p}_{cat}" if cat else ""
            if k2 and k2 not in by_plat_cat:
                by_plat_cat[k2] = pb
            if not cat and aff_p not in by_plat_generic:
                by_plat_generic[aff_p] = pb
            if aff_p not in by_platform:
                by_platform[aff_p] = pb

    # 2. Load thông tin programs (1 query)
    prog_rows = (
        await session.execute(
            select(AffiliateProgram).where(AffiliateProgram.id.in_(program_ids))
        )
    ).scalars().all()
    prog_map = {p.id: p for p in prog_rows}

    # 3. Resolve best playbook cho từng program_id
    result: dict = {}
    for pid in program_ids:
        prog = prog_map.get(pid)

        # Tầng 1 — khớp chính xác program_id
        pb = by_program.get(pid)
        match_type = "exact"
        is_fallback = False

        if pb is None and prog:
            aff_platform = _affiliate_platform(prog.signup_url or prog.url or "")
            category = (prog.category or "").lower()

            if aff_platform:
                # Tầng 2 — platform + category khớp
                pb = by_plat_cat.get(f"{aff_platform}_{category}")
                if pb:
                    match_type = "platform_category"

                # Tầng 3 — platform + category IS NULL (generic)
                if pb is None:
                    pb = by_plat_generic.get(aff_platform)
                    if pb:
                        match_type = "platform_category"

                # Tầng 4 — platform only, khác category (dùng tạm)
                if pb is None:
                    pb = by_platform.get(aff_platform)
                    if pb:
                        match_type = "platform"
                        is_fallback = True

        if pb:
            result[pid] = {
                "playbook_id": pb.id,
                "name": pb.name,
                "status": pb.status,
                "pending_llm_approval": pb.pending_llm_approval,
                "pending_review": pb.pending_review,
                "success_rate": round(pb.success_runs / pb.total_runs * 100, 1) if pb.total_runs else None,
                "consecutive_fails": pb.consecutive_fails,
                "match_type": match_type,
                "is_fallback": is_fallback,
            }

    return result


@router.get("/{playbook_id}", response_model=PlaybookOut)
async def get_playbook(
    playbook_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    pb = await _get_owned(playbook_id, user, session)
    return _to_out(pb)


@router.patch("/{playbook_id}", response_model=PlaybookOut)
async def update_playbook(
    playbook_id: int,
    body: PlaybookUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Đổi tên hoặc sửa steps của playbook."""
    pb = await _get_owned(playbook_id, user, session)
    if body.name is not None:
        pb.name = body.name.strip()
    if body.steps is not None:
        pb.steps_json = json.dumps(body.steps, ensure_ascii=False)
    from datetime import datetime
    pb.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(pb)
    return _to_out(pb)


@router.post("/{playbook_id}/archive", response_model=PlaybookOut)
async def archive_playbook(
    playbook_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    pb = await _get_owned(playbook_id, user, session)
    pb.status = "archived"
    from datetime import datetime
    pb.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(pb)
    return _to_out(pb)


@router.post("/{playbook_id}/restore", response_model=PlaybookOut)
async def restore_playbook(
    playbook_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    pb = await _get_owned(playbook_id, user, session)
    pb.status = "active"
    pb.pending_llm_approval = False
    pb.pending_review = False
    pb.consecutive_fails = 0
    from datetime import datetime
    pb.updated_at = datetime.utcnow()
    await session.commit()
    await session.refresh(pb)
    return _to_out(pb)


@router.post("/{playbook_id}/approve-llm")
async def approve_llm_fallback(
    playbook_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Admin yêu cầu LLM re-record script cho playbook.
    Reset trạng thái → tạo 1 LLM re-record job cho program.
    """
    pb = await _get_owned(playbook_id, user, session)
    if not pb.program_id:
        raise HTTPException(400, "Playbook này không gắn với program cụ thể; không thể tạo re-record job")

    # Reset playbook state
    pb.pending_llm_approval = False
    pb.status = "active"  # Tạm active, sẽ bị cập nhật sau khi LLM chạy xong
    pb.consecutive_fails = 0
    from datetime import datetime
    pb.updated_at = datetime.utcnow()
    await session.commit()

    # Tạo LLM signup job để re-record script
    # Cần có profile mặc định — sử dụng profile đầu tiên của user
    from app.services.storage import profile_store
    profiles = profile_store.list_profiles(user.id)
    if not profiles:
        raise HTTPException(400, "Không có profile nào để chạy LLM re-record. Hãy tạo profile trước.")

    profile_id = profiles[0]["id"]

    from app.services import signup_runner
    job_id = await signup_runner.enqueue(
        user_id=user.id,
        program_ids=[pb.program_id],
        profile_ids=[profile_id],
        extra_prompt="Đây là lần re-record script. Hãy hoàn thành đăng ký và ghi lại script.",
        llm_provider="",  # auto-select
        llm_key_index=0,
    )

    return {
        "ok": True,
        "message": f"Đã tạo LLM re-record job #{job_id}. Script sẽ được cập nhật sau khi job thành công.",
        "rerecord_job_id": job_id,
        "playbook": _to_out(pb),
    }


# ── Pending review (Tier 3 generated) ────────────────────────────────────────

@router.get("/pending", response_model=List[PlaybookOut])
async def list_pending_playbooks(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Danh sách playbooks do Tier 3 tạo ra, chờ human duyệt."""
    rows = (
        await session.execute(
            select(SignupPlaybook).where(
                SignupPlaybook.user_id == user.id,
                SignupPlaybook.pending_review == True,  # noqa: E712
            ).order_by(SignupPlaybook.updated_at.desc())
        )
    ).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("/{playbook_id}/approve-review", response_model=PlaybookOut)
async def approve_review_playbook(
    playbook_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Duyệt playbook do Tier 3 tạo → status = active."""
    pb = await _get_owned(playbook_id, user, session)
    from datetime import datetime as _dt
    pb.pending_review = False
    pb.status = "active"
    pb.updated_at = _dt.utcnow()
    await session.commit()
    await session.refresh(pb)
    return _to_out(pb)


@router.post("/{playbook_id}/reject-review", response_model=PlaybookOut)
async def reject_review_playbook(
    playbook_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Từ chối playbook do Tier 3 tạo → archived."""
    pb = await _get_owned(playbook_id, user, session)
    from datetime import datetime as _dt
    pb.pending_review = False
    pb.status = "archived"
    pb.updated_at = _dt.utcnow()
    await session.commit()
    await session.refresh(pb)
    return _to_out(pb)


# ── Helper ────────────────────────────────────────────────────────────────────

async def _get_owned(playbook_id: int, user: User, session: AsyncSession) -> SignupPlaybook:
    pb = (
        await session.execute(select(SignupPlaybook).where(SignupPlaybook.id == playbook_id))
    ).scalar_one_or_none()
    if not pb:
        raise HTTPException(404, "Playbook không tồn tại")
    if pb.user_id != user.id:
        raise HTTPException(403, "Không có quyền")
    return pb
