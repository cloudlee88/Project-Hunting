"""Google Ads Transparency Center — search endpoint thin wrapper SerpAPI."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session, SessionLocal
from app.core.logger import get_logger
from app.deps import get_current_user
from app.models.ads_search_history import AdsSearchHistory
from app.models.user import User
from app.services.serpapi import get_ad_details, search_ads_transparency

log = get_logger("ads_transparency")

router = APIRouter(prefix="/api/ads-transparency", tags=["ads-transparency"],
                   dependencies=[Depends(get_current_user)])


class SearchAdsRequest(BaseModel):
    text: str = ""
    advertiser_id: str = ""
    platform: str = ""           # SEARCH | YOUTUBE | PLAY | MAPS | SHOPPING
    creative_format: str = ""    # text | image | video
    start_date: str = ""         # YYYYMMDD
    end_date: str = ""           # YYYYMMDD
    region: str = ""             # e.g. "2704" cho VN
    political_ads: bool = False
    num: int = Field(40, ge=1, le=100)
    next_page_token: str = ""


class AdDetailsRequest(BaseModel):
    advertiser_id: str
    creative_id: str
    region: str = ""


def _handle_serpapi_error(exc: httpx.HTTPStatusError) -> None:
    code = exc.response.status_code
    try:
        detail = exc.response.json().get("error", exc.response.text)
    except Exception:
        detail = exc.response.text
    if code == 400:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"SerpAPI từ chối: {detail}")
    if code == 401:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "SerpAPI key không hợp lệ")
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"SerpAPI lỗi {code}: {detail}")


@router.post("/search")
async def search(
    req: SearchAdsRequest,
    user: User = Depends(get_current_user),
    s: AsyncSession = Depends(get_session),
):
    if not req.text and not req.advertiser_id and not req.next_page_token:
        raise HTTPException(400, "Cần `text` (domain/tên) hoặc `advertiser_id` để search")
    try:
        data = await search_ads_transparency(
            text=req.text,
            advertiser_id=req.advertiser_id,
            platform=req.platform,
            creative_format=req.creative_format,
            start_date=req.start_date,
            end_date=req.end_date,
            region=req.region,
            political_ads=req.political_ads,
            num=req.num,
            next_page_token=req.next_page_token,
        )
    except httpx.HTTPStatusError as exc:
        _handle_serpapi_error(exc)
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    # Chỉ lưu history khi là trang đầu và có text/advertiser_id
    if not req.next_page_token and (req.text or req.advertiser_id):
        try:
            count = len(data.get("ad_creatives") or [])
            s.add(AdsSearchHistory(
                user_id=user.id,
                text=req.text,
                advertiser_id=req.advertiser_id,
                platform=req.platform,
                creative_format=req.creative_format,
                region=req.region,
                start_date=req.start_date,
                end_date=req.end_date,
                num=req.num,
                political_ads=req.political_ads,
                result_count=count,
                results_json=json.dumps(data, ensure_ascii=False),
            ))
            await s.commit()
        except Exception as e:
            log.warning("Lưu history thất bại: %s", e)
    return data


@router.post("/ad-details")
async def ad_details(req: AdDetailsRequest):
    try:
        data = await get_ad_details(req.advertiser_id, req.creative_id, req.region)
    except httpx.HTTPStatusError as exc:
        _handle_serpapi_error(exc)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return data


class ExportToDiscoveryRequest(BaseModel):
    advertiser_id: str
    advertiser_name: str = ""
    creatives: list[dict] = Field(default_factory=list)
    start_date: str = ""      # YYYYMMDD — lọc quảng cáo theo khoảng ngày (khi all_time=False)
    end_date: str = ""        # YYYYMMDD
    all_time: bool = False     # True = lấy TẤT CẢ quảng cáo mọi thời gian (bỏ lọc ngày)


async def _run_ads_export_job(advertiser_id: str, advertiser_name: str,
                              creatives: list[dict], user_id: int | None,
                              start_date: str = "", end_date: str = "",
                              all_time: bool = False) -> None:
    """Chạy nền: trích domain (visurl + Gemini-vision) rồi tạo candidate Discovery.
    Vision có thể chậm/flaky (Gemini 503) nên KHÔNG chạy đồng bộ trong request →
    tránh timeout phía client. Tiến độ/kết quả báo qua chuông (CrawlJob).

    all_time=False → chỉ lấy quảng cáo trong [start_date, end_date] (đúng khung ngày
    user đang xem). all_time=True → lấy TẤT CẢ quảng cáo mọi thời gian."""
    from app.models.discovery import DiscoverySource, DiscoveryCandidate
    from app.services.ads_domain import extract_domains
    from app.services.discovery.crawler import _save_candidate_if_new
    from app.services import job_service

    # Phân trang lấy toàn bộ quảng cáo KHỚP bộ lọc (ngày) thay vì chỉ creative FE đã tải.
    sd = "" if all_time else (start_date or "")
    ed = "" if all_time else (end_date or "")
    all_ads: list[dict] = []
    seen_ids: set[str] = set()
    token = ""
    for _ in range(12):  # cap ~ vài trăm ad
        try:
            data = await search_ads_transparency(advertiser_id=advertiser_id, num=100,
                                                 start_date=sd, end_date=ed, next_page_token=token)
        except Exception as e:
            log.warning("export: lấy trang ad lỗi: %s", e)
            break
        page = data.get("ad_creatives") or []
        for a in page:
            cid = a.get("ad_creative_id")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                all_ads.append(a)
        token = (data.get("pagination") or {}).get("next_page_token") or \
                (data.get("serpapi_pagination") or {}).get("next_page_token") or ""
        if not token or not page:
            break
    if not all_ads:
        all_ads = creatives or []   # fallback: dùng creative FE gửi lên
    total_ads = len(all_ads)

    async with SessionLocal() as s:
        job = await job_service.create_job(
            "discovery_ads_export", s, user_id=user_id,
            params={"total": total_ads, "advertiser": advertiser_name or advertiser_id},
        )
        jid = job.id
        await job_service.update_job(jid, s, status="running", started_at=datetime.utcnow())
    try:
        dom_map = await extract_domains(all_ads)
        domains = sorted({d for d in dom_map.values() if d})
        # Mỗi domain: lấy quảng cáo tiêu biểu (chạy lâu nhất) → số ngày hiển thị + lần đầu/cuối.
        def _ts(v):
            try:
                return datetime.utcfromtimestamp(int(v)) if v else None
            except Exception:
                return None
        dom_meta: dict[str, dict] = {}
        for a in all_ads:
            dom = dom_map.get(a.get("ad_creative_id"))
            if not dom:
                continue
            days = a.get("total_days_shown")
            days = days if isinstance(days, int) else None
            cur = dom_meta.get(dom)
            if cur is None or (days is not None and days > (cur.get("days") or -1)):
                dom_meta[dom] = {"days": days, "first": _ts(a.get("first_shown")), "last": _ts(a.get("last_shown"))}
        src_url = f"https://adstransparency.google.com/advertiser/{advertiser_id}"
        src_name = f"Google Ads: {advertiser_name or advertiser_id}"[:255]
        new_count = 0
        if domains:
            async with SessionLocal() as s:
                source = (await s.execute(
                    select(DiscoverySource).where(DiscoverySource.url == src_url,
                                                 DiscoverySource.user_id == user_id)
                )).scalar_one_or_none()
                if source is None:
                    source = DiscoverySource(url=src_url, name=src_name, status="active", user_id=user_id)
                    s.add(source)
                    await s.commit()
                    await s.refresh(source)
                source_id = source.id
            for dom in domains:
                meta = dom_meta.get(dom) or {}
                if await _save_candidate_if_new(
                    source_id=source_id, raw_url=f"https://{dom}", domain=dom,
                    level=1, depth=1, detection_method="google_ads",
                    suggested_name=(advertiser_name or None), source_page_url=src_url,
                    source_page_title=None, user_id=user_id,
                    ad_days_shown=meta.get("days"),
                    ad_first_shown=meta.get("first"),
                    ad_last_shown=meta.get("last"),
                ):
                    new_count += 1
            # Backfill/làm mới ad-metadata cho candidate ĐÃ tồn tại (dedup bỏ qua ở trên):
            # số ngày hiển thị / lần đầu / lần cuối có thể đổi theo thời gian.
            async with SessionLocal() as s:
                for dom, meta in dom_meta.items():
                    if meta.get("days") is None and not meta.get("first") and not meta.get("last"):
                        continue
                    await s.execute(
                        sql_update(DiscoveryCandidate)
                        .where(DiscoveryCandidate.user_id == user_id,
                               DiscoveryCandidate.domain == dom)
                        .values(ad_days_shown=meta.get("days"),
                                ad_first_shown=meta.get("first"),
                                ad_last_shown=meta.get("last"))
                    )
                await s.commit()
        async with SessionLocal() as s:
            await job_service.update_job(
                jid, s, status="success",
                total_found=len(domains), total_saved=new_count,
                finished_at=datetime.utcnow(),
            )
        log.info("export-to-discovery: NQC=%s → %d domain (%d mới) từ %d ad",
                 advertiser_id, len(domains), new_count, total_ads)
    except Exception as e:
        log.exception("export-to-discovery job lỗi")
        async with SessionLocal() as s:
            await job_service.update_job(jid, s, status="failed", error=str(e),
                                         finished_at=datetime.utcnow())


@router.post("/export-to-discovery")
async def export_to_discovery(req: ExportToDiscoveryRequest, user: User = Depends(get_current_user)):
    """Kick off nền: trích domain quảng cáo của 1 NQC (visurl + Gemini-vision cho
    ảnh) rồi tạo candidate trong nguồn Discovery 'Google Ads: {NQC}'. Trả về ngay;
    kết quả báo ở chuông (vì vision có thể mất vài chục giây)."""
    if not req.creatives:
        raise HTTPException(400, "Không có quảng cáo để trích domain")
    asyncio.create_task(_run_ads_export_job(
        req.advertiser_id, req.advertiser_name, req.creatives, getattr(user, "id", None),
        start_date=req.start_date, end_date=req.end_date, all_time=req.all_time,
    ))
    return {"started": len(req.creatives), "all_time": req.all_time}


# ---------- Search history ----------

@router.get("/history")
async def list_history(
    limit: int = 30,
    user: User = Depends(get_current_user),
    s: AsyncSession = Depends(get_session),
):
    limit = max(1, min(100, limit))
    rows = (await s.execute(
        select(AdsSearchHistory)
        .where(AdsSearchHistory.user_id == user.id)
        .order_by(desc(AdsSearchHistory.created_at))
        .limit(limit)
    )).scalars().all()
    return [
        {
            "id": r.id,
            "text": r.text,
            "advertiser_id": r.advertiser_id,
            "platform": r.platform,
            "creative_format": r.creative_format,
            "region": r.region,
            "start_date": r.start_date,
            "end_date": r.end_date,
            "num": r.num,
            "political_ads": r.political_ads,
            "result_count": r.result_count,
            "results_json": r.results_json,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.delete("/history/{history_id}")
async def delete_history(
    history_id: int,
    user: User = Depends(get_current_user),
    s: AsyncSession = Depends(get_session),
):
    row = (await s.execute(
        select(AdsSearchHistory).where(
            AdsSearchHistory.id == history_id,
            AdsSearchHistory.user_id == user.id,
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Không tìm thấy lịch sử")
    await s.delete(row)
    await s.commit()
    return {"ok": True}


@router.delete("/history")
async def clear_history(
    user: User = Depends(get_current_user),
    s: AsyncSession = Depends(get_session),
):
    await s.execute(delete(AdsSearchHistory).where(AdsSearchHistory.user_id == user.id))
    await s.commit()
    return {"ok": True}
