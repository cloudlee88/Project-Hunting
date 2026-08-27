from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Boolean, Integer, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.db import Base


class DiscoverySource(Base):
    __tablename__ = "discovery_sources"
    __table_args__ = (UniqueConstraint("user_id", "url", name="uq_disc_source_user_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255))
    category: Mapped[Optional[str]] = mapped_column(String(128))  # gán cho mọi dự án promote từ nguồn này
    field: Mapped[Optional[str]] = mapped_column(String(128))      # lĩnh vực (CRM/Accounting/HR…) — gán cho mọi dự án crawl/import từ nguồn này
    status: Mapped[str] = mapped_column(String(16), default="active")
    last_crawled_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    total_candidates_found: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DiscoveryCandidate(Base):
    __tablename__ = "discovery_candidates"
    __table_args__ = (UniqueConstraint("user_id", "domain", name="uq_disc_cand_user_domain"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    raw_url: Mapped[str] = mapped_column(String(500), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    detection_method: Mapped[Optional[str]] = mapped_column(String(32))
    suggested_name: Mapped[Optional[str]] = mapped_column(String(255))
    # Per-row override khi import CSV (cột category/sub_category). Rỗng → dùng
    # category/Tên gợi nhớ của nguồn. Chảy sang program khi promote (đồng bộ Chương trình).
    category: Mapped[Optional[str]] = mapped_column(String(128))
    sub_category: Mapped[Optional[str]] = mapped_column(String(255))
    field: Mapped[Optional[str]] = mapped_column(String(128))
    source_page_url: Mapped[Optional[str]] = mapped_column(String(500))
    source_page_title: Mapped[Optional[str]] = mapped_column(String(255))

    homepage_url: Mapped[Optional[str]] = mapped_column(String(500))
    is_redirect_resolved: Mapped[bool] = mapped_column(Boolean, default=False)

    traffic_monthly: Mapped[Optional[int]] = mapped_column(Integer)
    traffic_status: Mapped[Optional[str]] = mapped_column(String(16))

    affiliate_url: Mapped[Optional[str]] = mapped_column(String(500))
    affiliate_detection_method: Mapped[Optional[str]] = mapped_column(String(16))
    # Đánh giá link affiliate: "ok" | "dead" (404/soft-404) | None (chưa kiểm tra)
    affiliate_url_status: Mapped[Optional[str]] = mapped_column(String(16))

    # Google Ads: thông tin hiển thị của quảng cáo (khi candidate đến từ export Google Ads)
    ad_days_shown: Mapped[Optional[int]] = mapped_column(Integer)       # số ngày hiển thị
    ad_first_shown: Mapped[Optional[datetime]] = mapped_column(DateTime)  # lần đầu chạy
    ad_last_shown: Mapped[Optional[datetime]] = mapped_column(DateTime)   # lần cuối chạy

    status: Mapped[str] = mapped_column(String(32), default="discovered", index=True)
    promoted_program_id: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class DomainBlacklist(Base):
    __tablename__ = "domain_blacklist"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
