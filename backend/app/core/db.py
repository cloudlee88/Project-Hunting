from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event
from sqlalchemy.engine import Engine
from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=False, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# SQLite mặc định KHÔNG enforce foreign keys → bật ON cho mọi connection
# để ON DELETE CASCADE hoạt động (xoá shortlist sẽ xoá items kèm theo).
@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_fk(dbapi_conn, _):
    try:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:
        pass


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    # Ensure data dirs exist
    for sub in ("profiles", "instructions", "screenshots"):
        settings.data_path(sub).mkdir(parents=True, exist_ok=True)
    # Import models so metadata is registered
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        renamed = await _rename_legacy_discovery_tables(conn)
        await conn.run_sync(Base.metadata.create_all)
        await _restore_legacy_discovery_tables(conn, renamed)
        # Lightweight auto-migration (v1 không dùng Alembic). Thêm cột nếu thiếu.
        await _ensure_column(conn, "users", "role", "VARCHAR(16) DEFAULT 'user'")
        # Mặc định 1 cho các user đã tồn tại từ trước; user đăng ký mới ghi 0 tường minh.
        await _ensure_column(conn, "users", "is_active", "BOOLEAN DEFAULT 1")
        await _ensure_column(conn, "crawl_jobs", "params_json", "TEXT")
        await _ensure_column(conn, "crawl_jobs", "user_id", "INTEGER")
        await _ensure_column(conn, "affiliate_programs", "traffic_score", "REAL")
        await _ensure_column(conn, "affiliate_programs", "traffic_period_month", "VARCHAR(7)")
        await _ensure_column(conn, "affiliate_programs", "traffic_details_json", "TEXT")
        await _ensure_column(conn, "affiliate_programs", "traffic_scanned_at", "DATETIME")
        await _ensure_column(conn, "affiliate_programs", "sms_country_id", "VARCHAR(16)")
        await _ensure_column(conn, "affiliate_programs", "sms_service_id", "VARCHAR(16)")
        await _ensure_column(conn, "affiliate_programs", "sms_profile_id", "VARCHAR(64)")
        await _ensure_column(conn, "affiliate_programs", "directory_traffic", "VARCHAR(64)")
        await _ensure_column(conn, "affiliate_programs", "directory_popularity", "VARCHAR(64)")
        await _ensure_column(conn, "affiliate_programs", "directory_status", "VARCHAR(64)")
        await _ensure_column(conn, "affiliate_programs", "logo_url", "VARCHAR(500)")
        await _ensure_column(conn, "affiliate_programs", "short_description", "VARCHAR(500)")
        await _ensure_column(conn, "affiliate_programs", "directory_network", "VARCHAR(64)")
        await _ensure_column(conn, "affiliate_programs", "directory_approval", "VARCHAR(32)")
        await _ensure_column(conn, "affiliate_programs", "directory_approval_time", "VARCHAR(32)")
        await _ensure_column(conn, "affiliate_programs", "directory_attribution", "VARCHAR(32)")
        await _ensure_column(conn, "affiliate_programs", "directory_tracking", "VARCHAR(32)")
        await _ensure_column(conn, "affiliate_programs", "directory_last_verified_at", "VARCHAR(32)")
        await _ensure_column(conn, "affiliate_programs", "directory_program_age", "VARCHAR(32)")
        await _ensure_column(conn, "affiliate_programs", "payout_min", "REAL")
        await _ensure_column(conn, "affiliate_programs", "payout_currency", "VARCHAR(16)")
        await _ensure_column(conn, "affiliate_programs", "payout_frequency", "VARCHAR(32)")
        await _ensure_column(conn, "affiliate_programs", "payout_methods_json", "TEXT")
        await _ensure_column(conn, "affiliate_programs", "commission_duration", "VARCHAR(64)")
        await _ensure_column(conn, "affiliate_programs", "commission_conditions", "TEXT")
        await _ensure_column(conn, "affiliate_programs", "restrictions_json", "TEXT")
        await _ensure_column(conn, "affiliate_programs", "agents_json", "TEXT")
        await _ensure_column(conn, "affiliate_programs", "registrations_open", "INTEGER")
        await _ensure_column(conn, "signup_jobs", "sms_profile_id", "VARCHAR(64)")
        await _ensure_column(conn, "signup_jobs", "gemini_key_index", "INTEGER")
        await _ensure_column(conn, "signup_jobs", "llm_provider", "VARCHAR(16)")
        await _ensure_column(conn, "signup_jobs", "llm_key_index", "INTEGER")
        await _ensure_column(conn, "signup_jobs", "run_mode", "VARCHAR(16)")
        await _ensure_column(conn, "signup_jobs", "playbook_id", "INTEGER")
        await _ensure_column(conn, "signup_jobs", "batch_id", "VARCHAR(36)")
        await _ensure_column(conn, "signup_jobs", "script_overrides_json", "TEXT")
        await _ensure_column(conn, "signup_jobs", "tier3_behavior", "VARCHAR(16)")
        await _ensure_column(conn, "signup_jobs", "tier3_status", "VARCHAR(32)")
        await _ensure_column(conn, "ads_search_history", "results_json", "TEXT")
        # signup_playbooks — Q&A / Learning columns
        await _ensure_column(conn, "signup_playbooks", "source", "TEXT")
        await _ensure_column(conn, "signup_playbooks", "version", "INTEGER")
        await _ensure_column(conn, "signup_playbooks", "pending_review", "BOOLEAN")
        await _ensure_column(conn, "signup_playbooks", "review_session_id", "TEXT")
        await _ensure_column(conn, "script_learning_log", "playbook_used_id", "INTEGER")
        await _ensure_column(conn, "script_learning_log", "playbook_fallback", "BOOLEAN")
        await _ensure_column(conn, "discovery_sources", "category", "VARCHAR(128)")
        await _ensure_column(conn, "discovery_candidates", "affiliate_url_status", "VARCHAR(16)")
        await _ensure_column(conn, "discovery_candidates", "ad_days_shown", "INTEGER")
        await _ensure_column(conn, "discovery_candidates", "ad_first_shown", "DATETIME")
        await _ensure_column(conn, "discovery_candidates", "ad_last_shown", "DATETIME")
        await _ensure_column(conn, "discovery_candidates", "category", "VARCHAR(128)")
        await _ensure_column(conn, "discovery_candidates", "sub_category", "VARCHAR(255)")
        await _ensure_column(conn, "discovery_candidates", "field", "VARCHAR(128)")
        await _ensure_column(conn, "discovery_sources", "field", "VARCHAR(128)")
        await _ensure_column(conn, "affiliate_programs", "field", "VARCHAR(128)")
        # WHOIS (RDAP) — ngày tạo/hết hạn domain cho màn Chương trình
        await _ensure_column(conn, "affiliate_programs", "domain_created_at", "DATETIME")
        await _ensure_column(conn, "affiliate_programs", "domain_expires_at", "DATETIME")
        await _ensure_column(conn, "affiliate_programs", "domain_whois_scanned_at", "DATETIME")
        await _ensure_column(conn, "affiliate_programs", "domain_whois_status", "VARCHAR(16)")
        await _ensure_column(conn, "affiliate_programs", "launch_year", "INTEGER")
        await _ensure_column(conn, "affiliate_programs", "sub_category", "VARCHAR(128)")
        await _ensure_column(conn, "affiliate_programs", "ad_days_shown", "INTEGER")
        await _ensure_column(conn, "affiliate_programs", "ad_first_shown", "DATETIME")
        await _ensure_column(conn, "affiliate_programs", "ad_last_shown", "DATETIME")
        await _ensure_column(conn, "affiliate_programs", "ads_advertisers_count", "INTEGER")
        await _ensure_column(conn, "affiliate_programs", "ads_advertisers_scanned_at", "DATETIME")
        await _ensure_column(conn, "affiliate_programs", "ads_advertisers_json", "TEXT")
        await _ensure_column(conn, "traffic_scan_jobs", "kind", "VARCHAR(16)")
        await _ensure_column(conn, "traffic_scan_jobs", "start_date", "VARCHAR(8)")
        await _ensure_column(conn, "traffic_scan_jobs", "end_date", "VARCHAR(8)")
    # Seed domain_blacklist nếu trống
    await _seed_domain_blacklist()


async def _seed_domain_blacklist() -> None:
    from sqlalchemy import text
    SEED_DOMAINS = [
        ("facebook.com", "social"), ("twitter.com", "social"), ("x.com", "social"),
        ("instagram.com", "social"), ("linkedin.com", "social"), ("youtube.com", "social"),
        ("tiktok.com", "social"), ("pinterest.com", "social"), ("reddit.com", "social"),
        ("t.me", "social"), ("telegram.org", "social"), ("whatsapp.com", "social"),
        ("api.whatsapp.com", "social"),
        ("google.com", "infra"), ("googleapis.com", "infra"), ("gstatic.com", "infra"),
        ("cloudflare.com", "infra"),
        ("wp.com", "infra"), ("wordpress.com", "infra"), ("gravatar.com", "infra"),
        ("amazon.com", "ecommerce"), ("ebay.com", "ecommerce"),
        ("apps.apple.com", "ecommerce"), ("play.google.com", "ecommerce"),
        ("doubleclick.net", "ads"), ("googlesyndication.com", "ads"),
    ]
    async with SessionLocal() as s:
        existing = {r[0] for r in (await s.execute(text("SELECT domain FROM domain_blacklist"))).fetchall()}
        if existing:
            return
        now = __import__("datetime").datetime.utcnow().isoformat()
        for domain, category in SEED_DOMAINS:
            await s.execute(
                text("INSERT OR IGNORE INTO domain_blacklist (domain, category, created_at) VALUES (:d, :c, :n)"),
                {"d": domain, "c": category, "n": now},
            )
        await s.commit()


# Bảng discovery đời đầu unique theo url/domain TOÀN CỤC (chưa có khái niệm chủ sở hữu).
# Nay unique theo (user_id, …) để 2 user quét trùng nguồn vẫn ra đủ dự án. SQLite không
# DROP được constraint → phải dựng lại bảng: đổi tên bảng cũ, create_all tạo bảng mới,
# copy dữ liệu sang rồi xoá bảng cũ. Tất cả nằm trong 1 transaction của init_db.
# Dấu hiệu bảng đời cũ = chưa có cột user_id (không soi được constraint vì unique
# của `domain` do SQLAlchemy render thành CREATE UNIQUE INDEX, không nằm trong CREATE TABLE).
_LEGACY_DISCOVERY_TABLES = ("discovery_sources", "discovery_candidates")


async def _rename_legacy_discovery_tables(conn) -> list[str]:
    from sqlalchemy import text
    renamed: list[str] = []
    for table in _LEGACY_DISCOVERY_TABLES:
        cols = {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()}
        if not cols or "user_id" in cols:
            continue
        # Index đi theo bảng khi RENAME và giữ nguyên tên → create_all sẽ đụng tên.
        # `sql IS NOT NULL` để bỏ qua auto-index của UNIQUE (không DROP được).
        idx_names = (await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name = :t AND sql IS NOT NULL"
        ), {"t": table})).scalars().all()
        for name in idx_names:
            await conn.execute(text(f"DROP INDEX {name}"))
        await conn.execute(text(f"ALTER TABLE {table} RENAME TO {table}_legacy"))
        renamed.append(table)
    return renamed


async def _restore_legacy_discovery_tables(conn, renamed: list[str]) -> None:
    from sqlalchemy import text
    for table in renamed:
        old_cols = {r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table}_legacy)"))).fetchall()}
        new_cols = [r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()]
        shared = ", ".join(c for c in new_cols if c in old_cols)
        await conn.execute(text(f"INSERT INTO {table} ({shared}) SELECT {shared} FROM {table}_legacy"))
        await conn.execute(text(f"DROP TABLE {table}_legacy"))


async def backfill_discovery_owner(admin_user_id: int) -> int:
    """Gán dữ liệu discovery chưa có chủ (từ thời chưa phân quyền) cho admin."""
    from sqlalchemy import text
    moved = 0
    async with engine.begin() as conn:
        for table in ("discovery_sources", "discovery_candidates"):
            r = await conn.execute(
                text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"),
                {"uid": admin_user_id},
            )
            moved += r.rowcount or 0
    return moved


async def _ensure_column(conn, table: str, column: str, ddl_type: str) -> None:
    from sqlalchemy import text
    res = await conn.execute(text(f"PRAGMA table_info({table})"))
    cols = {row[1] for row in res.fetchall()}
    if column not in cols:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
