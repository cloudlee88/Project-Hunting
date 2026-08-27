"""GrowthHero Affiliate Program Marketplace crawler.

Data lấy trực tiếp từ public JSON API:
  https://api.growthhero.io/api/marketplace_listings?page=N&page_limit=M
→ nhanh, ổn định, không cần browser (khác lần đầu tưởng phải scrape SPA).

Mỗi listing:
  store: {slug, name, storeCategory, ...}
  slug: program slug (vd "standard")
  affiliateReward.attrs.amount: commission %
  customerReward: giảm giá khách

Link đăng ký (signup/affiliate) — dạng trung gian, cần discover homepage như
nguồn Lovable:
  https://app.growthhero.io/#/c/{store.slug}/registration/{slug}?marketplace_listing=true
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from app.core.logger import get_logger
from .base import BaseCrawler

log = get_logger("crawler.growthhero")

API_URL = "https://api.growthhero.io/api/marketplace_listings"
APP_BASE = "https://app.growthhero.io"
PUBLIC_URL = "https://www.growthhero.io/affiliate_program_marketplace/"
PAGE_LIMIT = 100  # listings / API page

GROWTHHERO_MAX_CHOICES = [
    {"value": "100", "label": "100 chương trình"},
    {"value": "300", "label": "300 chương trình"},
    {"value": "1000", "label": "1000 chương trình"},
    {"value": "5000", "label": "5000 (rất nhiều)"},
]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Origin": "https://app.growthhero.io",
    "Referer": "https://app.growthhero.io/",
}


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _register_url(store_slug: str, prog_slug: str) -> str:
    return f"{APP_BASE}/#/c/{store_slug}/registration/{prog_slug or 'standard'}?marketplace_listing=true"


def _commission(reward: Optional[dict]) -> tuple[Optional[str], Optional[float]]:
    if not isinstance(reward, dict):
        return None, None
    typ = reward.get("type") or ""
    amt = (reward.get("attrs") or {}).get("amount")
    if amt in (None, ""):
        return None, None
    try:
        val = float(amt)
    except Exception:
        val = None
    if "percent" in typ:
        return f"{amt}%", val
    if "amount" in typ or "fixed" in typ or "flat" in typ:
        return f"{amt}", val
    return f"{amt}%", val  # mặc định coi là %


def _customer_discount(reward: Optional[dict]) -> Optional[str]:
    if not isinstance(reward, dict):
        return None
    typ = reward.get("type") or ""
    if typ == "no_reward":
        return "No Customer Discount"
    amt = (reward.get("attrs") or {}).get("amount")
    if amt not in (None, ""):
        return f"{amt}% New Customer Discount" if "percent" in typ else f"{amt} New Customer Discount"
    return None


class GrowthHeroCrawler(BaseCrawler):
    source = "growthhero"
    source_url = PUBLIC_URL

    def __init__(self, max_programs: str = "300", **_: object) -> None:
        try:
            self.limit = max(1, min(20000, int(max_programs)))
        except Exception:
            self.limit = 300

    async def crawl(self) -> List[Dict]:
        rows: List[Dict] = []
        seen: set[str] = set()
        log.info("GrowthHero crawl start (API, limit=%d)", self.limit)
        async with httpx.AsyncClient(timeout=25.0, headers=_HEADERS, follow_redirects=True) as client:
            page = 1
            total_pages = 1
            while len(rows) < self.limit:
                try:
                    r = await client.get(API_URL, params={"page": page, "page_limit": PAGE_LIMIT})
                except Exception as e:
                    log.warning("GrowthHero API page %d lỗi: %s", page, e)
                    break
                if r.status_code != 200:
                    log.warning("GrowthHero API page %d HTTP %d", page, r.status_code)
                    break
                data = r.json()
                total_pages = data.get("totalPages") or total_pages
                listings = data.get("listings") or []
                if not listings:
                    break
                for it in listings:
                    row = self._to_row(it)
                    if row and row["external_id"] not in seen:
                        seen.add(row["external_id"])
                        rows.append(row)
                        if len(rows) >= self.limit:
                            break
                if page >= total_pages:
                    break
                page += 1
                await asyncio.sleep(0.3)  # lịch sự
        log.info("GrowthHero: %d programs (từ %d trang API)", len(rows), page)
        return rows

    def _to_row(self, it: Dict) -> Optional[Dict]:
        store = it.get("store") or {}
        name = (store.get("name") or "").strip()
        if not name:
            return None
        store_slug = (store.get("slug") or _slugify(name)).strip()
        prog_slug = (it.get("slug") or "standard").strip()
        reg_url = _register_url(store_slug, prog_slug)
        commission, cval = _commission(it.get("affiliateReward"))
        discount = _customer_discount(it.get("customerReward"))
        desc_parts = [p for p in [discount, (store.get("storeDescription") or "").strip()] if p]
        return {
            "source": "growthhero",
            "external_id": f"growthhero:{store_slug}:{prog_slug}",
            "name": name,
            # URL trung gian (trang đăng ký GrowthHero) → discover-homepages sẽ
            # tìm trang chủ thật của merchant qua tên, giống nguồn Lovable.
            "url": reg_url,
            "signup_url": reg_url,
            "category": (store.get("storeCategory") or None),
            "commission": commission,
            "commission_value": cval,
            "commission_type": None,
            "payout": None,
            "cookie_duration": None,
            "description": " · ".join(desc_parts) or None,
            "logo_url": (store.get("logo") or None),
            "directory_network": "growthhero",
            "raw_json": json.dumps(it, ensure_ascii=False),
            "source_url": PUBLIC_URL,
            "crawled_at": datetime.utcnow(),
        }
