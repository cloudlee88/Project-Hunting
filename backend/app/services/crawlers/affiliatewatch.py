"""Affiliate.watch crawler.

Nguồn: https://affiliate.watch (Laravel/Inertia SPA — dữ liệu nằm trong thuộc tính
HTML `data-page="..."` dạng JSON HTML-entity-encode) → fetch bằng httpx thường,
KHÔNG cần browser.

Danh sách phân trang chuẩn `?page=N` (props.affiliates: {data:[...], last_page,
per_page=100, total=902}). Mỗi affiliate có sẵn:
  name, website (trang chủ thật), goPartner (link join affiliate qua redirect),
  all_payment_methods [{name,slug}], cookie_days, minimum_payout, categories,
  networks, teaser_affiliate (text hoa hồng, vd "50% Lifetime Commission"),
  teaser_company (tagline), launch_year, logo.

Mapping:
  • url          = website              (trang chủ thật, sạch)
  • signup_url   = goPartner            (redirect affiliate.watch → trang join)
  • commission   = teaser_affiliate     (+ tách % ra commission_value)
  • payout       = minimum_payout
  • cookie       = cookie_days
  • payment      = all_payment_methods → payout_methods_json (["PayPal","Stripe"])
  • network      = networks[0].name
"""
from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from app.core.logger import get_logger
from .base import BaseCrawler

log = get_logger("crawler.affiliatewatch")

BASE_URL = "https://affiliate.watch"
LIST_URL = "https://affiliate.watch/?sort=-launch_year&page={page}"
PER_PAGE = 100  # affiliate.watch trả 100/trang

AFFILIATEWATCH_MAX_CHOICES = [
    {"value": "100", "label": "100 chương trình"},
    {"value": "300", "label": "300 chương trình"},
    {"value": "902", "label": "Tất cả (~902)"},
]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
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


def _logo_url(it: Dict) -> Optional[str]:
    """`logo` là media-object (dict), URL thật nằm ở logoUrls.* hoặc logo.original_url."""
    lu = it.get("logoUrls")
    if isinstance(lu, dict):
        for k in ("thumb_sm", "thumb", "thumb_xs", "medium", "original", "original_url"):
            v = lu.get(k)
            if isinstance(v, str) and v.startswith("http"):
                return v
        for v in lu.values():
            if isinstance(v, str) and v.startswith("http"):
                return v
    lg = it.get("logo")
    if isinstance(lg, dict) and isinstance(lg.get("original_url"), str):
        return lg["original_url"]
    if isinstance(lg, str) and lg.startswith("http"):
        return lg
    return None


def _commission(teaser: Optional[str]):
    text = (teaser or "").strip() or None
    val: Optional[float] = None
    ctype: Optional[str] = None
    if text:
        mp = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        md = re.search(r"\$\s*(\d+(?:\.\d+)?)", text)
        if mp:
            val, ctype = float(mp.group(1)), "percent"
        elif md:
            val, ctype = float(md.group(1)), "fixed"
    return text, val, ctype


class AffiliateWatchCrawler(BaseCrawler):
    source = "affiliatewatch"
    source_url = BASE_URL

    def __init__(self, max_programs: str = "902", **_: object) -> None:
        try:
            self.limit = max(1, min(20000, int(max_programs)))
        except Exception:
            self.limit = 902

    async def crawl(self) -> List[Dict]:
        rows: List[Dict] = []
        seen: set[str] = set()
        log.info("Affiliate.watch crawl start (limit=%d)", self.limit)
        async with httpx.AsyncClient(timeout=25.0, headers=_HEADERS, follow_redirects=True) as client:
            page = 1
            last_page = 1
            while len(rows) < self.limit:
                try:
                    r = await client.get(LIST_URL.format(page=page))
                except Exception as e:
                    log.warning("Affiliate.watch trang %d lỗi: %s", page, e)
                    break
                if r.status_code != 200:
                    log.warning("Affiliate.watch trang %d HTTP %d", page, r.status_code)
                    break
                data = _parse_inertia(r.text)
                container = ((data or {}).get("props") or {}).get("affiliates") or {}
                items = container.get("data") or []
                last_page = container.get("last_page") or last_page
                if not items:
                    break
                for it in items:
                    row = self._to_row(it)
                    if row and row["external_id"] not in seen:
                        seen.add(row["external_id"])
                        rows.append(row)
                        if len(rows) >= self.limit:
                            break
                if page >= last_page:
                    break
                page += 1
                await asyncio.sleep(0.3)  # lịch sự
        log.info("Affiliate.watch: %d chương trình (từ %d trang)", len(rows), page)
        return rows

    def _to_row(self, it: Dict) -> Optional[Dict]:
        name = (it.get("name") or "").strip()
        slug = (it.get("slug") or "").strip()
        if not name or not slug:
            return None
        commission, cval, ctype = _commission(it.get("teaser_affiliate"))
        cats = it.get("categories") or []
        category = (cats[0].get("name") if cats and isinstance(cats[0], dict) else None)
        nets = it.get("networks") or []
        network = (nets[0].get("name") if nets and isinstance(nets[0], dict) else None)
        pms = [p.get("name") for p in (it.get("all_payment_methods") or []) if isinstance(p, dict) and p.get("name")]
        days = it.get("cookie_days")
        cookie = f"{days} days" if days else None
        return {
            "source": "affiliatewatch",
            "external_id": f"affiliatewatch:{slug}",
            "name": name,
            "url": (it.get("website") or None),
            "signup_url": (it.get("goPartner") or it.get("go") or None),
            "category": category,
            "commission": commission,
            "commission_value": cval,
            "commission_type": ctype,
            "payout": (it.get("minimum_payout") or None),
            "cookie_duration": cookie,
            "description": (it.get("teaser_company") or None),
            "logo_url": _logo_url(it),
            "directory_network": network,
            "launch_year": (int(it["launch_year"]) if str(it.get("launch_year") or "").isdigit() else None),
            "payout_methods_json": (json.dumps(pms, ensure_ascii=False) if pms else None),
            "raw_json": json.dumps(it, ensure_ascii=False),
            "source_url": (it.get("go") or f"{BASE_URL}/go/{slug}"),
            "crawled_at": datetime.utcnow(),
        }
