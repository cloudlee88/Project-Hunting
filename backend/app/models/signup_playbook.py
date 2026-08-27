"""SignupPlaybook — script được ghi lại từ LLM run, dùng để replay không cần LLM."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SignupPlaybook(Base):
    __tablename__ = "signup_playbooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    # ── Identity ─────────────────────────────────────────────
    # Tên do admin đặt hoặc tự động sinh: "{platform}_{category}"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[Optional[str]] = mapped_column(String(64))   # source: goaffpro, shopify…
    category: Mapped[Optional[str]] = mapped_column(String(64))   # program category

    # Playbook specific cho 1 program (NULL = generic cho platform+category)
    program_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    program_name: Mapped[Optional[str]] = mapped_column(String(256))
    signup_url: Mapped[Optional[str]] = mapped_column(String(500))

    # ── State ────────────────────────────────────────────────
    # active | archived | needs_llm
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    # True = đang chờ admin approve để gọi LLM re-record
    pending_llm_approval: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Script ───────────────────────────────────────────────
    steps_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    # ── Stats ────────────────────────────────────────────────
    consecutive_fails: Mapped[int] = mapped_column(Integer, default=0)
    total_runs: Mapped[int] = mapped_column(Integer, default=0)
    success_runs: Mapped[int] = mapped_column(Integer, default=0)
    fail_runs: Mapped[int] = mapped_column(Integer, default=0)

    # ── Lineage ──────────────────────────────────────────────
    created_from_job_id: Mapped[Optional[int]] = mapped_column(Integer)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # ── Q&A / Learning ───────────────────────────────────────
    # "manual" | "ai_generated" | "ai_updated"
    source: Mapped[Optional[str]] = mapped_column(String(16), default="manual")
    version: Mapped[int] = mapped_column(Integer, default=1)
    # True khi Tier 3 tạo playbook mới → chờ human approve
    pending_review: Mapped[bool] = mapped_column(Boolean, default=False)
    # Session ID của Tier 3 agent đã tạo ra playbook này
    review_session_id: Mapped[Optional[str]] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
