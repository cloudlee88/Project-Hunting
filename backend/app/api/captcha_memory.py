"""API: Captcha memory per user — GET/PUT."""
from fastapi import APIRouter, Depends
from app.deps import get_current_user
from app.models import User
from app.services.captcha.memory_service import raw_memory, update_memory_raw

router = APIRouter(prefix="/api/captcha-memory", tags=["captcha-memory"])


@router.get("")
async def get_memory(user: User = Depends(get_current_user)) -> dict:
    """Trả về captcha memory của user hiện tại."""
    return raw_memory(user.id)


@router.put("")
async def put_memory(body: dict, user: User = Depends(get_current_user)) -> dict:
    """Cập nhật captcha memory. Body = toàn bộ JSON object."""
    return update_memory_raw(user.id, body)
