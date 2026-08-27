from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Form, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, func, text, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session, SessionLocal
from app.deps import get_current_user
from app.models import User
from app.models.discovery import DiscoverySource, DiscoveryCandidate, DomainBlacklist
from app.services.discovery.crawler import crawl_discovery_source
from app.services.discovery.job import run_discovery_crawl_job
from app.services.discovery.resolver import resolve_homepage
from app.services.discovery.affiliate_detector import detect_affiliate

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/discovery", tags=["discovery"], dependencies=[Depends(get_current_user)])

# ─── Schemas ──────────────────────────────────────────────────────────────────

class SourceIn(BaseModel):
    url: str
    name: Optional[str] = None
    category: Optional[str] = None
    field: Optional[str] = None


class SourceOut(BaseModel):
    id: int
    url: str
    name: Optional[str]
    category: Optional[str]
    field: Optional[str] = None
    status: str
    is_crawling: bool
    last_crawled_at: Optional[str]
    total_candidates_found: int
    created_at: str

    model_config = {"from_attributes": True}


class CandidateOut(BaseModel):
    id: int
    source_id: int
    raw_url: str
    domain: str
    level: int
    depth: int
    is_primary: bool
    detection_method: Optional[str]
    suggested_name: Optional[str]
    source_page_url: Optional[str]
    source_page_title: Optional[str]
    homepage_url: Optional[str]
    is_redirect_resolved: bool
    traffic_monthly: Optional[int]
    traffic_status: Optional[str]
    affiliate_url: Optional[str]
    affiliate_detection_method: Optional[str]
    affiliate_url_status: Optional[str]
    ad_days_shown: Optional[int] = None
    ad_first_shown: Optional[str] = None
    ad_last_shown: Optional[str] = None
    status: str
    promoted_program_id: Optional[int]
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class CandidatePageOut(BaseModel):
    items: List[CandidateOut]
    total: int
    page: int
    page_size: int


class BlacklistIn(BaseModel):
    domain: str
    category: Optional[str] = "custom"


class BlacklistOut(BaseModel):
    id: int
    domain: str
    category: Optional[str]
    created_at: str

    model_config = {"from_attributes": True}


class RunPipelineIn(BaseModel):
    candidate_ids: List[int]
    proxy_ids: Optional[List[str]] = None   # route browser fetches through a proxy


class OwnerOut(BaseModel):
    id: int
    email: str
    source_count: int
    candidate_count: int


class PipelineSummaryOut(BaseModel):
    discovered: int
    homepage_resolved: int
    traffic_scanned: int
    affiliate_found: int
    promoted: int
    no_affiliate_found: int
    total: int


# ─── Phân quyền dữ liệu ───────────────────────────────────────────────────────

def _view_scope(user: User, owner_id: Optional[int]) -> int:
    """user_id dùng để LỌC khi xem. Admin xem được dữ liệu user khác (chỉ xem);
    mọi thao tác ghi vẫn dùng `user.id` nên không sửa được dữ liệu người khác."""
    if owner_id is None or owner_id == user.id:
        return user.id
    if not user.is_admin:
        raise HTTPException(403, "Chỉ admin mới xem được dự án của user khác")
    return owner_id


async def _own_source(source_id: int, user: User, session: AsyncSession) -> DiscoverySource:
    src = (await session.execute(
        select(DiscoverySource).where(DiscoverySource.id == source_id,
                                      DiscoverySource.user_id == user.id)
    )).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Không tìm thấy source")
    return src


async def _own_candidate_ids(ids: List[int], user: User, session: AsyncSession) -> List[int]:
    """Lọc bỏ id không thuộc về user — thao tác chạy nền nên phải chặn từ đầu vào."""
    if not ids:
        return []
    return list((await session.execute(
        select(DiscoveryCandidate.id).where(DiscoveryCandidate.id.in_(ids),
                                            DiscoveryCandidate.user_id == user.id)
    )).scalars().all())


@router.get("/owners", response_model=List[OwnerOut])
async def list_owners(session: AsyncSession = Depends(get_session),
                      user: User = Depends(get_current_user)):
    """Các user khác đang hoạt động — cho tab 'Dự án user khác' của admin.

    Trả về cả user CHƯA quét gì (count = 0): admin cần thấy tab ngay khi có tài khoản
    được duyệt, chứ không phải đợi tới lúc họ quét xong mới thấy."""
    if not user.is_admin:
        raise HTTPException(403, "Chỉ admin mới được truy cập")
    users = (await session.execute(
        select(User).where(User.id != user.id, User.is_active.is_(True)).order_by(User.email)
    )).scalars().all()
    if not users:
        return []
    src_counts = dict((await session.execute(
        select(DiscoverySource.user_id, func.count()).group_by(DiscoverySource.user_id)
    )).all())
    cand_counts = dict((await session.execute(
        select(DiscoveryCandidate.user_id, func.count()).group_by(DiscoveryCandidate.user_id)
    )).all())
    return [
        OwnerOut(id=u.id, email=u.email,
                 source_count=src_counts.get(u.id, 0), candidate_count=cand_counts.get(u.id, 0))
        for u in users
    ]


# ─── Sources ──────────────────────────────────────────────────────────────────

@router.get("/sources", response_model=List[SourceOut])
async def list_sources(owner_id: Optional[int] = None,
                       session: AsyncSession = Depends(get_session),
                       user: User = Depends(get_current_user)):
    scope = _view_scope(user, owner_id)
    rows = (await session.execute(
        select(DiscoverySource).where(DiscoverySource.user_id == scope).order_by(DiscoverySource.id)
    )).scalars().all()
    return [_source_out(r) for r in rows]


@router.post("/sources", response_model=SourceOut, status_code=201)
async def create_source(body: SourceIn, session: AsyncSession = Depends(get_session),
                        user: User = Depends(get_current_user)):
    url = body.url.strip().rstrip("/")
    existing = (await session.execute(
        select(DiscoverySource).where(DiscoverySource.url == url,
                                      DiscoverySource.user_id == user.id)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "URL này đã tồn tại")
    src = DiscoverySource(user_id=user.id, url=url, name=body.name, category=(body.category or None),
                          field=(body.field.strip() if body.field else None) or None,
                          status="active", total_candidates_found=0, created_at=datetime.utcnow())
    session.add(src)
    await session.commit()
    await session.refresh(src)
    return _source_out(src)


_DOMAIN_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")


def _norm_import_domain(s: str) -> Optional[str]:
    """Chuẩn hoá 1 dòng nhập → domain trần (bỏ scheme/path/www/port). None nếu không hợp lệ."""
    s = (s or "").strip().lower()
    if not s:
        return None
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0].split(":")[0]
    if s.startswith("www."):
        s = s[4:]
    return s if (_DOMAIN_RE.match(s) and len(s) >= 4) else None


def _parse_import_rows(content: str) -> list[tuple[str, str, str, str]]:
    """Nội dung file import → [(domain, category, sub_category, field)]. Nếu dòng đầu là
    header CSV có cột 'domain' → đọc theo cột (category/sub_category/field theo từng dòng);
    ngược lại coi mỗi token là 1 domain (các cột rỗng → dùng mặc định của form)."""
    content = (content or "").lstrip("﻿")
    if not content.strip():
        return []
    first = content.splitlines()[0].lower()
    if "domain" in first and ("," in first or ";" in first or "\t" in first):
        out: list[tuple[str, str, str, str]] = []
        reader = csv.DictReader(io.StringIO(content))
        for r in reader:
            row = {(k or "").replace("﻿", "").strip().lower(): (v or "").strip()
                   for k, v in r.items() if k}
            dom = row.get("domain") or ""
            if not dom:
                continue
            out.append((dom, row.get("category", ""),
                        row.get("sub_category") or row.get("subcategory", ""),
                        row.get("field") or row.get("linh_vuc", "")))
        return out
    return [(t, "", "", "") for t in re.split(r"[\s,;]+", content) if t.strip()]


def _parse_numbers_rows(raw: bytes) -> list[tuple[str, str, str, str]]:
    """Apple Numbers (.numbers = ZIP + protobuf) → rows. Ghi file tạm rồi đọc bằng
    numbers-parser, chuyển bảng ĐẦU TIÊN thành text CSV và tái dùng _parse_import_rows
    (nhận diện header domain/category/sub_category/field như file CSV thường)."""
    import os
    import tempfile
    import warnings
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".numbers", delete=False) as tf:
            tf.write(raw)
            tmp = tf.name
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")   # nuốt cảnh báo "Numbers version X not tested"
            from numbers_parser import Document
            table = Document(tmp).sheets[0].tables[0]
            buf = io.StringIO()
            w = csv.writer(buf)
            for r in table.rows(values_only=True):
                w.writerow(["" if c is None else str(c) for c in r])
        return _parse_import_rows(buf.getvalue())
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


class ImportDomainsOut(BaseModel):
    source_id: int
    name: str
    category: Optional[str]
    total_parsed: int         # tổng dòng nhập (paste + file)
    created: int              # domain tải lên thành công (mới)
    skipped_duplicate: int    # bỏ qua vì trùng (đã có ở nguồn khác của bạn, hoặc trùng trong danh sách)
    skipped_invalid: int      # dòng không phải domain hợp lệ


@router.post("/import-domains", response_model=ImportDomainsOut, status_code=201)
async def import_domains(
    name: str = Form(...),
    category: str = Form(""),
    field: str = Form(""),            # Lĩnh vực (Cách 3) — áp cho mọi domain của nguồn này
    domains: str = Form(""),          # dán nhiều domain (xuống dòng/phẩy/space)
    file: Optional[UploadFile] = File(None),  # hoặc tải file danh sách domain (.txt/.csv)
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Nhập thủ công 1 danh sách domain thành 1 nguồn Discovery (đặt Tên gợi nhớ + Category
    như khi Thêm nguồn). Trùng domain (trong danh sách hoặc đã có ở nguồn khác của bạn) sẽ bỏ
    qua. Sau đó dùng được các chức năng: Dò affiliate, Quét traffic… như candidate thường."""
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "Cần Tên gợi nhớ")

    default_cat = (category or "").strip() or None
    default_sub = name or None   # Tên gợi nhớ = sub_category mặc định (ngách hẹp)
    default_field = (field or "").strip() or None   # Lĩnh vực mặc định (Cách 3 — cấp nguồn)

    # Ô "Dán domain" luôn là danh sách phẳng; file CSV có cột 'domain' thì đọc category/
    # sub_category/field theo từng dòng (mỗi domain 1 ngách riêng). Dòng thiếu → dùng mặc định.
    rows: list[tuple[str, str, str, str]] = [
        (t, "", "", "") for t in re.split(r"[\s,;]+", domains or "") if t.strip()
    ]
    if file is not None:
        raw = await file.read()
        if (file.filename or "").lower().endswith(".numbers"):
            # Apple Numbers: mở bằng Numbers rồi Save ra .numbers (ZIP+protobuf) — đọc riêng.
            try:
                rows.extend(_parse_numbers_rows(raw))
            except ImportError:
                raise HTTPException(400, "Server chưa cài numbers-parser để đọc .numbers — hãy Export sang CSV (Numbers: File → Export To → CSV).")
            except Exception as e:
                raise HTTPException(400, f"Không đọc được file .numbers ({e}). Thử Export sang CSV trong Numbers.")
        else:
            rows.extend(_parse_import_rows(raw.decode("utf-8", "ignore")))

    total_parsed = len(rows)
    invalid = 0
    seen: set[str] = set()
    norm: list[tuple[str, str, str, str]] = []   # (domain, category, sub_category, field) chuẩn hoá + dedup
    for raw_dom, row_cat, row_sub, row_field in rows:
        d = _norm_import_domain(raw_dom)
        if not d:
            invalid += 1
            continue
        if d in seen:
            continue
        seen.add(d)
        norm.append((d, (row_cat or "").strip() or default_cat,
                     (row_sub or "").strip() or default_sub,
                     (row_field or "").strip() or default_field))
    batch_dupes = (total_parsed - invalid) - len(norm)
    if not norm:
        raise HTTPException(400, "Không có domain hợp lệ trong danh sách")

    now = datetime.utcnow()
    src = DiscoverySource(
        user_id=user.id,
        url=f"manual-import://{name}-{int(now.timestamp())}",
        name=name, category=(category.strip() or None), field=default_field,
        status="active", total_candidates_found=0, created_at=now,
    )
    session.add(src)
    await session.flush()   # lấy src.id

    # Dedup toàn cục theo (user_id, domain) — giống crawler.
    existing = set((await session.execute(
        select(DiscoveryCandidate.domain).where(
            DiscoveryCandidate.user_id == user.id,
            DiscoveryCandidate.domain.in_([d for d, *_ in norm]),
        )
    )).scalars().all())
    created = 0
    for d, row_cat, row_sub, row_field in norm:
        if d in existing:
            continue
        session.add(DiscoveryCandidate(
            user_id=user.id, source_id=src.id, raw_url=f"https://{d}", domain=d,
            level=1, depth=1, is_primary=True, detection_method="manual_import",
            suggested_name=d, category=row_cat, sub_category=row_sub, field=row_field,
            status="discovered", created_at=now, updated_at=now,
        ))
        created += 1
    src.total_candidates_found = created
    src.last_crawled_at = now
    await session.commit()
    await session.refresh(src)
    return ImportDomainsOut(
        source_id=src.id, name=name, category=src.category,
        total_parsed=total_parsed, created=created,
        skipped_duplicate=(len(norm) - created) + batch_dupes,
        skipped_invalid=invalid,
    )


class SourceUpdateIn(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None


@router.patch("/sources/{source_id}", response_model=SourceOut)
async def update_source(source_id: int, body: SourceUpdateIn, session: AsyncSession = Depends(get_session),
                        user: User = Depends(get_current_user)):
    """Cập nhật tên / category của nguồn. Khi đổi category, re-sync category cho
    mọi dự án đã promote từ nguồn này (dựa trên domain của candidate)."""
    src = await _own_source(source_id, user, session)

    name_changed = False
    if body.name is not None:
        new_name = body.name.strip() or None
        if new_name != src.name:
            src.name = new_name
            name_changed = True
    cat_changed = False
    if body.category is not None:
        new_cat = body.category.strip() or None
        if new_cat != src.category:
            src.category = new_cat
            cat_changed = True
    await session.commit()

    if cat_changed or name_changed:
        # program promote từ discovery/google_ads: external_id = "<source>_<domain>".
        # Đổi Category nguồn → re-sync program.category (nhóm rộng);
        # đổi Tên gợi nhớ → re-sync program.sub_category (ngách hẹp).
        domains = (await session.execute(
            select(DiscoveryCandidate.domain).where(DiscoveryCandidate.source_id == source_id)
        )).scalars().all()
        ext_ids = [f"{pref}_{d}" for d in domains if d for pref in ("discovery", "google_ads")]
        if ext_ids:
            from app.models.affiliate_program import AffiliateProgram
            from sqlalchemy import update as _sql_update
            values: dict = {}
            if cat_changed:
                values["category"] = src.category
            if name_changed:
                values["sub_category"] = src.name
            r = await session.execute(
                _sql_update(AffiliateProgram)
                .where(AffiliateProgram.source.in_(("discovery", "google_ads")),
                       AffiliateProgram.external_id.in_(ext_ids))
                .values(**values)
            )
            await session.commit()
            log.info("[discovery] re-sync %s cho %d program của nguồn #%d",
                     list(values.keys()), r.rowcount, source_id)

    await session.refresh(src)
    return _source_out(src)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: int, session: AsyncSession = Depends(get_session),
                        user: User = Depends(get_current_user)):
    src = await _own_source(source_id, user, session)
    await session.execute(delete(DiscoveryCandidate).where(DiscoveryCandidate.source_id == source_id))
    await session.delete(src)
    await session.commit()


class CrawlOptionsIn(BaseModel):
    proxy_ids: Optional[List[str]] = None   # pool of proxy ids (round-robin)
    max_pages: Optional[int] = None         # max listing pages (None/0 = all)
    incremental: bool = False               # only scan newest until all-known → stop


@router.post("/sources/{source_id}/crawl")
async def crawl_source(
    source_id: int,
    body: Optional[CrawlOptionsIn] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    src = await _own_source(source_id, user, session)
    if src.status == "crawling":
        raise HTTPException(409, "Nguồn này đang được quét")

    opts = body or CrawlOptionsIn()
    proxy_urls: List[str] = []
    proxy_labels: List[str] = []
    if opts.proxy_ids:
        from app.services.storage import proxy_store
        for pid in opts.proxy_ids:
            p = proxy_store.get_proxy(getattr(user, "id", 0), pid)
            if not p:
                continue
            url = p.get("url")
            if url:
                proxy_urls.append(url)
                proxy_labels.append(p.get("label") or f'{p.get("host")}:{p.get("port")}')
        if not proxy_urls:
            raise HTTPException(404, "Không có proxy hợp lệ trong danh sách")

    max_pages = opts.max_pages if (opts.max_pages and opts.max_pages > 0) else None

    asyncio.create_task(run_discovery_crawl_job(
        source_id,
        user_id=getattr(user, "id", None),
        proxy_urls=proxy_urls,
        proxy_labels=proxy_labels,
        max_listing_pages=max_pages,
        incremental=bool(opts.incremental),
    ))
    return {"ok": True, "source_id": source_id, "message": "Crawl đã được khởi động"}


@router.post("/sources/{source_id}/stop")
async def stop_source(source_id: int, session: AsyncSession = Depends(get_session),
                      user: User = Depends(get_current_user)):
    """Yêu cầu dừng crawl đang chạy. Crawler dừng êm ở lần fetch tiếp theo và
    giữ lại các dự án đã tìm được."""
    from app.services.discovery.crawler import request_stop
    await _own_source(source_id, user, session)
    request_stop(source_id)
    return {"ok": True, "source_id": source_id, "message": "Đã yêu cầu dừng — crawl sẽ dừng trong giây lát"}


# ─── Candidates ───────────────────────────────────────────────────────────────

def _candidate_filters(source_id=None, status=None, is_primary=None, detection_method=None,
                       min_traffic=None, traffic_state=None, affiliate_state=None, q=None,
                       owner_scope=None):
    """Bộ điều kiện lọc candidate — dùng chung cho list + export CSV (tránh lệch logic)."""
    filters = []
    if owner_scope is not None:
        filters.append(DiscoveryCandidate.user_id == owner_scope)
    if source_id is not None:
        filters.append(DiscoveryCandidate.source_id == source_id)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        filters.append(
            func.lower(DiscoveryCandidate.domain).like(like)
            | func.lower(DiscoveryCandidate.suggested_name).like(like)
        )
    if status:
        filters.append(DiscoveryCandidate.status == status)
    if is_primary is not None:
        filters.append(DiscoveryCandidate.is_primary == is_primary)
    if detection_method:
        filters.append(DiscoveryCandidate.detection_method == detection_method)
    if min_traffic is not None:
        filters.append(DiscoveryCandidate.traffic_monthly >= min_traffic)
    # TT Traffic
    if traffic_state == "pending":
        filters.append(DiscoveryCandidate.traffic_status.is_(None))
    elif traffic_state in ("scanned", "not_found", "failed"):
        filters.append(DiscoveryCandidate.traffic_status == traffic_state)
    # TT Affiliate (derived from affiliate_url + status)
    if affiliate_state == "found":
        filters.append(DiscoveryCandidate.affiliate_url.is_not(None))
    elif affiliate_state == "not_found":
        filters.append(DiscoveryCandidate.status == "no_affiliate_found")
    elif affiliate_state == "pending":
        filters.append(DiscoveryCandidate.affiliate_url.is_(None))
        filters.append(DiscoveryCandidate.status != "no_affiliate_found")
    return filters


@router.get("/candidates", response_model=CandidatePageOut)
async def list_candidates(
    source_id: Optional[int] = None,
    status: Optional[str] = None,
    is_primary: Optional[bool] = None,
    detection_method: Optional[str] = None,
    min_traffic: Optional[int] = None,
    traffic_state: Optional[str] = None,     # pending | scanned | not_found | failed
    affiliate_state: Optional[str] = None,   # pending | found | not_found
    q: Optional[str] = None,                 # tìm theo domain / tên gợi ý
    owner_id: Optional[int] = None,          # admin xem dự án của user khác (chỉ xem)
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    filters = _candidate_filters(source_id, status, is_primary, detection_method,
                                 min_traffic, traffic_state, affiliate_state, q,
                                 owner_scope=_view_scope(user, owner_id))

    total = (await session.execute(
        select(func.count()).select_from(DiscoveryCandidate).where(*filters)
    )).scalar() or 0

    q = (select(DiscoveryCandidate).where(*filters)
         .order_by(DiscoveryCandidate.id.desc())
         .offset((page - 1) * page_size).limit(page_size))
    rows = (await session.execute(q)).scalars().all()
    return CandidatePageOut(
        items=[_candidate_out(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/candidates/summary", response_model=PipelineSummaryOut)
async def candidates_summary(
    source_id: Optional[int] = None,
    owner_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    q = select(DiscoveryCandidate.status, func.count().label("n")).where(
        DiscoveryCandidate.user_id == _view_scope(user, owner_id)
    )
    if source_id:
        q = q.where(DiscoveryCandidate.source_id == source_id)
    q = q.group_by(DiscoveryCandidate.status)
    rows = (await session.execute(q)).fetchall()
    counts: dict[str, int] = {r[0]: r[1] for r in rows}
    total = sum(counts.values())
    return PipelineSummaryOut(
        discovered=counts.get("discovered", 0),
        homepage_resolved=counts.get("homepage_resolved", 0),
        traffic_scanned=counts.get("traffic_scanned", 0),
        affiliate_found=counts.get("affiliate_found", 0),
        promoted=counts.get("promoted", 0),
        no_affiliate_found=counts.get("no_affiliate_found", 0),
        total=total,
    )


@router.get("/candidates/export.csv")
async def export_candidates_csv(
    ids: Optional[str] = Query(None, description="Danh sách id ngăn cách bởi dấu phẩy — xuất các dự án ĐÃ CHỌN"),
    source_id: Optional[int] = None,
    status: Optional[str] = None,
    is_primary: Optional[bool] = None,
    detection_method: Optional[str] = None,
    min_traffic: Optional[int] = None,
    traffic_state: Optional[str] = None,
    affiliate_state: Optional[str] = None,
    q: Optional[str] = None,
    owner_id: Optional[int] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Xuất CSV đầy đủ thông tin. Có `ids` → xuất đúng các candidate đã chọn.
    Không có `ids` → xuất TOÀN BỘ candidate khớp bộ lọc hiện tại (không phân trang)."""
    scope = _view_scope(user, owner_id)
    if ids:
        try:
            id_list = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(400, "ids phải là danh sách số")
        filters = [DiscoveryCandidate.user_id == scope]
        if id_list:
            filters.append(DiscoveryCandidate.id.in_(id_list))
    else:
        filters = _candidate_filters(source_id, status, is_primary, detection_method,
                                     min_traffic, traffic_state, affiliate_state, q,
                                     owner_scope=scope)

    rows = (await session.execute(
        select(DiscoveryCandidate).where(*filters).order_by(DiscoveryCandidate.id.desc())
    )).scalars().all()
    srcs = (await session.execute(
        select(DiscoverySource).where(DiscoverySource.user_id == scope)
    )).scalars().all()
    src_map = {s.id: s for s in srcs}

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "id", "domain", "homepage_url", "raw_url", "status",
        "detection_method", "suggested_name", "is_primary",
        "traffic_monthly", "traffic_status",
        "affiliate_url", "affiliate_detection_method", "affiliate_url_status",
        "promoted_program_id", "source_name", "source_url",
        "source_page_url", "source_page_title", "level", "depth",
        "created_at", "updated_at",
    ])
    for c in rows:
        s = src_map.get(c.source_id)
        w.writerow([
            c.id, c.domain, c.homepage_url or "", c.raw_url or "", c.status,
            c.detection_method or "", (c.suggested_name or "").replace("\n", " "), int(bool(c.is_primary)),
            c.traffic_monthly if c.traffic_monthly is not None else "", c.traffic_status or "",
            c.affiliate_url or "", c.affiliate_detection_method or "", c.affiliate_url_status or "",
            c.promoted_program_id if c.promoted_program_id is not None else "",
            (s.name or "") if s else "", (s.url or "") if s else "",
            c.source_page_url or "", (c.source_page_title or "").replace("\n", " "),
            c.level, c.depth, _fmt(c.created_at), _fmt(c.updated_at),
        ])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="discovery_candidates.csv"'},
    )


async def _own_candidate(candidate_id: int, user: User, session: AsyncSession) -> DiscoveryCandidate:
    c = (await session.execute(
        select(DiscoveryCandidate).where(DiscoveryCandidate.id == candidate_id,
                                         DiscoveryCandidate.user_id == user.id)
    )).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Không tìm thấy candidate")
    return c


@router.post("/candidates/{candidate_id}/resolve-homepage")
async def resolve_homepage_endpoint(candidate_id: int, session: AsyncSession = Depends(get_session),
                                    user: User = Depends(get_current_user)):
    await _own_candidate(candidate_id, user, session)
    await resolve_homepage(candidate_id, session)
    return {"ok": True}


async def _sync_program_traffic(program_id: int, result: dict, session: AsyncSession) -> None:
    """Copy a candidate's traffic scan onto its promoted affiliate program, using the
    exact fields the Programs screen reads. Without this, a candidate scanned AFTER it
    was promoted keeps its traffic only on the discovery row, never on the program."""
    import json as _json
    from app.models.affiliate_program import AffiliateProgram
    prog = await session.get(AffiliateProgram, program_id)
    if not prog:
        return
    prog.traffic_score = float(int(result.get("monthly_visits") or 0))
    prog.traffic_period_month = result.get("period_month")
    details = result.get("traffic_details")
    prog.traffic_details_json = _json.dumps(details, ensure_ascii=False) if details else None
    prog.traffic_scanned_at = datetime.utcnow()


async def _scan_candidate_traffic(c: DiscoveryCandidate, session: AsyncSession, months: int = 3) -> dict:
    """Scan SimilarWeb traffic for a candidate — same engine the Programs screen
    uses (app.services.traffic.scan_traffic). Updates traffic_monthly/status and
    advances the candidate to 'traffic_scanned' when still early in the pipeline."""
    from app.services.traffic.scanner import scan_traffic
    url = c.homepage_url or c.raw_url
    try:
        result = await scan_traffic(url, months=months)
        c.traffic_monthly = result.get("monthly_visits") or 0
        c.traffic_status = "scanned" if result.get("found") else "not_found"
        if c.status in ("discovered", "homepage_resolved"):
            c.status = "traffic_scanned"
        c.updated_at = datetime.utcnow()
        # Keep the promoted program's traffic in sync with the discovery row.
        if c.promoted_program_id:
            await _sync_program_traffic(c.promoted_program_id, result, session)
        await session.commit()
        return {"ok": True, "monthly_visits": c.traffic_monthly, "found": bool(result.get("found"))}
    except Exception as e:
        c.traffic_status = "failed"
        c.updated_at = datetime.utcnow()
        await session.commit()
        log.warning("[discovery] scan-traffic failed id=%s url=%s: %s", c.id, url, e)
        return {"ok": False, "error": str(e)}


@router.post("/candidates/{candidate_id}/scan-traffic")
async def scan_traffic_endpoint(candidate_id: int, session: AsyncSession = Depends(get_session),
                                user: User = Depends(get_current_user)):
    c = await _own_candidate(candidate_id, user, session)
    return await _scan_candidate_traffic(c, session)


async def _run_traffic_batch(candidate_ids: List[int], user_id: Optional[int] = None) -> None:
    """Background: scan traffic sequentially, tracked as a CrawlJob so the UI can
    show progress + a bell notification. Candidate rows also update live."""
    from app.services import job_service
    total = len(candidate_ids)
    async with SessionLocal() as s:
        job = await job_service.create_job("discovery_traffic", s, user_id=user_id, params={"total": total})
        jid = job.id
        await job_service.update_job(jid, s, status="running", started_at=datetime.utcnow())
    scanned = found = 0
    try:
        for cid in candidate_ids:
            try:
                async with SessionLocal() as s:
                    c = await s.get(DiscoveryCandidate, cid)
                    if c:
                        r = await _scan_candidate_traffic(c, s)
                        scanned += 1
                        if r.get("found"):
                            found += 1
            except Exception as e:
                log.warning("[discovery] traffic batch id=%s lỗi: %s", cid, e)
            async with SessionLocal() as s:
                await job_service.update_job(jid, s, total_saved=scanned, total_found=found)
        async with SessionLocal() as s:
            await job_service.update_job(jid, s, status="success", total_saved=scanned,
                                         total_found=found, finished_at=datetime.utcnow())
    except Exception as e:
        async with SessionLocal() as s:
            await job_service.update_job(jid, s, status="failed", error=str(e), finished_at=datetime.utcnow())


@router.post("/candidates/scan-traffic-batch")
async def scan_traffic_batch(body: RunPipelineIn, session: AsyncSession = Depends(get_session),
                             user: User = Depends(get_current_user)):
    """Kick off a background traffic scan for many candidates. Returns immediately;
    progress shows in the banner + bell."""
    ids = await _own_candidate_ids(body.candidate_ids, user, session)
    asyncio.create_task(_run_traffic_batch(ids, user.id))
    return {"started": len(ids)}


@router.post("/candidates/{candidate_id}/detect-affiliate")
async def detect_affiliate_endpoint(candidate_id: int, session: AsyncSession = Depends(get_session),
                                    user: User = Depends(get_current_user)):
    from app.services.discovery import browser_fetch
    await _own_candidate(candidate_id, user, session)
    try:
        await detect_affiliate(candidate_id, session)
    finally:
        await browser_fetch.close_shared_browser()
    c = (await session.execute(
        select(DiscoveryCandidate).where(DiscoveryCandidate.id == candidate_id)
    )).scalar_one_or_none()
    return {"ok": True, "status": c.status if c else "unknown", "affiliate_url": c.affiliate_url if c else None}


def _resolve_proxy_urls(user_id: int, proxy_ids: Optional[List[str]]) -> List[str]:
    """Resolve proxy ids → list of proxy URLs (rotating pool for browser fetches)."""
    if not proxy_ids:
        return []
    from app.services.storage import proxy_store
    urls: List[str] = []
    for pid in proxy_ids:
        p = proxy_store.get_proxy(user_id, pid)
        if p and p.get("url"):
            urls.append(p["url"])
    return urls


async def _run_affiliate_batch(candidate_ids: List[int], proxy_urls: Optional[List[str]] = None,
                               user_id: Optional[int] = None) -> None:
    """Background: dò affiliate qua pool CloakBrowser xoay IP, tracked as a CrawlJob
    (banner + bell). Bảng cũng cập nhật dần."""
    from app.services.discovery import browser_fetch
    from app.services import job_service
    total = len(candidate_ids)
    async with SessionLocal() as s:
        job = await job_service.create_job("discovery_affiliate", s, user_id=user_id,
                                           params={"total": total, "proxy": f"pool {len(proxy_urls or [])} IP" if proxy_urls else "không"})
        jid = job.id
        await job_service.update_job(jid, s, status="running", started_at=datetime.utcnow())
    processed = found = 0
    try:
        await browser_fetch.set_proxies(proxy_urls or [])
        for cid in candidate_ids:
            try:
                async with SessionLocal() as s:
                    await detect_affiliate(cid, s)
                    c = await s.get(DiscoveryCandidate, cid)
                    processed += 1
                    if c and c.affiliate_url:
                        found += 1
            except Exception as e:
                processed += 1
                log.warning("[discovery] detect-affiliate id=%s lỗi: %s", cid, e)
            async with SessionLocal() as s:
                await job_service.update_job(jid, s, total_saved=processed, total_found=found)
        async with SessionLocal() as s:
            await job_service.update_job(jid, s, status="success", total_saved=processed,
                                         total_found=found, finished_at=datetime.utcnow())
    except Exception as e:
        async with SessionLocal() as s:
            await job_service.update_job(jid, s, status="failed", error=str(e), finished_at=datetime.utcnow())
    finally:
        await browser_fetch.close_shared_browser()


@router.post("/candidates/detect-affiliate-batch")
async def detect_affiliate_batch(body: RunPipelineIn, session: AsyncSession = Depends(get_session),
                                 user: User = Depends(get_current_user)):
    """Kick off a background affiliate scan. Returns immediately; progress in
    banner + bell."""
    proxy_urls = _resolve_proxy_urls(user.id, body.proxy_ids)
    ids = await _own_candidate_ids(body.candidate_ids, user, session)
    asyncio.create_task(_run_affiliate_batch(ids, proxy_urls, user.id))
    return {"started": len(ids)}


async def _run_affiliate_search_batch(candidate_ids: List[int], proxy_urls: Optional[List[str]] = None,
                                      user_id: Optional[int] = None) -> None:
    """Background: dò affiliate qua Google search (CloakBrowser, MỘT browser dùng
    chung, tuần tự để tránh Google chặn — miễn phí như 'Tìm trang chủ'). Dùng khi
    fetch site thất bại (Cloudflare) hoặc affiliate ở path sâu/lạ. Tracked as CrawlJob."""
    import random
    from app.services.crawlers import homepage_finder
    from app.services.discovery.affiliate_detector import detect_affiliate_via_search
    from app.services.discovery import browser_fetch
    from app.services import job_service
    from app.services.browser.session import get_browser

    total = len(candidate_ids)
    capsolver_proxy = (proxy_urls[0] if proxy_urls else "")  # proxy cho CapSolver; browser Google KHÔNG proxy
    async with SessionLocal() as s:
        job = await job_service.create_job("discovery_affiliate_search", s, user_id=user_id,
                                           params={"total": total})
        jid = job.id
        await job_service.update_job(jid, s, status="running", started_at=datetime.utcnow())
    processed = found = 0
    browser = page = None
    try:
        await browser_fetch.set_proxies(proxy_urls or [])   # cho check link-status render
        browser = await get_browser(headless=True)          # Google search: KHÔNG proxy (VN proxy chặn Google)
        if browser is not None:
            page = await browser.new_page()
            await homepage_finder._warmup_google(page)
        for cid in candidate_ids:
            try:
                async with SessionLocal() as s:
                    ok = await detect_affiliate_via_search(cid, s, page=page, proxy_url=capsolver_proxy)
                    processed += 1
                    if ok:
                        found += 1
            except Exception as e:
                processed += 1
                log.warning("[discovery] affiliate-search id=%s lỗi: %s", cid, e)
            async with SessionLocal() as s:
                await job_service.update_job(jid, s, total_saved=processed, total_found=found)
            await asyncio.sleep(random.uniform(4.0, 8.0))   # giãn cách tránh Google rate-limit
        async with SessionLocal() as s:
            await job_service.update_job(jid, s, status="success", total_saved=processed,
                                         total_found=found, finished_at=datetime.utcnow())
    except Exception as e:
        async with SessionLocal() as s:
            await job_service.update_job(jid, s, status="failed", error=str(e), finished_at=datetime.utcnow())
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        await browser_fetch.close_shared_browser()


@router.post("/candidates/detect-affiliate-search-batch")
async def detect_affiliate_search_batch(body: RunPipelineIn, session: AsyncSession = Depends(get_session),
                                        user: User = Depends(get_current_user)):
    """Dò affiliate qua Google search cho nhiều candidate (chạy nền; banner+bell).
    Dùng khi fetch site bị chặn (Cloudflare) hoặc affiliate ở path sâu."""
    proxy_urls = _resolve_proxy_urls(user.id, body.proxy_ids)
    ids = await _own_candidate_ids(body.candidate_ids, user, session)
    asyncio.create_task(_run_affiliate_search_batch(ids, proxy_urls, user.id))
    return {"started": len(ids)}


async def _run_link_check_batch(candidate_ids: List[int], proxy_urls: Optional[List[str]] = None) -> None:
    """Background: đánh giá link affiliate (sống / 404-soft) qua CloakBrowser pool.
    Cột 'TT Link' cập nhật dần nhờ bảng tự refetch (4s)."""
    from app.services.discovery import browser_fetch
    from app.services.discovery.affiliate_detector import check_affiliate_link_status
    try:
        await browser_fetch.set_proxies(proxy_urls or [])
        for cid in candidate_ids:
            try:
                async with SessionLocal() as s:
                    c = await s.get(DiscoveryCandidate, cid)
                    if c and c.affiliate_url:
                        c.affiliate_url_status = await check_affiliate_link_status(c.affiliate_url)
                        c.updated_at = datetime.utcnow()
                        await s.commit()
            except Exception as e:
                log.warning("[discovery] link-check id=%s lỗi: %s", cid, e)
    finally:
        await browser_fetch.close_shared_browser()


@router.post("/candidates/check-affiliate-link-batch")
async def check_affiliate_link_batch(body: RunPipelineIn, session: AsyncSession = Depends(get_session),
                                     user: User = Depends(get_current_user)):
    """Đánh giá link affiliate cho nhiều candidate (chạy nền; cột TT Link cập nhật dần)."""
    proxy_urls = _resolve_proxy_urls(user.id, body.proxy_ids)
    ids = await _own_candidate_ids(body.candidate_ids, user, session)
    asyncio.create_task(_run_link_check_batch(ids, proxy_urls))
    return {"started": len(ids)}


async def _run_pipeline_batch(candidate_ids: List[int], proxy_urls: Optional[List[str]] = None) -> None:
    """Background: full pipeline (homepage → traffic → affiliate) per candidate.
    Each step uses its own session; the table shows progress via live refetch."""
    from app.services.discovery import browser_fetch
    try:
        await browser_fetch.set_proxies(proxy_urls or [])
        for cid in candidate_ids:
            try:
                async with SessionLocal() as s:
                    c = await s.get(DiscoveryCandidate, cid)
                    if not c:
                        continue
                    if c.status == "discovered":
                        await resolve_homepage(cid, s)
                        await s.refresh(c)
                    if c.status == "homepage_resolved":
                        await _scan_candidate_traffic(c, s)
                        await s.refresh(c)
                    if c.status in ("homepage_resolved", "traffic_scanned"):
                        await detect_affiliate(cid, s)
            except Exception as e:
                log.warning("[discovery] pipeline id=%s lỗi: %s", cid, e)
    finally:
        await browser_fetch.close_shared_browser()


@router.post("/candidates/run-pipeline")
async def run_pipeline(body: RunPipelineIn, session: AsyncSession = Depends(get_session),
                       user: User = Depends(get_current_user)):
    """Kick off the full pipeline in the background. Returns immediately."""
    proxy_urls = _resolve_proxy_urls(user.id, body.proxy_ids)
    ids = await _own_candidate_ids(body.candidate_ids, user, session)
    asyncio.create_task(_run_pipeline_batch(ids, proxy_urls))
    return {"started": len(ids)}


# ─── Blacklist ────────────────────────────────────────────────────────────────

@router.get("/blacklist", response_model=List[BlacklistOut])
async def list_blacklist(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(DomainBlacklist).order_by(DomainBlacklist.domain)
    )).scalars().all()
    return [_blacklist_out(r) for r in rows]


@router.post("/blacklist", response_model=BlacklistOut, status_code=201)
async def add_blacklist(body: BlacklistIn, session: AsyncSession = Depends(get_session)):
    domain = body.domain.strip().lower().removeprefix("www.")
    existing = (await session.execute(
        select(DomainBlacklist).where(DomainBlacklist.domain == domain)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "Domain đã có trong blacklist")
    entry = DomainBlacklist(domain=domain, category=body.category or "custom", created_at=datetime.utcnow())
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return _blacklist_out(entry)


@router.delete("/blacklist/{entry_id}", status_code=204)
async def delete_blacklist(entry_id: int, session: AsyncSession = Depends(get_session)):
    entry = (await session.execute(
        select(DomainBlacklist).where(DomainBlacklist.id == entry_id)
    )).scalar_one_or_none()
    if not entry:
        raise HTTPException(404, "Không tìm thấy")
    await session.delete(entry)
    await session.commit()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _fmt(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def _source_out(s: DiscoverySource) -> SourceOut:
    return SourceOut(
        id=s.id, url=s.url, name=s.name, category=s.category, field=s.field, status=s.status,
        is_crawling=(s.status == "crawling"),
        last_crawled_at=_fmt(s.last_crawled_at) or None,
        total_candidates_found=s.total_candidates_found or 0,
        created_at=_fmt(s.created_at),
    )


def _candidate_out(c: DiscoveryCandidate) -> CandidateOut:
    return CandidateOut(
        id=c.id, source_id=c.source_id, raw_url=c.raw_url, domain=c.domain,
        level=c.level, depth=c.depth, is_primary=bool(c.is_primary),
        detection_method=c.detection_method, suggested_name=c.suggested_name,
        source_page_url=c.source_page_url, source_page_title=c.source_page_title,
        homepage_url=c.homepage_url, is_redirect_resolved=bool(c.is_redirect_resolved),
        traffic_monthly=c.traffic_monthly, traffic_status=c.traffic_status,
        affiliate_url=c.affiliate_url, affiliate_detection_method=c.affiliate_detection_method,
        affiliate_url_status=c.affiliate_url_status,
        ad_days_shown=c.ad_days_shown,
        ad_first_shown=_fmt(c.ad_first_shown) or None,
        ad_last_shown=_fmt(c.ad_last_shown) or None,
        status=c.status, promoted_program_id=c.promoted_program_id,
        created_at=_fmt(c.created_at), updated_at=_fmt(c.updated_at),
    )


def _blacklist_out(b: DomainBlacklist) -> BlacklistOut:
    return BlacklistOut(id=b.id, domain=b.domain, category=b.category, created_at=_fmt(b.created_at))
