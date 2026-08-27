from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.deps import require_admin
from app.models import User, UserRole
from app.models.discovery import DiscoveryCandidate, DiscoverySource

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class AdminUserOut(BaseModel):
    id: int
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime
    source_count: int
    candidate_count: int


class UserListOut(BaseModel):
    items: List[AdminUserOut]
    pending_count: int


async def _get_target(user_id: int, session: AsyncSession, me: User) -> User:
    target = await session.get(User, user_id)
    if not target:
        raise HTTPException(404, "Không tìm thấy tài khoản")
    if target.id == me.id:
        raise HTTPException(400, "Không thể thao tác trên chính tài khoản của bạn")
    if target.is_admin:
        raise HTTPException(400, "Không thể thao tác trên tài khoản admin")
    return target


@router.get("/users", response_model=UserListOut)
async def list_users(session: AsyncSession = Depends(get_session)):
    users = (await session.execute(select(User).order_by(User.is_active, User.id.desc()))).scalars().all()
    src_counts = dict((await session.execute(
        select(DiscoverySource.user_id, func.count()).group_by(DiscoverySource.user_id)
    )).all())
    cand_counts = dict((await session.execute(
        select(DiscoveryCandidate.user_id, func.count()).group_by(DiscoveryCandidate.user_id)
    )).all())
    return UserListOut(
        items=[
            AdminUserOut(
                id=u.id, email=u.email, role=u.role, is_active=u.is_active, created_at=u.created_at,
                source_count=src_counts.get(u.id, 0), candidate_count=cand_counts.get(u.id, 0),
            )
            for u in users
        ],
        pending_count=sum(1 for u in users if not u.is_active),
    )


@router.get("/users/pending-count")
async def pending_count(session: AsyncSession = Depends(get_session)):
    n = (await session.execute(
        select(func.count()).select_from(User).where(User.is_active.is_(False))
    )).scalar() or 0
    return {"pending_count": n}


@router.post("/users/{user_id}/approve")
async def approve_user(user_id: int, session: AsyncSession = Depends(get_session),
                       me: User = Depends(require_admin)):
    target = await _get_target(user_id, session, me)
    target.is_active = True
    await session.commit()
    return {"ok": True, "id": target.id, "is_active": True}


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(user_id: int, session: AsyncSession = Depends(get_session),
                          me: User = Depends(require_admin)):
    """Khoá tài khoản. Request tiếp theo của họ bị chặn ngay (get_current_user kiểm tra is_active)."""
    target = await _get_target(user_id, session, me)
    target.is_active = False
    await session.commit()
    return {"ok": True, "id": target.id, "is_active": False}


@router.delete("/users/{user_id}", status_code=204)
async def reject_user(user_id: int, session: AsyncSession = Depends(get_session),
                      me: User = Depends(require_admin)):
    """Từ chối một tài khoản chờ duyệt. Tài khoản đã active phải khoá trước khi xoá,
    để không lỡ tay xoá mất người đang dùng (dữ liệu dự án của họ sẽ thành mồ côi)."""
    target = await _get_target(user_id, session, me)
    if target.is_active:
        raise HTTPException(400, "Hãy khoá tài khoản trước khi xoá")
    await session.delete(target)
    await session.commit()
