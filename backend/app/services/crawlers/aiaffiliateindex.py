"""AI Affiliate Index crawler.

Nguồn: https://aiaffiliateindex.com/affiliate-programs (Laravel/Inertia SPA).
Toàn bộ dữ liệu nằm trong thuộc tính HTML `data-page="..."` (JSON đã
HTML-entity-encode) → fetch bằng httpx thường, không cần browser.

Trang danh sách trả về ~148 program trong 1 lần tải. Mỗi program:
  name, slug, website_url, tool_view_url, commission_summary/details,
  commission_types, cookie_duration_days, minimum_payout_amount,
  affiliate_network, categories, logo_url ...

Hai URL người dùng cần:
  • "Join AI Affiliate Program" = website_url  → signup/affiliate link.
  • "View AI tool"              = tool_view_url → trang awesomeaitools; trang
    chủ THẬT của brand nằm ở `props.tool.external_url` trong trang đó.

Vì vậy homepage lấy từ trang tool_view_url (external_url) — chính xác cả với
các website_url host trên network (lemonsqueezy/firstpromoter…). Nếu program
không có tool_view_url thì fallback về domain của website_url.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.core.logger import get_logger
from .base import BaseCrawler

log = get_logger("crawler.aiaffiliateindex")

LIST_URL = "https://aiaffiliateindex.com/affiliate-programs"
DETAIL_BASE = "https://aiaffiliateindex.com/affiliate"

# awesomeaitools rate-limit (Cloudflare 520) khi bị bắn dồn/song song, nhưng
# chịu được request tuần tự có giãn cách → chỉ resolve khi thật cần + spacing.
RESOLVE_DELAY = 1.2   # giây giữa 2 request awesomeaitools
RESOLVE_RETRIES = 3

# Các host là "network/affiliate platform" — domain của website_url KHÔNG phải
# trang chủ brand → cần resolve trang chủ thật qua trang "View AI tool".
_NETWORK_HOSTS = (
    "lemonsqueezy.com", "firstpromoter.com", "tolt.io", "rewardful.com",
    "getrewardful.com", "partnerstack.com", "shareasale.com", "goaffpro.com",
    "refersion.com", "impact.com", "impactradius", "gumroad.com",
)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://aiaffiliateindex.com/",
}

_DATA_PAGE_RE = re.compile(r'data-page="([^"]+)"')


def _parse_inertia(text: str) -> Optional[dict]:
    m = _DATA_PAGE_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(html.unescape(m.group(1)))
    except Exception:
        return None


def _homepage_fallback(website_url: str) -> Optional[str]:
    u = urlparse(website_url or "")
    if u.scheme and u.netloc:
        return f"{u.scheme}://{u.netloc}/"
    return None


def _is_network_hosted(website_url: str) -> bool:
    host = (urlparse(website_url or "").netloc or "").lower()
    return any(n in host for n in _NETWORK_HOSTS)


def _commission(summary: Optional[str], details: Optional[str]):
    text = (details or summary or "").strip() or None
    src = f"{summary or ''} {details or ''}"
    val: Optional[float] = None
    ctype: Optional[str] = None
    mp = re.search(r"(\d+(?:\.\d+)?)\s*%", src)
    md = re.search(r"\$\s*(\d+(?:\.\d+)?)", src)
    if mp:
        val, ctype = float(mp.group(1)), "percent"
    elif md:
        val, ctype = float(md.group(1)), "fixed"
    return text, val, ctype


class AIAffiliateIndexCrawler(BaseCrawler):
    source = "aiaffiliateindex"
    source_url = LIST_URL

    def __init__(self, **_: object) -> None:
        pass

    async def crawl(self) -> List[Dict]:
        async with httpx.AsyncClient(timeout=25.0, headers=_HEADERS, follow_redirects=True) as client:
            r = await client.get(LIST_URL)
            if r.status_code != 200:
                log.warning("AIAffiliateIndex list HTTP %d", r.status_code)
                return []
            data = _parse_inertia(r.text)
            progs = ((data or {}).get("props") or {}).get("programs") or []
            log.info("AIAffiliateIndex: %d program từ trang danh sách", len(progs))

            # Trang chủ mặc định = domain của website_url (đúng cho ~130/148).
            # Chỉ với website_url host trên affiliate-network mới cần resolve trang
            # chủ thật qua "View AI tool" — resolve TUẦN TỰ + giãn cách để tránh
            # bị awesomeaitools chặn (Cloudflare 520 khi bắn dồn/song song).
            homepages: List[Optional[str]] = []
            resolved = 0
            for p in progs:
                website_url = p.get("website_url") or ""
                if _is_network_hosted(website_url) and (p.get("tool_view_url") or "").strip():
                    hp = await self._resolve_homepage(client, p["tool_view_url"])
                    if hp:
                        resolved += 1
                    homepages.append(hp or _homepage_fallback(website_url))
                    await asyncio.sleep(RESOLVE_DELAY)
                else:
                    homepages.append(_homepage_fallback(website_url))
            log.info("AIAffiliateIndex: resolve trang chủ thật cho %d program network-hosted", resolved)

        rows: List[Dict] = []
        seen: set[str] = set()
        for p, hp in zip(progs, homepages):
            row = self._to_row(p, hp)
            if row and row["external_id"] not in seen:
                seen.add(row["external_id"])
                rows.append(row)
        log.info("AIAffiliateIndex: %d program hợp lệ", len(rows))
        return rows

    async def _resolve_homepage(self, client: httpx.AsyncClient, tool_view_url: str) -> Optional[str]:
        """Lấy trang chủ thật từ trang awesomeaitools (props.tool.external_url).
        Retry có backoff vì awesomeaitools hay trả 520 tạm thời."""
        for attempt in range(RESOLVE_RETRIES):
            try:
                rr = await client.get(tool_view_url)
                if rr.status_code == 200:
                    d = _parse_inertia(rr.text)
                    ext = (((d or {}).get("props") or {}).get("tool") or {}).get("external_url")
                    ext = (ext or "").strip()
                    if ext.startswith("http") and "awesomeaitools.com" not in ext:
                        return ext
                    return None  # trang OK nhưng không có external_url → khỏi retry
            except Exception as e:
                log.debug("resolve homepage lỗi (%s): %s", tool_view_url, e)
            await asyncio.sleep(RESOLVE_DELAY * (attempt + 1))
        return None

    def _to_row(self, p: Dict, homepage: Optional[str]) -> Optional[Dict]:
        name = (p.get("name") or "").strip()
        slug = (p.get("slug") or "").strip()
        website_url = (p.get("website_url") or "").strip() or None
        if not name or not slug:
            return None
        url = homepage or _homepage_fallback(website_url or "") or website_url
        commission, cval, ctype = _commission(p.get("commission_summary"), p.get("commission_details"))
        cats = p.get("categories") or []
        category = cats[0] if isinstance(cats, list) and cats else None
        days = p.get("cookie_duration_days")
        cookie = f"{days} days" if days else None
        return {
            "source": "aiaffiliateindex",
            "external_id": f"aiaffiliateindex:{slug}",
            "name": name,
            "url": url,
            "signup_url": website_url,
            "category": category,
            "commission": commission,
            "commission_value": cval,
            "commission_type": ctype,
            "payout": (p.get("minimum_payout_amount") or None),
            "cookie_duration": cookie,
            "description": (p.get("tagline") or None),
            "logo_url": (p.get("logo_url") or None),
            "directory_network": (p.get("affiliate_network") or None),
            "raw_json": json.dumps(p, ensure_ascii=False),
            "source_url": f"{DETAIL_BASE}/{slug}",
            "crawled_at": datetime.utcnow(),
        }
