"""Đếm số nhà quảng cáo (Google Ads Transparency) đã chạy QC cho 1 domain.

Dùng SerpAPI `text=<domain>` → ad_creatives, mỗi ad có advertiser_id + advertiser (tên).
Đếm distinct advertiser_id = số NQC. 1 call/domain (num=100). `has_more` = còn trang sau
(số đếm là TỐI THIỂU của trang đầu — đủ làm tín hiệu, khỏi phân trang tốn credit).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from app.core.logger import get_logger
from app.services.serpapi import search_ads_transparency

log = get_logger("ads_advertisers")


def extract_domain(url: str) -> str:
    """URL/host → domain trần (bỏ www, path, port)."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = parsed.netloc or parsed.path.split("/")[0]
        return host.removeprefix("www.").split(":")[0].lower()
    except Exception:
        return (url or "").strip().lower()


async def count_advertisers_for_domain(
    domain: str, start_date: str = "", end_date: str = ""
) -> dict:
    """Trả {domain, count, has_more, advertisers:[{advertiser_id, advertiser}], found}.

    start_date/end_date: YYYYMMDD (rỗng = mọi thời gian). Chỉ đếm NQC có QC trong khoảng.
    """
    dom = extract_domain(domain)
    if not dom:
        return {"domain": domain, "count": 0, "has_more": False, "advertisers": [], "found": False}

    data = await search_ads_transparency(
        text=dom, region="", num=100, start_date=start_date or "", end_date=end_date or "",
    )
    ads = data.get("ad_creatives") or []
    seen: dict[str, str] = {}   # advertiser_id -> advertiser name (giữ tên đầu tiên gặp)
    for a in ads:
        aid = a.get("advertiser_id") or ""
        if not aid:
            continue
        if aid not in seen:
            seen[aid] = a.get("advertiser") or ""
    has_more = bool((data.get("serpapi_pagination") or {}).get("next_page_token"))
    advertisers = [{"advertiser_id": aid, "advertiser": name} for aid, name in seen.items()]
    return {
        "domain": dom,
        "count": len(advertisers),
        "has_more": has_more,
        "advertisers": advertisers,
        "found": len(ads) > 0,
    }
