from datetime import datetime
from pydantic import BaseModel, Field
from app.models.user import UserRole


class RegisterIn(BaseModel):
    email: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime


class RegisterOut(BaseModel):
    """Đăng ký xong CHƯA đăng nhập được — phải chờ admin duyệt."""
    id: int
    email: str
    pending_approval: bool
    message: str
