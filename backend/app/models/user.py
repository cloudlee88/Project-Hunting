from datetime import datetime
from enum import StrEnum
from sqlalchemy import String, DateTime, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class UserRole(StrEnum):
    """Quyền của tài khoản. Lưu dạng chuỗi trong cột `users.role` — chỉ đổi bằng
    tay trong DB (hoặc tài khoản admin mặc định được seed lúc khởi động)."""
    USER = "user"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=UserRole.USER, nullable=False)
    # Tài khoản mới phải chờ admin duyệt mới đăng nhập được
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN
