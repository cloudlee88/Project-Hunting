from datetime import datetime, timedelta
from typing import Optional, Tuple
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User, UserRole, Session as DbSession
from app.core.security import hash_password, verify_password, generate_token
from app.core.config import settings


class AuthError(Exception):
    pass


class PendingApprovalError(AuthError):
    """Đăng nhập đúng mật khẩu nhưng tài khoản chưa được admin duyệt."""


async def register(email: str, password: str, session: AsyncSession) -> User:
    email = email.lower().strip()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing:
        raise AuthError("Email đã tồn tại")
    user = User(email=email, password_hash=hash_password(password), role=UserRole.USER, is_active=False)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def login(email: str, password: str, session: AsyncSession) -> Tuple[User, str]:
    email = email.lower().strip()
    user = await session.scalar(select(User).where(User.email == email))
    if not user or not verify_password(password, user.password_hash):
        raise AuthError("Email hoặc mật khẩu không đúng")
    if not user.is_active:
        raise PendingApprovalError("Tài khoản đang chờ admin duyệt")
    token = generate_token()
    expires = datetime.utcnow() + timedelta(hours=settings.session_ttl_hours)
    session.add(DbSession(token=token, user_id=user.id, expires_at=expires))
    await session.commit()
    return user, token


async def get_user_by_token(token: str, session: AsyncSession) -> Optional[User]:
    if not token:
        return None
    row = await session.scalar(select(DbSession).where(DbSession.token == token))
    if not row:
        return None
    now = datetime.utcnow()
    if row.expires_at < now:
        return None
    # Sliding expiration: khi phiên đã qua nửa vòng đời thì gia hạn thêm 1 TTL nữa,
    # để user ĐANG HOẠT ĐỘNG không bị đăng xuất đột ngột đúng lúc hết TTL. Ghi
    # throttled (tối đa ~1 lần / nửa TTL) nên gần như không thêm tải.
    ttl = timedelta(hours=settings.session_ttl_hours)
    if row.expires_at - now < ttl / 2:
        row.expires_at = now + ttl
        await session.commit()
    return await session.get(User, row.user_id)


async def logout(token: str, session: AsyncSession) -> None:
    if not token:
        return
    await session.execute(delete(DbSession).where(DbSession.token == token))
    await session.commit()


async def seed_default_admin(session: AsyncSession, email: str = "admin", password: str = "123456") -> None:
    """Đảm bảo tài khoản admin mặc định (admin/123456) luôn tồn tại, active và có role admin.

    KHÔNG đụng tới các user khác — họ do admin duyệt và phải sống qua mỗi lần BE reload.
    """
    email = email.lower().strip()
    existing = await session.scalar(select(User).where(User.email == email))
    target_hash = hash_password(password)
    if existing is None:
        session.add(User(email=email, password_hash=target_hash, role=UserRole.ADMIN, is_active=True))
    else:
        if not verify_password(password, existing.password_hash):
            existing.password_hash = target_hash
        existing.role = UserRole.ADMIN
        existing.is_active = True
    await session.commit()
