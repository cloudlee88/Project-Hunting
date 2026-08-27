"""PlatformQAEntry — kho Q&A cho từng affiliate platform."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PlatformQAEntry(Base):
    __tablename__ = "platform_qa_library"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(64))
    program_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    # Pattern matching
    question_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    question_hash: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # Answer
    answer_template: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # "profile_field" | "static" | "template" | "select_option"
    answer_type: Mapped[str] = mapped_column(String(32), nullable=False, default="static")

    required: Mapped[bool] = mapped_column(Boolean, default=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.95)

    # Usage stats
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)

    # State: "active" | "needs_review" | "archived"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    # "manual" | "ai_generated" | "seed"
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")

    note: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ScriptLearningLog(Base):
    __tablename__ = "script_learning_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    program_id: Mapped[Optional[int]] = mapped_column(Integer)
    platform: Mapped[Optional[str]] = mapped_column(String(64))
    category: Mapped[Optional[str]] = mapped_column(String(64))

    # "qa_matched" | "qa_new" | "qa_failed" | "selector_fixed" | "full_ai_learned"
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)

    question: Mapped[Optional[str]] = mapped_column(Text)
    answer_used: Mapped[Optional[str]] = mapped_column(Text)
    qa_library_id: Mapped[Optional[int]] = mapped_column(Integer)

    selector_old: Mapped[Optional[str]] = mapped_column(Text)
    selector_new: Mapped[Optional[str]] = mapped_column(Text)

    tier_used: Mapped[Optional[int]] = mapped_column(Integer)  # 1 | 2 | 3
    token_cost: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    playbook_used_id: Mapped[Optional[int]] = mapped_column(Integer)
    playbook_fallback: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)

    # "success" | "failed" | "pending_review"
    outcome: Mapped[Optional[str]] = mapped_column(String(32))

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
