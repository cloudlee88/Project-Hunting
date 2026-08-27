import asyncio
import csv
import io
import logging
import json as _json
from datetime import datetime as _dt
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.config import settings, read_env_value
from app.deps import get_current_user
from app.schemas.program import ProgramListOut, ProgramOut
from app.services import program_service, traffic_runner
from app.services.traffic import scan_traffic
from app.services.whois import scan_whois
from app.models.traffic_scan_job import TrafficScanJob
from app.models import AffiliateProgram
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/programs", tags=["programs"], dependencies=[Depends(get_current_user)])


class BulkDeleteIn(BaseModel):
    ids: List[int]


class BulkScanTrafficIn(BaseModel):
    ids: List[int]
    skip_existing: bool = True
    months: int = 3          # 1..12 — số tháng dữ liệu cần lấy (mặc định 3 tháng)
    concurrency: int = 2     # 1..4 — chạy song song


class BulkScanAdvertisersIn(BaseModel):
    ids: List[int]
    start_date: str = ""     # YYYYMMDD — rỗng = mọi thời gian
    end_date: str = ""       # YYYYMMDD
    skip_existing: bool = False  # mặc định quét lại (user chọn khung ngày mỗi lần)
    concurrency: int = 3


class SmsPresetIn(BaseModel):
    sms_country_id: str = ""   # rỗng = dùng default từ .env
    sms_service_id: str = ""
    sms_profile_id: str = ""   # ưu tiên hơn nếu set → resolve country/service từ profile


@router.get("", response_model=ProgramListOut)
async def list_programs(
    source: Optional[str] = None,
    category: Optional[str] = None,
    sub_category: Optional[List[str]] = Query(None),
    field: Optional[List[str]] = Query(None),
    search: Optional[str] = None,
    min_commission: Optional[float] = None,
    max_commission: Optional[float] = None,
    min_traffic: Optional[float] = None,
    min_cookie_days: Optional[int] = Query(None, ge=0),
    has_traffic: Optional[bool] = None,
    has_signup: Optional[bool] = None,
    sources: Optional[List[str]] = Query(None),
    categories: Optional[List[str]] = Query(None),
    directory_status: Optional[str] = Query(None, description="active|inactive"),
    networks: Optional[List[str]] = Query(None, description="openaffiliate: in-house|cj|impact|awin|partnerstack…"),
    approval: Optional[str] = Query(None, description="auto|manual"),
    registrations_open: Optional[bool] = Query(None, description="goaffpro: chỉ store còn mở đăng ký"),
    payout_currency: Optional[str] = Query(None),
    payout_frequency: Optional[str] = Query(None, description="monthly|weekly|net30…"),
    min_domain_age_years: Optional[float] = Query(None, ge=0, description="chỉ hiện domain ≥ N năm tuổi"),
    max_domain_age_years: Optional[float] = Query(None, ge=0, description="chỉ hiện domain < N năm tuổi (mới đăng ký)"),
    whois_state: Optional[str] = Query(None, description="pending|found|not_found — trạng thái quét WHOIS"),
    traffic_state: Optional[str] = Query(None, description="has|zero|pending — trạng thái quét traffic"),
    min_launch_year: Optional[int] = Query(None, description="năm ra mắt sản phẩm >= (nếu có)"),
    max_launch_year: Optional[int] = Query(None, description="năm ra mắt sản phẩm <= (nếu có)"),
    domain_age_ranges: Optional[List[str]] = Query(None, description="mốc tuổi domain loY:hiY (chọn nhiều)"),
    duration_ranges: Optional[List[str]] = Query(None, description="mốc thời lượng TB loSec:hiSec (chọn nhiều)"),
    dedupe_domain: bool = Query(False, description="gộp trùng: mỗi domain chỉ 1 dòng (bản nhiều dữ liệu nhất)"),
    sort_by: str = "crawled_at",
    order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50000),
    session: AsyncSession = Depends(get_session),
):
    items, total = await program_service.list_programs(
        session, source=source, category=category, sub_category=sub_category, field=field, search=search,
        min_commission=min_commission, max_commission=max_commission,
        min_traffic=min_traffic, min_cookie_days=min_cookie_days,
        has_traffic=has_traffic, has_signup=has_signup,
        sources=sources, categories=categories,
        directory_status=directory_status,
        networks=networks, approval=approval, registrations_open=registrations_open,
        payout_currency=payout_currency, payout_frequency=payout_frequency,
        min_domain_age_years=min_domain_age_years,
        max_domain_age_years=max_domain_age_years, whois_state=whois_state,
        traffic_state=traffic_state,
        min_launch_year=min_launch_year, max_launch_year=max_launch_year,
        domain_age_ranges=domain_age_ranges, duration_ranges=duration_ranges,
        dedupe_domain=dedupe_domain,
        page=page, page_size=page_size,
        sort_by=sort_by, order=order,
    )
    return ProgramListOut(
        items=[ProgramOut.model_validate(i) for i in items],
        total=total, page=page, page_size=page_size,
    )


@router.get("/categories", response_model=List[str])
async def list_categories(source: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    return await program_service.list_categories(session, source=source)


@router.get("/sub-categories", response_model=List[str])
async def list_sub_categories(source: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    return await program_service.list_sub_categories(session, source=source)


@router.get("/fields", response_model=List[str])
async def list_fields(source: Optional[str] = None, session: AsyncSession = Depends(get_session)):
    return await program_service.list_fields(session, source=source)


@router.get("/facets")
async def list_facets(
    source: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Trả về danh sách distinct value cho các filter đặc thù theo source.

    Dùng cho FE render dropdown động (network/currency/frequency/status…).
    """
    from sqlalchemy import select as _select
    q_base = _select
    cond = (AffiliateProgram.source == source) if source else None

    async def _distinct(col):
        q = _select(col).where(col.isnot(None)).distinct()
        if cond is not None:
            q = q.where(cond)
        rows = (await session.execute(q)).scalars().all()
        return sorted({(r or "").strip() for r in rows if r and str(r).strip()})

    networks = await _distinct(AffiliateProgram.directory_network)
    currencies = await _distinct(AffiliateProgram.payout_currency)
    frequencies = await _distinct(AffiliateProgram.payout_frequency)
    statuses = await _distinct(AffiliateProgram.directory_status)
    approvals = await _distinct(AffiliateProgram.directory_approval)
    return {
        "networks": networks,
        "currencies": currencies,
        "frequencies": frequencies,
        "statuses": statuses,
        "approvals": approvals,
    }


@router.get("/ids", response_model=List[int])
async def list_program_ids(
    source: Optional[str] = None,
    category: Optional[str] = None,
    sub_category: Optional[List[str]] = Query(None),
    field: Optional[List[str]] = Query(None),
    search: Optional[str] = None,
    min_commission: Optional[float] = None,
    max_commission: Optional[float] = None,
    min_traffic: Optional[float] = None,
    min_cookie_days: Optional[int] = Query(None, ge=0),
    has_traffic: Optional[bool] = None,
    has_signup: Optional[bool] = None,
    sources: Optional[List[str]] = Query(None),
    categories: Optional[List[str]] = Query(None),
    directory_status: Optional[str] = Query(None),
    networks: Optional[List[str]] = Query(None),
    approval: Optional[str] = Query(None),
    registrations_open: Optional[bool] = Query(None),
    payout_currency: Optional[str] = Query(None),
    payout_frequency: Optional[str] = Query(None),
    min_launch_year: Optional[int] = Query(None),
    max_launch_year: Optional[int] = Query(None),
    domain_age_ranges: Optional[List[str]] = Query(None),
    duration_ranges: Optional[List[str]] = Query(None),
    dedupe_domain: bool = Query(False),
    sort_by: str = "crawled_at",
    order: str = "desc",
    session: AsyncSession = Depends(get_session),
):
    """Trả về id của toàn bộ program khớp filter — dùng cho 'Chọn tất cả' FE."""
    return await program_service.list_program_ids(
        session, source=source, category=category, sub_category=sub_category, field=field, search=search,
        min_commission=min_commission, max_commission=max_commission,
        min_traffic=min_traffic, min_cookie_days=min_cookie_days,
        has_traffic=has_traffic, has_signup=has_signup,
        sources=sources, categories=categories,
        directory_status=directory_status,
        networks=networks, approval=approval, registrations_open=registrations_open,
        payout_currency=payout_currency, payout_frequency=payout_frequency,
        min_launch_year=min_launch_year, max_launch_year=max_launch_year,
        domain_age_ranges=domain_age_ranges, duration_ranges=duration_ranges,
        dedupe_domain=dedupe_domain,
        sort_by=sort_by, order=order,
    )


def _traffic_flat(details_json: Optional[str]) -> dict:
    """Trích các số phẳng từ traffic_details_json để xuất CSV (khớp cột trên màn Chương trình):
    pages/visit, thời lượng TB (giây), tỷ lệ thoát, diễn biến (tổng truy cập 3 kỳ), top quốc gia."""
    out = {"pages_per_visit": "", "avg_visit_duration": "", "bounce_rate": "", "trend": "", "top_countries": ""}
    if not details_json:
        return out
    try:
        d = _json.loads(details_json)
    except Exception:
        return out
    g = sorted((d.get("global") or []), key=lambda x: x.get("period_month") or "")
    if g:
        latest = g[-1]
        out["pages_per_visit"] = latest.get("pages_per_visit", "")
        out["avg_visit_duration"] = latest.get("avg_visit_duration", "")
        out["bounce_rate"] = latest.get("bounce_rate_percentage", "")
        out["trend"] = " → ".join(str(int(x.get("total_visits_monthly") or 0)) for x in g)
    cs = (d.get("country") or [])[:5]
    out["top_countries"] = "; ".join(
        f"{c.get('country_code','')} {round(c.get('traffic_share_percentage') or 0, 1)}%" for c in cs
    )
    return out


@router.get("/export.csv")
async def export_csv(
    source: Optional[str] = None,
    category: Optional[str] = None,
    sub_category: Optional[List[str]] = Query(None),
    field: Optional[List[str]] = Query(None),
    search: Optional[str] = None,
    min_commission: Optional[float] = None,
    max_commission: Optional[float] = None,
    min_traffic: Optional[float] = None,
    min_cookie_days: Optional[int] = Query(None, ge=0),
    has_traffic: Optional[bool] = None,
    has_signup: Optional[bool] = None,
    networks: Optional[List[str]] = Query(None),
    traffic_state: Optional[str] = Query(None, description="has|zero|pending"),
    domain_age_ranges: Optional[List[str]] = Query(None),
    duration_ranges: Optional[List[str]] = Query(None),
    ids: Optional[str] = Query(None, description="Comma-separated id list"),
    session: AsyncSession = Depends(get_session),
):
    id_list = None
    if ids:
        try:
            id_list = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(400, "ids phải là danh sách số")
    rows = await program_service.all_programs(
        session, source=source, category=category, sub_category=sub_category, field=field, search=search,
        min_commission=min_commission, max_commission=max_commission,
        min_traffic=min_traffic, min_cookie_days=min_cookie_days,
        domain_age_ranges=domain_age_ranges, duration_ranges=duration_ranges,
        has_traffic=has_traffic, has_signup=has_signup,
        networks=networks, traffic_state=traffic_state,
        ids=id_list,
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "source", "external_id", "name", "category", "sub_category", "field",
        "commission", "commission_value", "commission_type",
        "payout", "payment_methods", "cookie_duration",
        "traffic_score", "traffic_period", "pages_per_visit", "avg_visit_duration_sec",
        "bounce_rate_pct", "traffic_trend", "top_countries", "ads_advertisers_count",
        "domain_created_at", "domain_expires_at", "launch_year",
        "ad_days_shown", "ad_first_shown", "ad_last_shown",
        "url", "signup_url",
        "description", "source_url", "crawled_at",
    ])
    for p in rows:
        try:
            _pm = "; ".join(_json.loads(p.payout_methods_json)) if p.payout_methods_json else ""
        except Exception:
            _pm = ""
        _tf = _traffic_flat(p.traffic_details_json)
        writer.writerow([
            p.id, p.source, p.external_id, p.name, p.category or "", p.sub_category or "", p.field or "",
            p.commission or "", p.commission_value or "", p.commission_type or "",
            p.payout or "", _pm, p.cookie_duration or "",
            int(p.traffic_score) if p.traffic_score else "",
            p.traffic_period_month or "",
            _tf["pages_per_visit"], _tf["avg_visit_duration"], _tf["bounce_rate"],
            _tf["trend"], _tf["top_countries"],
            int(p.ads_advertisers_count) if p.ads_advertisers_count is not None else "",
            p.domain_created_at.strftime("%Y-%m-%d") if p.domain_created_at else "",
            p.domain_expires_at.strftime("%Y-%m-%d") if p.domain_expires_at else "",
            int(p.launch_year) if p.launch_year else "",
            int(p.ad_days_shown) if p.ad_days_shown else "",
            p.ad_first_shown.strftime("%Y-%m-%d") if p.ad_first_shown else "",
            p.ad_last_shown.strftime("%Y-%m-%d") if p.ad_last_shown else "",
            p.url or "", p.signup_url or "",
            (p.description or "").replace("\n", " "), p.source_url or "",
            p.crawled_at.isoformat() if p.crawled_at else "",
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="programs.csv"'},
    )


@router.post("/import.csv")
async def import_csv(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Import program từ CSV. Format phải đồng nhất với /export.csv.

    - Cột bắt buộc: `source`, `external_id`, `name`.
    - Upsert theo (source, external_id) — đã tồn tại sẽ update.
    - Cột `id`, `crawled_at`, `updated_at` bị bỏ qua (server tự set).
    - Cột thiếu → để trống/NULL (backward compatible).
    """
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "File phải có đuôi .csv")
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(400, "CSV rỗng hoặc không có header")

    rows: List[dict] = []
    errors: List[dict] = []
    for idx, raw_row in enumerate(reader, start=2):  # row 1 = header
        row = {(k or "").strip(): (v or "").strip() for k, v in raw_row.items() if k}
        source = row.get("source")
        external_id = row.get("external_id")
        name = row.get("name")
        if not source or not external_id or not name:
            errors.append({"row": idx, "error": "thiếu source/external_id/name"})
            continue

        def _f(key: str) -> Optional[float]:
            v = row.get(key)
            if not v:
                return None
            try:
                return float(v)
            except ValueError:
                return None

        def _s(key: str) -> Optional[str]:
            v = row.get(key)
            return v if v else None

        rows.append({
            "source": source,
            "external_id": external_id,
            "name": name,
            "category": _s("category"),
            "commission": _s("commission"),
            "commission_value": _f("commission_value"),
            "commission_type": _s("commission_type"),
            "payout": _s("payout"),
            "cookie_duration": _s("cookie_duration"),
            "traffic_score": _f("traffic_score"),
            "url": _s("url"),
            "signup_url": _s("signup_url"),
            "description": _s("description"),
            "source_url": _s("source_url"),
        })

    saved = await program_service.upsert_programs(rows, session) if rows else 0
    return {"saved": saved, "skipped": len(errors), "errors": errors[:50]}


@router.post("/bulk-delete")
async def bulk_delete(body: BulkDeleteIn, session: AsyncSession = Depends(get_session)):
    n = await program_service.bulk_delete(body.ids, session)
    return {"deleted": n}


@router.patch("/{program_id}/sms-preset", response_model=ProgramOut)
async def update_sms_preset(
    program_id: int,
    body: SmsPresetIn,
    session: AsyncSession = Depends(get_session),
):
    """Lưu cấu hình SMS OTP riêng cho program này (rỗng = dùng default từ .env)."""
    p = await program_service.get_program(program_id, session)
    if not p:
        raise HTTPException(404, "Không tìm thấy program")
    p.sms_country_id = body.sms_country_id or None
    p.sms_service_id = body.sms_service_id or None
    p.sms_profile_id = body.sms_profile_id or None
    await session.commit()
    await session.refresh(p)
    return p


@router.get("/{program_id}", response_model=ProgramOut)
async def get_program(program_id: int, session: AsyncSession = Depends(get_session)):
    p = await program_service.get_program(program_id, session)
    if not p:
        raise HTTPException(404, "Không tìm thấy program")
    return p


@router.delete("/{program_id}")
async def delete_program(program_id: int, session: AsyncSession = Depends(get_session)):
    ok = await program_service.delete_program(program_id, session)
    if not ok:
        raise HTTPException(404, "Không tìm thấy program")
    return {"ok": True}


@router.post("/{program_id}/scan-traffic")
async def scan_program_traffic(program_id: int, session: AsyncSession = Depends(get_session)):
    """Quét traffic từ SimilarWeb cho program → update traffic_score."""
    p = await program_service.get_program(program_id, session)
    if not p:
        raise HTTPException(404, "Không tìm thấy program")
    url = p.url or p.signup_url or p.source_url
    if not url:
        raise HTTPException(400, "Program không có URL để quét")
    try:
        result = await scan_traffic(url)
    except RuntimeError as e:
        raise HTTPException(500, f"Quét traffic thất bại: {e}")
    import json as _json
    from datetime import datetime as _dt
    visits = int(result.get("monthly_visits") or 0)
    p.traffic_score = float(visits)
    p.traffic_period_month = result.get("period_month")
    details = result.get("traffic_details")
    p.traffic_details_json = _json.dumps(details, ensure_ascii=False) if details else None
    p.traffic_scanned_at = _dt.utcnow()
    await session.commit()
    await session.refresh(p)
    return {
        "program_id": program_id,
        "url": url,
        "domain": result.get("domain"),
        "monthly_visits": visits,
        "period_month": result.get("period_month"),
        "found": result.get("found", False),
        "traffic_score": p.traffic_score,
        "has_details": bool(details),
    }


@router.post("/{program_id}/scan-whois")
async def scan_program_whois(program_id: int, session: AsyncSession = Depends(get_session)):
    """Quét ngày tạo/hết hạn domain qua RDAP → update domain_created_at/expires_at."""
    p = await program_service.get_program(program_id, session)
    if not p:
        raise HTTPException(404, "Không tìm thấy program")
    url = p.url or p.signup_url or p.source_url
    if not url:
        raise HTTPException(400, "Program không có URL để tra WHOIS")
    result = await scan_whois(url)
    p.domain_created_at = result.get("created")
    p.domain_expires_at = result.get("expires")
    p.domain_whois_scanned_at = _dt.utcnow()
    p.domain_whois_status = result.get("status") or ("ok" if result.get("found") else "not_found")
    await session.commit()
    await session.refresh(p)
    return {
        "program_id": program_id,
        "domain": result.get("domain"),
        "created": p.domain_created_at,
        "expires": p.domain_expires_at,
        "found": result.get("found", False),
        "status": p.domain_whois_status,
    }


class BulkScanWhoisIn(BaseModel):
    ids: List[int]
    skip_existing: bool = True   # bỏ qua program đã quét (domain_whois_scanned_at != null)


_WHOIS_BULK_LIMIT = 3000   # RDAP nhẹ → cho phép nhiều hơn traffic


@router.post("/bulk-scan-whois-job")
async def create_whois_scan_job(
    payload: BulkScanWhoisIn,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Tạo background job quét WHOIS (RDAP) — dùng chung hạ tầng job của traffic
    (kind='whois'). FE poll GET /traffic-jobs/{id} để xem tiến độ."""
    if not payload.ids:
        raise HTTPException(400, "Chưa chọn program nào")
    if len(payload.ids) > _WHOIS_BULK_LIMIT:
        raise HTTPException(400, f"Tối đa {_WHOIS_BULK_LIMIT} programs/lần")
    job_id = await traffic_runner.enqueue(
        user_id=getattr(user, "id", None),
        program_ids=payload.ids,
        skip_existing=payload.skip_existing,
        kind="whois",
        concurrency=3,   # rdap.org dùng chung → giữ thấp để tránh 429/timeout
    )
    row = (await session.execute(select(TrafficScanJob).where(TrafficScanJob.id == job_id))).scalar_one()
    return _traffic_job_out(row)


_BULK_SCAN_LIMIT = 100
_BULK_SCAN_CONCURRENCY = 2


@router.post("/bulk-scan-traffic")
async def bulk_scan_traffic(payload: BulkScanTrafficIn, session: AsyncSession = Depends(get_session)):
    """Quét traffic SimilarWeb cho nhiều program cùng lúc."""
    if not payload.ids:
        raise HTTPException(400, "ids rỗng")
    if len(payload.ids) > _BULK_SCAN_LIMIT:
        raise HTTPException(400, f"Tối đa {_BULK_SCAN_LIMIT} program / lần quét")
    months = max(1, min(12, int(payload.months or 3)))
    concurrency = max(1, min(4, int(payload.concurrency or _BULK_SCAN_CONCURRENCY)))

    # Lấy các program theo thứ tự ids (giữ nguyên dải input)
    programs = []
    for pid in payload.ids:
        p = await program_service.get_program(pid, session)
        if p:
            programs.append(p)

    sem = asyncio.Semaphore(concurrency)
    items: list[dict] = []
    counts = {"scanned": 0, "found": 0, "skipped": 0, "failed": 0}

    async def _one(p):
        # Skip CHỈ khi đã có ĐẦY ĐỦ chi tiết (không chỉ score) — đồng bộ với nút quét chi tiết.
        if payload.skip_existing and p.traffic_score and p.traffic_score > 0 and p.traffic_details_json:
            counts["skipped"] += 1
            items.append({"program_id": p.id, "name": p.name, "status": "skipped", "traffic_score": p.traffic_score})
            return
        url = p.url or p.signup_url or p.source_url
        if not url:
            counts["failed"] += 1
            items.append({"program_id": p.id, "name": p.name, "status": "failed", "error": "không có URL"})
            return
        async with sem:
            try:
                result = await scan_traffic(url, months=months)
            except Exception as e:  # noqa: BLE001 - gộp lỗi SW về 1 chỗ
                logger.warning("bulk-scan-traffic failed id=%s url=%s: %s", p.id, url, e)
                counts["failed"] += 1
                items.append({"program_id": p.id, "name": p.name, "status": "failed", "error": str(e)[:200]})
                return
        visits = int(result.get("monthly_visits") or 0)
        p.traffic_score = float(visits)
        p.traffic_period_month = result.get("period_month")
        details = result.get("traffic_details")
        p.traffic_details_json = _json.dumps(details, ensure_ascii=False) if details else None
        p.traffic_scanned_at = _dt.utcnow()
        counts["scanned"] += 1
        if result.get("found"):
            counts["found"] += 1
        items.append({
            "program_id": p.id,
            "name": p.name,
            "status": "ok" if result.get("found") else "empty",
            "monthly_visits": visits,
            "period_month": result.get("period_month"),
        })

    await asyncio.gather(*[_one(p) for p in programs])
    await session.commit()
    return {
        "total": len(payload.ids),
        "matched": len(programs),
        **counts,
        "items": items,
    }


# --- Background traffic-scan job (phương án chuẩn, FE poll progress) ---

def _traffic_job_out(j: TrafficScanJob) -> dict:
    return {
        "id": j.id,
        "kind": getattr(j, "kind", "traffic") or "traffic",
        "status": j.status,
        "total": j.total,
        "scanned": j.scanned,
        "found": j.found,
        "skipped": j.skipped,
        "failed": j.failed,
        "months": j.months,
        "concurrency": j.concurrency,
        "skip_existing": j.skip_existing,
        "start_date": getattr(j, "start_date", None),
        "end_date": getattr(j, "end_date", None),
        "program_ids": _json.loads(j.program_ids_json or "[]"),
        "results": _json.loads(j.results_json or "[]"),
        "error": j.error,
        "started_at": j.started_at.isoformat() + "Z" if j.started_at else None,
        "finished_at": j.finished_at.isoformat() + "Z" if j.finished_at else None,
        "created_at": j.created_at.isoformat() + "Z" if j.created_at else None,
    }


@router.post("/bulk-scan-traffic-job")
async def create_traffic_scan_job(
    payload: BulkScanTrafficIn,
    user = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Tạo background job quét traffic. FE poll GET /traffic-jobs/{id}."""
    if not payload.ids:
        raise HTTPException(400, "Chưa chọn program nào")
    if len(payload.ids) > _BULK_SCAN_LIMIT:
        raise HTTPException(400, f"Tối đa {_BULK_SCAN_LIMIT} programs/lần")
    job_id = await traffic_runner.enqueue(
        user_id=getattr(user, "id", None),
        program_ids=payload.ids,
        skip_existing=payload.skip_existing,
        months=payload.months,
        concurrency=payload.concurrency,
    )
    row = (
        await session.execute(select(TrafficScanJob).where(TrafficScanJob.id == job_id))
    ).scalar_one()
    return _traffic_job_out(row)


@router.post("/bulk-scan-advertisers-job")
async def create_advertisers_scan_job(
    payload: BulkScanAdvertisersIn,
    user = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Tạo background job đếm số nhà quảng cáo (Google Ads) cho domain của các program
    trong khung ngày chọn. FE poll GET /traffic-jobs/{id} (dùng chung)."""
    if not payload.ids:
        raise HTTPException(400, "Chưa chọn program nào")
    if len(payload.ids) > _BULK_SCAN_LIMIT:
        raise HTTPException(400, f"Tối đa {_BULK_SCAN_LIMIT} programs/lần")
    def _norm(d: str) -> str:
        d = (d or "").strip().replace("-", "")
        if d and (len(d) != 8 or not d.isdigit()):
            raise HTTPException(400, "Ngày phải dạng YYYYMMDD")
        return d
    sd, ed = _norm(payload.start_date), _norm(payload.end_date)
    if sd and ed and sd > ed:
        raise HTTPException(400, "Ngày bắt đầu phải ≤ ngày kết thúc")
    job_id = await traffic_runner.enqueue(
        user_id=getattr(user, "id", None),
        program_ids=payload.ids,
        skip_existing=payload.skip_existing,
        concurrency=payload.concurrency,
        kind="advertisers",
        start_date=sd,
        end_date=ed,
    )
    row = (
        await session.execute(select(TrafficScanJob).where(TrafficScanJob.id == job_id))
    ).scalar_one()
    return _traffic_job_out(row)


@router.get("/traffic-jobs/{job_id}")
async def get_traffic_scan_job(
    job_id: int,
    user = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    row = (
        await session.execute(select(TrafficScanJob).where(TrafficScanJob.id == job_id))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Job không tồn tại")
    if row.user_id is not None and row.user_id != getattr(user, "id", None):
        raise HTTPException(404, "Job không tồn tại")
    return _traffic_job_out(row)


# --- SimilarWeb: đăng nhập tay qua noVNC (khi phiên hết hạn / bị chặn 2FA) ---
_sw_login_state: dict = {"running": False, "error": None}


def _novnc_url() -> str:
    """URL để người dùng xem/thao tác trình duyệt Selenium.

    Nếu hub là REMOTE (Selenium Grid) mà NOVNC_URL vẫn trỏ localhost (mặc định cho
    standalone-chrome nội bộ) → localhost không mở được vì phiên chạy trên node từ xa
    (IP nội bộ). Khi đó dùng Grid console live-view của hub: {hub-origin}/ui/#/sessions.
    """
    nov = (read_env_value("NOVNC_URL") or settings.novnc_url or "").strip()
    hub = (read_env_value("SELENIUM_HUB_URL") or settings.selenium_hub_url or "").strip()
    hub_remote = bool(hub) and "localhost" not in hub and "127.0.0.1" not in hub
    nov_local = (not nov) or ("localhost" in nov) or ("127.0.0.1" in nov)
    if hub_remote and nov_local:
        from urllib.parse import urlparse
        p = urlparse(hub if "://" in hub else f"https://{hub}")
        return f"{p.scheme}://{p.netloc}/ui/#/sessions"
    return nov


async def _run_sw_login() -> None:
    from app.services.traffic import similarweb as sw
    from app.services.traffic import scanner as _scanner
    try:
        # headless=False (hiện trên noVNC) + allow_manual=True (chờ người dùng ~10').
        await asyncio.to_thread(sw.refresh_cookie_blocking, False, True)
        _scanner._refresh_failed_at = None   # mở lại circuit-breaker → quét lại được ngay
        _sw_login_state["error"] = None
    except Exception as e:  # noqa: BLE001
        _sw_login_state["error"] = str(e)[:300]
        logger.warning("SimilarWeb login thất bại: %s", e)
    finally:
        _sw_login_state["running"] = False


@router.get("/similarweb/status")
async def similarweb_status(user=Depends(get_current_user)):
    """Trạng thái cookie SimilarWeb + phiên đăng nhập tay đang chạy (nếu có)."""
    from app.services.traffic import similarweb as sw
    return {
        "logged_in": sw.load_cached_cookie() is not None,
        "login_running": _sw_login_state["running"],
        "error": _sw_login_state["error"],
        "novnc_url": _novnc_url(),
    }


@router.post("/similarweb/login")
async def similarweb_login(user=Depends(get_current_user)):
    """Mở phiên Selenium (hiện trên noVNC) để đăng nhập SimilarWeb bằng tay. Trả về
    ngay kèm URL noVNC; cookie tự lưu khi đăng nhập xong. FE poll /similarweb-status."""
    if _sw_login_state["running"]:
        return {"status": "already_running", "novnc_url": _novnc_url(),
                "message": "Đang chờ bạn đăng nhập ở noVNC…"}
    _sw_login_state.update({"running": True, "error": None})
    asyncio.create_task(_run_sw_login())
    return {"status": "started", "novnc_url": _novnc_url(),
            "message": "Mở noVNC và đăng nhập SimilarWeb. Cookie sẽ tự lưu khi xong."}


class DiscoverHomepagesIn(BaseModel):
    source: str = "lovable"
    limit: int = 200
    concurrency: int = 5
    method: str = "auto"  # "auto" | "browser"


@router.post("/discover-homepages")
async def discover_homepages(
    payload: DiscoverHomepagesIn = DiscoverHomepagesIn(),
    user = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Auto-discover real homepage for programs with intermediate affiliate URL.

    For source=lovable where url is {slug}.getrewardful.com instead of real company domain.
    Updates DB directly. Returns {"total": N, "updated": M, "failed": K}.
    """
    if not (1 <= payload.concurrency <= 20):
        raise HTTPException(400, "concurrency phai tu 1 den 20")
    if not (1 <= payload.limit <= 1000):
        raise HTTPException(400, "limit phai tu 1 den 1000")
    result = await program_service.discover_homepages_batch(
        session,
        source=payload.source,
        limit=payload.limit,
        concurrency=payload.concurrency,
        method=payload.method,
    )
    return result


class UpdateProgramUrlIn(BaseModel):
    url: str


@router.patch("/{program_id}/url")
async def update_program_url(
    program_id: int,
    payload: UpdateProgramUrlIn,
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Manually set URL for a program."""
    from datetime import datetime
    prog = await session.get(AffiliateProgram, program_id)
    if not prog:
        raise HTTPException(404, "Program not found")
    prog.url = payload.url.strip()
    prog.updated_at = datetime.utcnow()
    await session.commit()
    return {"id": program_id, "url": prog.url}


@router.post("/{program_id}/discover-homepage")
async def discover_single_homepage(
    program_id: int,
    method: str = "auto",
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Auto-discover homepage for a single program."""
    result = await program_service.discover_single_homepage(
        session, program_id, method=method
    )
    if result is None:
        raise HTTPException(404, "Program not found")
    return result
