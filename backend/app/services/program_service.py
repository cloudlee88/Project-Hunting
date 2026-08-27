import asyncio
import json
from datetime import datetime, timedelta
import re
from typing import List, Optional, Tuple
from sqlalchemy import select, func, delete, and_, asc, desc, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.logger import get_logger
from app.models import AffiliateProgram

log = get_logger("program_service")

_SORTABLE = {
    "name": AffiliateProgram.name,
    "source": AffiliateProgram.source,
    "category": AffiliateProgram.category,
    "commission_value": AffiliateProgram.commission_value,
    "traffic_score": AffiliateProgram.traffic_score,
    "domain_created_at": AffiliateProgram.domain_created_at,
    "domain_expires_at": AffiliateProgram.domain_expires_at,
    "launch_year": AffiliateProgram.launch_year,
    "crawled_at": AffiliateProgram.crawled_at,
    "updated_at": AffiliateProgram.updated_at,
}

_COOKIE_RE = re.compile(
    r"(\d+)\s*(ngày|ngay|tuần|tuan|tháng|thang|năm|nam|d|day|days|w|week|weeks|m|month|months|y|year|years|h|hour|hours)?",
    re.I,
)


def _parse_cookie_days(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    m = _COOKIE_RE.search(str(text))
    if not m:
        return None
    n = int(m.group(1))
    unit = (m.group(2) or "d").lower()
    if unit.startswith(("ngày", "ngay", "d")):
        return n
    if unit.startswith(("tuần", "tuan", "w")):
        return n * 7
    if unit.startswith("h"):
        return max(1, n // 24)
    if unit.startswith(("tháng", "thang", "m")):
        return n * 30
    if unit.startswith(("năm", "nam", "y")):
        return n * 365
    return n


def _latest_avg_duration(details_json: Optional[str]) -> Optional[int]:
    """Thời lượng TB (giây) của kỳ mới nhất trong traffic_details_json. None nếu chưa quét."""
    if not details_json:
        return None
    try:
        g = sorted((json.loads(details_json).get("global") or []),
                   key=lambda x: x.get("period_month") or "")
        return int(g[-1].get("avg_visit_duration") or 0) if g else None
    except Exception:
        return None


def _duration_in_ranges(p, ranges) -> bool:
    """Lọc theo thời lượng TB, chọn NHIỀU mốc. ranges: list "loSec:hiSec" (rỗng = mở đầu đó).
    Trả True nếu thời lượng TB nằm trong BẤT KỲ khoảng nào. Dự án chưa quét traffic → loại."""
    if not ranges:
        return True
    v = _latest_avg_duration(p.traffic_details_json)
    if v is None:
        return False
    for spec in ranges:
        try:
            lo_s, hi_s = str(spec).split(":")
        except ValueError:
            continue
        if lo_s.strip() and v < float(lo_s):
            continue
        if hi_s.strip() and v >= float(hi_s):
            continue
        return True
    return False


async def upsert_apdb_programs(rows: List[dict], session: AsyncSession) -> int:
    """APDB-specific upsert: dedup cross-source theo signup_url/url trước khi insert."""
    if not rows:
        return 0
    saved = 0
    skipped = 0
    for row in rows:
        signup_url = (row.get("signup_url") or "").strip()
        url = (row.get("url") or "").strip()

        # Kiểm tra trùng với nguồn khác (không phải apdb)
        if signup_url or url:
            conds = []
            if signup_url:
                conds.append(AffiliateProgram.signup_url == signup_url)
            if url:
                conds.append(AffiliateProgram.url == url)
            existing = (
                await session.execute(
                    select(AffiliateProgram.id)
                    .where(or_(*conds), AffiliateProgram.source != "apdb")
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped += 1
                continue

        row = dict(row)
        row.setdefault("crawled_at", datetime.utcnow())
        row["updated_at"] = datetime.utcnow()
        stmt = sqlite_insert(AffiliateProgram).values(**row)
        update_cols = {k: stmt.excluded[k] for k in row.keys() if k not in {"source", "external_id", "crawled_at"}}
        stmt = stmt.on_conflict_do_update(index_elements=["source", "external_id"], set_=update_cols)
        await session.execute(stmt)
        saved += 1

    await session.commit()
    log.info("APDB upsert: saved=%s skipped=%s (cross-source dedup)", saved, skipped)
    return saved


async def upsert_programs(rows: List[dict], session: AsyncSession) -> int:
    if not rows:
        return 0
    saved = 0
    for row in rows:
        row = dict(row)
        row.setdefault("crawled_at", datetime.utcnow())
        row["updated_at"] = datetime.utcnow()
        stmt = sqlite_insert(AffiliateProgram).values(**row)
        update_cols = {k: stmt.excluded[k] for k in row.keys() if k not in {"source", "external_id", "crawled_at"}}
        stmt = stmt.on_conflict_do_update(index_elements=["source", "external_id"], set_=update_cols)
        await session.execute(stmt)
        saved += 1
    await session.commit()
    return saved


def _build_where(
    source, category, search, min_commission, min_traffic=None,
    sources=None, categories=None,
    max_commission=None, has_traffic=None, has_signup=None,
    directory_status=None,
    networks=None, approval=None, registrations_open=None,
    payout_currency=None, payout_frequency=None,
    min_domain_age_years=None, max_domain_age_years=None, whois_state=None,
    traffic_state=None, min_launch_year=None, max_launch_year=None,
    sub_category=None, field=None, review_status=None, domain_age_ranges=None,
):
    conds = []
    if min_launch_year is not None:
        conds.append(AffiliateProgram.launch_year.isnot(None))
        conds.append(AffiliateProgram.launch_year >= min_launch_year)
    if max_launch_year is not None:
        conds.append(AffiliateProgram.launch_year.isnot(None))
        conds.append(AffiliateProgram.launch_year <= max_launch_year)
    # Trạng thái quét traffic: has (>0) | zero (đã quét, 0) | pending (chưa quét)
    if traffic_state == "has":
        conds.append(AffiliateProgram.traffic_score.isnot(None))
        conds.append(AffiliateProgram.traffic_score > 0)
    elif traffic_state == "zero":
        conds.append(AffiliateProgram.traffic_scanned_at.isnot(None))
        conds.append((AffiliateProgram.traffic_score.is_(None)) | (AffiliateProgram.traffic_score == 0))
    elif traffic_state == "pending":
        conds.append(AffiliateProgram.traffic_scanned_at.is_(None))
    if min_domain_age_years is not None and min_domain_age_years > 0:
        cutoff = datetime.utcnow() - timedelta(days=int(365.25 * min_domain_age_years))
        conds.append(AffiliateProgram.domain_created_at.isnot(None))
        conds.append(AffiliateProgram.domain_created_at <= cutoff)   # tuổi ≥ N năm
    if max_domain_age_years is not None and max_domain_age_years > 0:
        cutoff = datetime.utcnow() - timedelta(days=int(365.25 * max_domain_age_years))
        conds.append(AffiliateProgram.domain_created_at.isnot(None))
        conds.append(AffiliateProgram.domain_created_at > cutoff)    # tuổi < N năm (mới đăng ký)
    # Chọn NHIỀU mốc tuổi domain: mỗi mốc "loY:hiY" (năm; rỗng = mở đầu đó) → OR các khoảng.
    if domain_age_ranges:
        now = datetime.utcnow()
        ors = []
        for spec in domain_age_ranges:
            try:
                lo_s, hi_s = str(spec).split(":")
            except ValueError:
                continue
            rc = [AffiliateProgram.domain_created_at.isnot(None)]
            if lo_s.strip():
                rc.append(AffiliateProgram.domain_created_at <= now - timedelta(days=int(365.25 * float(lo_s))))  # tuổi ≥ lo
            if hi_s.strip():
                rc.append(AffiliateProgram.domain_created_at > now - timedelta(days=int(365.25 * float(hi_s))))   # tuổi < hi
            ors.append(and_(*rc))
        if ors:
            conds.append(or_(*ors))
    # Trạng thái quét WHOIS: có (ok) | không có (not_found) | chưa dò (null/error → cần quét)
    if whois_state == "found":
        conds.append(AffiliateProgram.domain_whois_status == "ok")
    elif whois_state == "not_found":
        conds.append(AffiliateProgram.domain_whois_status == "not_found")
    elif whois_state == "pending":
        conds.append((AffiliateProgram.domain_whois_status.is_(None)) | (AffiliateProgram.domain_whois_status == "error"))
    if source:
        conds.append(AffiliateProgram.source == source)
    if sources:
        conds.append(AffiliateProgram.source.in_(sources))
    if category:
        conds.append(AffiliateProgram.category == category)
    if categories:
        conds.append(AffiliateProgram.category.in_(categories))
    if sub_category:
        # Chấp nhận 1 giá trị (str) hoặc nhiều (list) — lọc IN để chọn nhiều sub-category.
        subs = sub_category if isinstance(sub_category, (list, tuple, set)) else [sub_category]
        subs = [s for s in subs if s]
        if subs:
            conds.append(AffiliateProgram.sub_category.in_(subs))
    if field:
        # Lĩnh vực (CRM/Accounting/HR…) — chấp nhận 1 giá trị hoặc nhiều, lọc IN.
        fields = field if isinstance(field, (list, tuple, set)) else [field]
        fields = [f for f in fields if f]
        if fields:
            conds.append(AffiliateProgram.field.in_(fields))
    if review_status:
        # Tình trạng đánh giá (Đạt / Bỏ / Theo dõi thêm) — chọn 1 giá trị.
        conds.append(AffiliateProgram.review_status == review_status)
    if search:
        like = f"%{search.lower()}%"
        conds.append(func.lower(AffiliateProgram.name).like(like))
    if min_commission is not None:
        conds.append(AffiliateProgram.commission_value >= min_commission)
    if max_commission is not None:
        conds.append(AffiliateProgram.commission_value <= max_commission)
    if min_traffic is not None:
        conds.append(AffiliateProgram.traffic_score >= min_traffic)
    if has_traffic is True:
        conds.append(AffiliateProgram.traffic_score.isnot(None))
        conds.append(AffiliateProgram.traffic_score > 0)
    elif has_traffic is False:
        conds.append((AffiliateProgram.traffic_score.is_(None)) | (AffiliateProgram.traffic_score == 0))
    if has_signup is True:
        conds.append(AffiliateProgram.signup_url.isnot(None))
        conds.append(AffiliateProgram.signup_url != "")
    elif has_signup is False:
        conds.append((AffiliateProgram.signup_url.is_(None)) | (AffiliateProgram.signup_url == ""))
    # directory_status: "active" → verified / active / auto-approve (KHÔNG match "unverified" lẫn)
    if directory_status == "active":
        ds = func.lower(AffiliateProgram.directory_status)
        active_vals = ["active", "active & verified", "verified", "auto-approve"]
        conds.append(ds.in_(active_vals) | ds.like("auto%") | ds.like("%active &%"))
    elif directory_status == "inactive":
        ds = func.lower(AffiliateProgram.directory_status)
        inactive_vals = ["inactive", "moved", "closed", "unverified", "manual approve", "manual"]
        conds.append(ds.in_(inactive_vals) | ds.like("manual%"))
    # Filter đặc thù — openaffiliate
    if networks:
        conds.append(func.lower(AffiliateProgram.directory_network).in_([n.lower() for n in networks]))
    if approval:
        conds.append(func.lower(AffiliateProgram.directory_approval) == approval.lower())
    # Filter đặc thù — goaffpro
    if registrations_open is True:
        conds.append(AffiliateProgram.registrations_open == 1)
    elif registrations_open is False:
        conds.append(AffiliateProgram.registrations_open == 0)
    if payout_currency:
        conds.append(func.upper(AffiliateProgram.payout_currency) == payout_currency.upper())
    if payout_frequency:
        conds.append(func.lower(AffiliateProgram.payout_frequency) == payout_frequency.lower())
    return and_(*conds) if conds else None


# ── Gộp trùng theo domain (giữ bản nhiều dữ liệu nhất) ────────────────────────
_DEDUPE_RICH_FIELDS = (
    "commission", "commission_value", "category", "payout", "cookie_duration",
    "directory_network", "payout_methods_json", "logo_url", "signup_url",
    "description", "traffic_score", "domain_created_at",
)


def _program_domain(p: AffiliateProgram) -> str:
    u = (p.url or p.signup_url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = u.split("/")[0].split("?")[0].split(":")[0]
    if u.startswith("www."):
        u = u[4:]
    return u


def _program_richness(p: AffiliateProgram) -> int:
    return sum(1 for f in _DEDUPE_RICH_FIELDS if getattr(p, f, None) not in (None, "", 0, 0.0))


def _dedupe_by_domain(rows: List[AffiliateProgram]) -> List[AffiliateProgram]:
    """Giữ 1 dòng/domain: bản NHIỀU DỮ LIỆU nhất; hoà → ưu tiên nguồn directory
    (không phải discovery/google_ads); hoà nữa → id nhỏ hơn (cũ hơn). Dòng không
    có domain thì giữ nguyên, không gộp."""
    best: dict = {}
    keep_no_domain: List[AffiliateProgram] = []
    for p in rows:
        dom = _program_domain(p)
        if not dom:
            keep_no_domain.append(p)
            continue
        rank = (_program_richness(p), 0 if p.source in ("discovery", "google_ads") else 1, -(p.id or 0))
        cur = best.get(dom)
        if cur is None or rank > cur[0]:
            best[dom] = (rank, p)
    return [v[1] for v in best.values()] + keep_no_domain


async def list_programs(
    session: AsyncSession,
    source: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    min_commission: Optional[float] = None,
    min_traffic: Optional[float] = None,
    min_cookie_days: Optional[int] = None,
    sources: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    max_commission: Optional[float] = None,
    has_traffic: Optional[bool] = None,
    has_signup: Optional[bool] = None,
    directory_status: Optional[str] = None,
    networks: Optional[List[str]] = None,
    approval: Optional[str] = None,
    registrations_open: Optional[bool] = None,
    payout_currency: Optional[str] = None,
    payout_frequency: Optional[str] = None,
    min_domain_age_years: Optional[float] = None,
    max_domain_age_years: Optional[float] = None,
    whois_state: Optional[str] = None,
    traffic_state: Optional[str] = None,
    min_launch_year: Optional[int] = None,
    max_launch_year: Optional[int] = None,
    sub_category: Optional[str] = None,
    field: Optional[str] = None,
    review_status: Optional[str] = None,
    domain_age_ranges: Optional[List[str]] = None,
    duration_ranges: Optional[List[str]] = None,
    dedupe_domain: bool = False,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "crawled_at",
    order: str = "desc",
) -> Tuple[List[AffiliateProgram], int]:
    where = _build_where(
        source, category, search, min_commission, min_traffic,
        sources, categories,
        max_commission=max_commission, has_traffic=has_traffic, has_signup=has_signup,
        directory_status=directory_status,
        networks=networks, approval=approval, registrations_open=registrations_open,
        payout_currency=payout_currency, payout_frequency=payout_frequency,
        min_domain_age_years=min_domain_age_years,
        max_domain_age_years=max_domain_age_years, whois_state=whois_state,
        traffic_state=traffic_state,
        min_launch_year=min_launch_year, max_launch_year=max_launch_year,
        sub_category=sub_category, field=field, review_status=review_status, domain_age_ranges=domain_age_ranges,
    )
    col = _SORTABLE.get(sort_by, AffiliateProgram.crawled_at)
    direction = asc if order == "asc" else desc

    # Đường Python (load hết rồi phân trang): cần khi lọc cookie (text), lọc thời lượng TB
    # (nằm trong traffic_details_json) HOẶC gộp trùng domain — không làm được bằng SQL thuần.
    _dur_filter = bool(duration_ranges)
    if (min_cookie_days and min_cookie_days > 0) or _dur_filter or dedupe_domain:
        q = select(AffiliateProgram)
        if where is not None:
            q = q.where(where)
        rows = list((await session.execute(q)).scalars().all())
        if min_cookie_days and min_cookie_days > 0:
            rows = [p for p in rows if (_parse_cookie_days(p.cookie_duration) or 0) >= min_cookie_days]
        if _dur_filter:
            rows = [p for p in rows if _duration_in_ranges(p, duration_ranges)]
        if dedupe_domain:
            rows = _dedupe_by_domain(rows)
        attr = col.key
        rev = order != "asc"
        rows.sort(key=lambda p: (p.id or 0), reverse=True)  # tiebreak ~ id desc (giống SQL)
        present = [p for p in rows if getattr(p, attr, None) is not None]
        absent = [p for p in rows if getattr(p, attr, None) is None]
        present.sort(key=lambda p: (lambda v: v.lower() if isinstance(v, str) else v)(getattr(p, attr)), reverse=rev)
        rows = present + absent  # None luôn xếp cuối
        total = len(rows)
        start = (page - 1) * page_size
        return rows[start:start + page_size], total

    count_q = select(func.count()).select_from(AffiliateProgram)
    if where is not None:
        count_q = count_q.where(where)
    total = (await session.execute(count_q)).scalar_one()

    q = select(AffiliateProgram).order_by(direction(col), AffiliateProgram.id.desc())
    if where is not None:
        q = q.where(where)
    q = q.offset((page - 1) * page_size).limit(page_size)
    items = (await session.execute(q)).scalars().all()
    return list(items), int(total)


async def all_programs(
    session: AsyncSession,
    source: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    min_commission: Optional[float] = None,
    min_traffic: Optional[float] = None,
    min_cookie_days: Optional[int] = None,
    ids: Optional[List[int]] = None,
    max_commission: Optional[float] = None,
    has_traffic: Optional[bool] = None,
    has_signup: Optional[bool] = None,
    directory_status: Optional[str] = None,
    networks: Optional[List[str]] = None,
    approval: Optional[str] = None,
    registrations_open: Optional[bool] = None,
    payout_currency: Optional[str] = None,
    payout_frequency: Optional[str] = None,
    traffic_state: Optional[str] = None,
    sub_category: Optional[str] = None,
    field: Optional[str] = None,
    review_status: Optional[str] = None,
    domain_age_ranges: Optional[List[str]] = None,
    duration_ranges: Optional[List[str]] = None,
) -> List[AffiliateProgram]:
    where = _build_where(
        source, category, search, min_commission, min_traffic,
        max_commission=max_commission, has_traffic=has_traffic, has_signup=has_signup,
        directory_status=directory_status,
        networks=networks, approval=approval, registrations_open=registrations_open,
        payout_currency=payout_currency, payout_frequency=payout_frequency,
        traffic_state=traffic_state, sub_category=sub_category, field=field, review_status=review_status, domain_age_ranges=domain_age_ranges,
    )
    q = select(AffiliateProgram).order_by(AffiliateProgram.crawled_at.desc())
    if where is not None:
        q = q.where(where)
    if ids:
        q = q.where(AffiliateProgram.id.in_(ids))
    rows = list((await session.execute(q)).scalars().all())
    if min_cookie_days and min_cookie_days > 0:
        rows = [p for p in rows if (_parse_cookie_days(p.cookie_duration) or 0) >= min_cookie_days]
    if duration_ranges:
        rows = [p for p in rows if _duration_in_ranges(p, duration_ranges)]
    return rows


async def list_program_ids(
    session: AsyncSession,
    source: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    min_commission: Optional[float] = None,
    max_commission: Optional[float] = None,
    min_traffic: Optional[float] = None,
    min_cookie_days: Optional[int] = None,
    has_traffic: Optional[bool] = None,
    has_signup: Optional[bool] = None,
    sources: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    directory_status: Optional[str] = None,
    networks: Optional[List[str]] = None,
    approval: Optional[str] = None,
    registrations_open: Optional[bool] = None,
    payout_currency: Optional[str] = None,
    payout_frequency: Optional[str] = None,
    min_launch_year: Optional[int] = None,
    max_launch_year: Optional[int] = None,
    sub_category: Optional[str] = None,
    field: Optional[str] = None,
    review_status: Optional[str] = None,
    domain_age_ranges: Optional[List[str]] = None,
    duration_ranges: Optional[List[str]] = None,
    dedupe_domain: bool = False,
    sort_by: str = "crawled_at",
    order: str = "desc",
) -> List[int]:
    """Trả về toàn bộ id khớp filter — dùng cho 'Chọn tất cả' phía FE.

    Không phân trang. ``min_cookie_days`` và thời lượng TB (trong JSON) phải post-filter
    trong Python.
    """
    where = _build_where(
        source, category, search, min_commission, min_traffic,
        sources, categories,
        max_commission=max_commission, has_traffic=has_traffic, has_signup=has_signup,
        directory_status=directory_status,
        networks=networks, approval=approval, registrations_open=registrations_open,
        payout_currency=payout_currency, payout_frequency=payout_frequency,
        min_launch_year=min_launch_year, max_launch_year=max_launch_year,
        sub_category=sub_category, field=field, review_status=review_status, domain_age_ranges=domain_age_ranges,
    )
    col = _SORTABLE.get(sort_by, AffiliateProgram.crawled_at)
    direction = asc if order == "asc" else desc

    # Gộp trùng domain / lọc thời lượng TB (trong JSON) → load full row.
    _dur_filter = bool(duration_ranges)
    if dedupe_domain or _dur_filter:
        q = select(AffiliateProgram)
        if where is not None:
            q = q.where(where)
        rows = list((await session.execute(q)).scalars().all())
        if min_cookie_days and min_cookie_days > 0:
            rows = [p for p in rows if (_parse_cookie_days(p.cookie_duration) or 0) >= min_cookie_days]
        if _dur_filter:
            rows = [p for p in rows if _duration_in_ranges(p, duration_ranges)]
        if dedupe_domain:
            rows = _dedupe_by_domain(rows)
        return [int(p.id) for p in rows]

    if min_cookie_days and min_cookie_days > 0:
        q = select(AffiliateProgram.id, AffiliateProgram.cookie_duration).order_by(
            direction(col), AffiliateProgram.id.desc()
        )
        if where is not None:
            q = q.where(where)
        rows = (await session.execute(q)).all()
        return [
            int(rid) for rid, cookie in rows
            if (_parse_cookie_days(cookie) or 0) >= min_cookie_days
        ]

    q = select(AffiliateProgram.id).order_by(direction(col), AffiliateProgram.id.desc())
    if where is not None:
        q = q.where(where)
    return [int(r) for r in (await session.execute(q)).scalars().all()]


async def list_categories(session: AsyncSession, source: Optional[str] = None) -> List[str]:
    q = select(AffiliateProgram.category).where(AffiliateProgram.category.isnot(None)).distinct()
    if source:
        q = q.where(AffiliateProgram.source == source)
    rows = (await session.execute(q)).scalars().all()
    return sorted([r for r in rows if r])


async def list_sub_categories(session: AsyncSession, source: Optional[str] = None) -> List[str]:
    """Danh sách distinct sub_category (ngách hẹp — vd 'AI Code assistant') cho dropdown."""
    q = select(AffiliateProgram.sub_category).where(AffiliateProgram.sub_category.isnot(None)).distinct()
    if source:
        q = q.where(AffiliateProgram.source == source)
    rows = (await session.execute(q)).scalars().all()
    return sorted([r for r in rows if r])


async def list_fields(session: AsyncSession, source: Optional[str] = None) -> List[str]:
    """Danh sách distinct field (lĩnh vực — vd 'CRM', 'Accounting', 'HR') cho dropdown."""
    q = select(AffiliateProgram.field).where(AffiliateProgram.field.isnot(None)).distinct()
    if source:
        q = q.where(AffiliateProgram.source == source)
    rows = (await session.execute(q)).scalars().all()
    return sorted([r for r in rows if r])


async def get_program(program_id: int, session: AsyncSession) -> Optional[AffiliateProgram]:
    return await session.get(AffiliateProgram, program_id)


async def delete_program(program_id: int, session: AsyncSession) -> bool:
    res = await session.execute(delete(AffiliateProgram).where(AffiliateProgram.id == program_id))
    await session.commit()
    return res.rowcount > 0


async def bulk_delete(ids: List[int], session: AsyncSession) -> int:
    if not ids:
        return 0
    res = await session.execute(delete(AffiliateProgram).where(AffiliateProgram.id.in_(ids)))
    await session.commit()
    return int(res.rowcount or 0)


async def discover_homepages_batch(
    session: AsyncSession,
    *,
    source: str = "lovable",
    limit: int = 200,
    concurrency: int = 5,
    method: str = "auto",
) -> dict:
    """Tìm trang chủ thực cho các programs có intermediate affiliate URL.

    method:
      - "auto"   : browser Google → browser Bing
      - "browser": CloakBrowser search (Google + Bing fallback, giải captcha tự động)

    Trả về {"total": N, "updated": M, "failed": K}.
    """
    from app.services.crawlers.homepage_finder import (
        is_intermediate_url, find_homepage, find_homepages_browser_batch
    )
    import logging
    log = logging.getLogger("program_service.homepage_discovery")

    q = (
        select(AffiliateProgram)
        .where(
            AffiliateProgram.source == source,
            AffiliateProgram.url.isnot(None),
        )
        .limit(limit)
    )
    rows = (await session.execute(q)).scalars().all()
    candidates = [p for p in rows if is_intermediate_url(p.url or "")]
    log.info("discover_homepages: source=%s, method=%s, candidates=%d", source, method, len(candidates))

    if not candidates:
        return {"total": 0, "updated": 0, "failed": 0}

    updated = 0
    failed = 0

    if method == "browser":
        # Single shared browser, sequential (tánh rate-limit)
        programs_input = [(p.name or "", p.url or "") for p in candidates]
        url_map = await find_homepages_browser_batch(programs_input, delay=2.0)
        for p in candidates:
            homepage = url_map.get(p.url or "")
            if homepage:
                p.url = homepage
                p.updated_at = datetime.utcnow()
                updated += 1
                log.info("Browser updated %r → %s", p.name, homepage)
            else:
                failed += 1
                log.warning("Browser: no homepage for %r", p.name)
        await session.commit()
    else:
        sem = asyncio.Semaphore(concurrency)

        async def _process(program: "AffiliateProgram") -> None:  # type: ignore[name-defined]
            nonlocal updated, failed
            try:
                async with sem:
                    homepage = await find_homepage(
                        program.name or "", program.url or "", method=method
                    )
                if homepage:
                    program.url = homepage
                    program.updated_at = datetime.utcnow()
                    updated += 1
                    log.info("Updated %r → %s", program.name, homepage)
                else:
                    failed += 1
                    log.warning("No homepage found for %r (%s)", program.name, program.url)
            except Exception as exc:
                failed += 1
                log.error("Error processing %r: %s", program.name, exc)

        await asyncio.gather(*(_process(p) for p in candidates))
        await session.commit()

    return {"total": len(candidates), "updated": updated, "failed": failed}


async def discover_single_homepage(
    session: AsyncSession,
    program_id: int,
    method: str = "auto",
) -> dict | None:
    """Tìm trang chủ cho một program cụ thể theo ID.

    Trả về {"id", "url", "found"} hoặc None nếu không tìm thấy program.
    """
    from app.services.crawlers.homepage_finder import find_homepage
    import logging
    log = logging.getLogger("program_service.homepage_discovery")

    prog = await session.get(AffiliateProgram, program_id)
    if not prog:
        return None

    homepage = await find_homepage(
        prog.name or "", prog.url or "", method=method
    )
    found = homepage is not None
    if found:
        prog.url = homepage
        prog.updated_at = datetime.utcnow()
        await session.commit()
        log.info("Single discover: %r [%d] → %s", prog.name, program_id, homepage)
    return {"id": program_id, "url": prog.url, "found": found}
