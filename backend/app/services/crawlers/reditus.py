"""Reditus Marketplace crawler.

Nguồn: https://app.getreditus.com/marketplace — app Next.js (React Server Components).
Dữ liệu chương trình nằm sẵn trong các chunk `self.__next_f.push([1,"..."])` dạng JSON
→ fetch httpx thường, KHÔNG cần browser.

Mỗi program có sẵn:
  name, endpoint (URL sản phẩm), commission_percentage_range [min,max],
  commission_length_in_months_range [.. ] (rỗng = Lifetime), cookie_expires_in (ngày),
  payout_threshold (cents → ngưỡng payout tối thiểu), avg_revenue_per_account (CPA ước tính),
  category (Marketing/Sales/Accounting…), short_description, brand_logo, slug, auto_accept.

Mapping 2 tầng: category (rộng) = "SP số" (đều là B2B SaaS); sub_category (hẹp) = category
của Reditus. url = endpoint (trang sản phẩm); signup_url = trang join trên Reditus.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from app.core.logger import get_logger
from .base import BaseCrawler

log = get_logger("crawler.reditus")

BASE_URL = "https://app.getreditus.com"
LIST_URL = "https://app.getreditus.com/marketplace"
BROAD_CATEGORY = "SP số"  # Reditus toàn B2B SaaS → nhóm rộng = sản phẩm số

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')


def _rsc_blob(html: str) -> str:
    """Gom các chunk RSC (__next_f) → unescape (json.loads từng chuỗi) → 1 chuỗi lớn."""
    parts: List[str] = []
    for m in _CHUNK_RE.finditer(html):
        try:
            parts.append(json.loads(m.group(1)))
        except Exception:
            continue
    return "".join(parts)


def _extract_programs(blob: str) -> List[Dict]:
    """Tách các object program (có endpoint + slug + commission_percentage_range) từ blob RSC.
    Cắt object theo cân bằng ngoặc rồi json.loads; dedupe theo slug."""
    out: List[Dict] = []
    seen: set[str] = set()
    for m in re.finditer(r'\{', blob):
        start = m.start()
        depth = 0
        end = -1
        for i in range(start, min(start + 6000, len(blob))):
            c = blob[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            continue
        frag = blob[start:end + 1]
        if ('"endpoint"' not in frag or '"slug"' not in frag
                or '"commission_percentage_range"' not in frag):
            continue
        try:
            o = json.loads(frag)
        except Exception:
            continue
        slug = o.get("slug")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        out.append(o)
    return out


def _commission(p: Dict):
    """→ (text 'X-Y% for N mo', commission_value=max%, 'percent')."""
    pr = p.get("commission_percentage_range") or []
    lr = p.get("commission_length_in_months_range") or []
    pct = None
    if len(pr) >= 2 and pr[0] != pr[1]:
        pct = f"{pr[0]}-{pr[1]}%"
    elif pr:
        pct = f"{pr[0]}%"
    if not lr:
        dur = "Lifetime"
    elif any(not isinstance(v, (int, float)) for v in lr):
        dur = str(lr[0])                       # vd "Lifetime" — không thêm "mo"
    elif len(lr) >= 2 and lr[0] != lr[1]:
        dur = f"{lr[0]}-{lr[1]} mo"
    else:
        dur = f"{lr[0]} mo"
    text = f"{pct} for {dur}" if pct else None
    cval = float(pr[-1]) if pr else None   # đầu cao của range (headline "up to")
    return text, cval, ("percent" if pct else None)


class ReditusCrawler(BaseCrawler):
    source = "reditus"
    source_url = LIST_URL

    def __init__(self, **_: object) -> None:
        pass

    async def crawl(self) -> List[Dict]:
        log.info("Reditus crawl start")
        async with httpx.AsyncClient(timeout=25.0, headers=_HEADERS, follow_redirects=True) as client:
            try:
                r = await client.get(LIST_URL)
            except Exception as e:
                log.warning("Reditus lỗi tải trang: %s", e)
                return []
        if r.status_code != 200:
            log.warning("Reditus HTTP %d", r.status_code)
            return []
        programs = _extract_programs(_rsc_blob(r.text))
        rows = [row for row in (self._to_row(p) for p in programs) if row]
        log.info("Reditus: %d chương trình", len(rows))
        return rows

    def _to_row(self, p: Dict) -> Optional[Dict]:
        name = (p.get("name") or "").strip()
        slug = (p.get("slug") or "").strip()
        if not name or not slug:
            return None
        commission, cval, ctype = _commission(p)
        days = p.get("cookie_expires_in")
        cookie = f"{days} days" if days else None
        cur = (p.get("preferred_currency") or "usd").upper()
        thr = p.get("payout_threshold")
        payout = None
        payout_min = None
        if isinstance(thr, (int, float)) and thr:
            payout_min = round(thr / 100.0, 2)
            sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(cur, "")
            payout = f"{sym}{payout_min:g}" if sym else f"{payout_min:g} {cur}"
        return {
            "source": "reditus",
            "external_id": f"reditus:{slug}",
            "name": name,
            "url": (p.get("endpoint") or None),                    # trang sản phẩm (point 3)
            "signup_url": f"{BASE_URL}/marketplace/{slug}",         # join qua Reditus
            "category": BROAD_CATEGORY,                             # nhóm rộng
            "sub_category": (p.get("category") or None),            # ngách (Marketing/Sales…)
            "commission": commission,
            "commission_value": cval,
            "commission_type": ctype,
            "payout": payout,
            "payout_min": payout_min,
            "payout_currency": cur,
            "cookie_duration": cookie,
            "description": (p.get("short_description") or None),
            "logo_url": (p.get("brand_logo") or None),
            "directory_network": "Reditus",
            "directory_approval": ("auto" if p.get("auto_accept") else "manual"),
            "raw_json": json.dumps(p, ensure_ascii=False),
            "source_url": f"{BASE_URL}/marketplace/{slug}",
            "crawled_at": datetime.utcnow(),
        }
