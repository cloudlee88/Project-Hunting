"""Lovable Directory crawler — crawl thật bằng CloakBrowser (Playwright).

Trang root https://affiliateprogram.lovable.app/ là SPA. Mỗi nền tảng
(rewardful, firstpromoter…) là 1 route React, đôi khi nội dung lồng
trong iframe. Crawler nhận `project` → mở CloakBrowser → JS extract
trực tiếp trong DOM (kèm fallback đi vào iframe).
"""
from __future__ import annotations
import asyncio
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.core.logger import get_logger
from app.services.browser.session import get_browser
from .base import BaseCrawler

log = get_logger("crawler.lovable")

LOVABLE_PROJECTS: List[Dict[str, str]] = [
    {"value": "_all", "label": "✨ Tất cả (8 nền tảng)"},
    {"value": "rewardful", "label": "Rewardful"},
    {"value": "firstpromoter", "label": "FirstPromoter"},
    {"value": "postaffiliatepro", "label": "Post Affiliate Pro"},
    {"value": "tolt", "label": "Tolt"},
    {"value": "taprefer", "label": "TapRefer"},
    {"value": "leaddyno", "label": "LeadDyno"},
    {"value": "everflow", "label": "Everflow"},
    {"value": "promotekit", "label": "PromoteKit"},
]
_VALID_PROJECTS = {p["value"] for p in LOVABLE_PROJECTS}
_REAL_PROJECTS = [p["value"] for p in LOVABLE_PROJECTS if p["value"] != "_all"]
_PROJECT_URLS = {
    "rewardful": "https://getrewardfuldirectory.lovable.app/",
    "firstpromoter": "https://firstpromoterdirectory.lovable.app/",
    "postaffiliatepro": "https://postaffiliateprodirectory.lovable.app/",
    "tolt": "https://toltdirectory.lovable.app/",
    "taprefer": "https://tapreferdirectory.lovable.app/",
    "leaddyno": "https://leaddynoaffiliatedirectory.lovable.app/",
    "everflow": "https://everflowdirectory.lovable.app/",
    "promotekit": "https://promotekitdirectory.lovable.app/",
}

BASE_URL = "https://affiliateprogram.lovable.app"

# Extract toàn bộ cards → trả JSON string.
# Mỗi sub-directory của Lovable (toltdirectory, getrewardfuldirectory,
# firstpromoterdirectory…) tự xây Tailwind/shadcn riêng → class khác nhau,
# nhưng STRUCTURAL ORDER giống nhau:
#   h3 (name) → status badge → slug (p.font-mono) → description
#   → traffic + popularity row → keywords → pills [commission, type, cookie]
#   → <a>Sign Up</a> (hoặc "Đăng Ký" ở leaddyno)
# ⇒ Định vị pills bằng "previousElementSibling của anchor signup".
# - Status: chuẩn hoá text (strip bullet/dot prefix) và cả element có child decoration
#   (vd promotekit: <span><span dot/></span>Active & Verified</span>) đều match được.
# - leaddyno: signup text = "Đăng Ký", cookie có thể = "30 ngày" (không có word "cookie").
_EXTRACT_JS = r"""
(() => {
  const txt = (el) => (el ? (el.textContent || '').replace(/\s+/g,' ').trim() : '');
  const SIGNUP_RE = /sign\s*up|signup|đăng\s*k[ýy]/i;
  const STATUS_ONLY = /^(active\s*&\s*verified|active|verified|inactive|moved|new|invite\s*only)$/i;

  // 1. Tìm card root = ancestor LỚN NHẤT của <h3> mà vẫn chỉ chứa 1 h3.
  //    (Walk up tới khi parent tiếp theo có >1 h3 → ancestor hiện tại là card đầy đủ.)
  //    Cách này đảm bảo lấy được toàn bộ siblings (status badge, description,
  //    commission row…) thường ở mức cha của h3-wrapper.
  //    Bỏ qua h3 không có slug + không trong card layout (vd "Đăng ký affiliate"
  //    hero text) bằng cách yêu cầu card phải chứa <p> slug khớp domain hoặc
  //    có signup link hợp lệ.
  const SLUG_RE = /^[a-z0-9_-]+(\.[a-z0-9_-]+){1,}/i;
  const isCardLike = (el) => {
    if (el.querySelector('p.font-mono')) return true;
    if (Array.from(el.querySelectorAll('p')).some(p => {
      const t = (p.textContent || '').trim();
      return t.length < 80 && SLUG_RE.test(t);
    })) return true;
    return Array.from(el.querySelectorAll('a[href]')).some(a =>
      SIGNUP_RE.test(a.textContent || '')
      && /^https?:/.test(a.href)
      && !/lovable\.dev|facebook\.com/.test(a.href)
    );
  };
  const cards = new Set();
  document.querySelectorAll('h3').forEach(h => {
    let p = h.parentElement;
    let best = null;
    for (let i = 0; i < 12 && p; i++, p = p.parentElement) {
      if (p.querySelectorAll('h3').length !== 1) break; // gặp grid → dừng
      if (isCardLike(p)) best = p;
    }
    if (best) cards.add(best);
  });

  const out = [];
  cards.forEach(card => {
    const h3 = card.querySelector('h3');
    const name = txt(h3); if (!name) return;

    // Status: scan toàn card, lấy "direct text" của element (bỏ qua text của con)
    // để vượt qua nested decoration (vd <span><span dot/></span>Active</span>).
    // Strip bullet/dot prefix trước khi match.
    let status = '';
    for (const el of card.querySelectorAll('*')) {
      if (el === h3) continue;
      const direct = Array.from(el.childNodes)
        .filter(n => n.nodeType === 3)
        .map(n => n.textContent).join('').trim();
      const candidate = direct || (el.children.length === 0 ? txt(el) : '');
      if (!candidate || candidate.length > 40) continue;
      const norm = candidate.replace(/^[^A-Za-z]+/, '').replace(/[^A-Za-z &]+$/, '').trim();
      if (STATUS_ONLY.test(norm)) { status = norm; break; }
    }

    // Slug: p.font-mono hoặc p khớp pattern domain
    const slugEl = card.querySelector('p.font-mono')
      || Array.from(card.querySelectorAll('p')).find(p => /^[a-z0-9_-]+(\.[a-z0-9_-]+){1,}/i.test(txt(p)) && txt(p).length < 80);
    const slug = txt(slugEl);

    // Description: p text dài, không phải slug, không phải keywords
    const descEl = Array.from(card.querySelectorAll('p')).find(p => {
      const t = txt(p);
      if (!t || t === slug) return false;
      if (/^(Từ khóa|Keywords?)/i.test(t)) return false;
      return t.length >= 20;
    });
    const description = txt(descEl);

    // Traffic + popularity: row flex có ĐÚNG 2 span con + svg (icon eye + trending)
    let traffic = '', popularity = '';
    const tRow = Array.from(card.querySelectorAll('div')).find(d => {
      if (!/flex/.test(d.className || '')) return false;
      const spans = d.querySelectorAll(':scope > span');
      return spans.length === 2 && d.querySelector('svg');
    });
    if (tRow) {
      const spans = tRow.querySelectorAll(':scope > span');
      traffic = txt(spans[0]); popularity = txt(spans[1]);
    }
    // Fallback: "Rank #xxx" pattern (postaffiliatepro và các trang dùng SimilarWeb rank)
    if (!traffic) {
      const rankSpan = Array.from(card.querySelectorAll('span')).find(
        s => /^Rank\s*#[\d,]+/.test(txt(s))
      );
      if (rankSpan) {
        traffic = txt(rankSpan);
        const prev = rankSpan.previousElementSibling;
        if (prev) popularity = txt(prev);
      }
    }

    // Keywords: labeled <p> first (chip fallback runs after pillsRow is set below)
    let keywords = [];
    const kwP = Array.from(card.querySelectorAll('p')).find(p => /^(Từ khóa|Keywords?)/i.test(txt(p)));
    if (kwP) {
      keywords = txt(kwP).replace(/^[^:]+:\s*/, '').split(/,\s*/).map(s => s.trim()).filter(Boolean);
    }

    // Pills row = previousElementSibling trực tiếp của anchor signup nếu có.
    // Fallback (inactive cards, không có Sign Up): pills = div cuối cùng có
    // bg-secondary span children — đây là layout chuẩn cho commission/type/cookie.
    let commission = '', commissionType = '', cookie = '';
    const signupA = Array.from(card.querySelectorAll('a[href]')).find(a => SIGNUP_RE.test(txt(a)));
    let pillsRow = null;
    if (signupA) {
      pillsRow = signupA.previousElementSibling;
    } else {
      // Tìm div pills: chứa span.bg-secondary HOẶC flex div với 2-3 children ngắn
      const cands = Array.from(card.querySelectorAll('div')).filter(d => {
        if (!/flex/.test(d.className || '')) return false;
        const kids = d.children;
        if (kids.length < 2 || kids.length > 4) return false;
        return Array.from(kids).every(c => {
          const t = txt(c);
          return t && t.length < 60 && !c.querySelector('h3');
        }) && (d.querySelector('span.bg-secondary, [class*="bg-secondary"]') || /commission|cookie|recurring|one[- ]time|lifetime|\d+\s*%|\d+\s*d/i.test(txt(d)));
      });
      // Lấy cái cuối cùng (gần signup nhất nếu có)
      pillsRow = cands[cands.length - 1] || null;
    }
    if (pillsRow) {
      const pills = Array.from(pillsRow.children)
        .map(c => txt(c))
        .filter(t => t && t.length < 60);
      commission = pills[0] || '';
      commissionType = pills[1] || '';
      cookie = pills[2] || '';
    }

    // Chip-style keywords fallback (pillsRow now defined — safe to exclude it)
    if (keywords.length === 0) {
      const chipCands = [];
      card.querySelectorAll('div,ul').forEach(d => {
        if (d === pillsRow || (pillsRow && pillsRow.contains(d))) return;
        if (d === tRow || (tRow && tRow.contains(d))) return;
        const kids = Array.from(d.children);
        if (kids.length < 2 || kids.length > 12) return;
        if (kids.some(k => /^(A|BUTTON|H[1-6]|IMG|INPUT|SELECT|TEXTAREA)$/.test(k.tagName) || k.querySelector('h1,h2,h3,h4,a,button,img'))) return;
        const texts = kids.map(k => txt(k)).filter(t => t.length > 0 && t.length < 40 && !/^\+\d+/.test(t));
        if (texts.length >= 2) chipCands.push(texts);
      });
      if (chipCands.length > 0) {
        chipCands.sort((a, b) => b.length - a.length);
        keywords = chipCands[0];
      }
    }

    const signup_url = signupA ? signupA.href : '';
    out.push({
      name, slug, status, description, traffic, popularity, keywords,
      commission, commission_type: commissionType, cookie_duration: cookie, signup_url
    });
  });
  return JSON.stringify(out);
})()
"""


def _parse_commission_value(raw: str) -> Optional[float]:
    """Lấy giá trị commission. Với range '15-30%' lấy số cao nhất để rank."""
    if not raw:
        return None
    nums = re.findall(r"\d+(?:\.\d+)?", raw)
    if not nums:
        return None
    return max(float(n) for n in nums)


def _norm_type(raw: str) -> Optional[str]:
    s = (raw or "").strip().lower()
    if not s:
        return None
    if "recurring" in s:
        return "recurring"
    if "lifetime" in s:
        return "lifetime"
    if "tiered" in s or "tier" in s:
        return "tiered"
    if "hybrid" in s:
        return "hybrid"
    if "per referral" in s or "referral" in s:
        return "per-referral"
    if "per sale" in s or "cps" in s:
        return "per-sale"
    if "per lead" in s or "cpl" in s:
        return "per-lead"
    if "per click" in s or "cpc" in s:
        return "per-click"
    if "one" in s or "one-time" in s:
        return "one-time"
    if "cpa" in s:
        return "cpa"
    if "flat" in s or "fixed" in s:
        return "flat"
    # Fallback: store raw value (capped at 32 chars) instead of dropping it
    return (raw or "").strip()[:32] or None


def _parse_lovable_traffic(raw: str) -> Optional[float]:
    """Parse cỗi traffic từ Lovable directory card → monthly visits (float).

    Ví dụ:
        "100K-300K monthly"  → 200_000.0  (trung bình dải)
        "300K-500K monthly"  → 400_000.0
        "50K monthly"        →  50_000.0
        "1M+ monthly"        → 1_500_000.0  (base * 1.5)
        "> 1M monthly"       → 1_500_000.0
        "<10K monthly"       →   5_000.0   (half of upper bound)
    """
    if not raw:
        return None
    s = raw.strip().lower()

    # "Rank #26,348" → SimilarWeb rank → estimate monthly visits
    # Formula calibrated: ~1.5M visits at rank #26,348 (PureVPN benchmark)
    rank_m = re.search(r'rank\s*#\s*([\d,]+)', s)
    if rank_m:
        rank = int(rank_m.group(1).replace(',', ''))
        if rank > 0:
            import math
            return round(8e9 / (rank ** 0.8))
        return None

    # Bỏ suffix "monthly", "visits/month", "per month"
    s = re.sub(r'\s+(monthly|visits?\s*/\s*month|per\s+month)\s*$', '', s).strip()

    _MULT = {'k': 1_000.0, 'm': 1_000_000.0, 'b': 1_000_000_000.0}

    def _to_float(n: str) -> float:
        n = n.strip().lower()
        for suffix, mult in _MULT.items():
            if n.endswith(suffix):
                try:
                    return float(n[:-1]) * mult
                except ValueError:
                    return 0.0
        try:
            return float(n)
        except ValueError:
            return 0.0

    # "<10K" hoặc "< 10K" → half of upper bound
    lt_m = re.search(r'<\s*(\d+(?:\.\d+)?\s*[kmb]?)', s)
    if lt_m:
        upper = _to_float(lt_m.group(1).replace(' ', ''))
        return upper / 2 if upper else None

    # "1M+" hoặc ">1M" hoặc "> 1M" → base * 1.5
    if '+' in s or re.search(r'>\s*[\d]', s):
        clean = s.replace('+', '').replace('>', '').strip()
        m = re.search(r'([\d.]+\s*[kmb]?)', clean)
        if m:
            base = _to_float(m.group(1).replace(' ', ''))
            return base * 1.5 if base else None

    # Dải: "100K-300K" hoặc "100K – 300K"
    range_m = re.search(r'([\d.]+\s*[kmb]?)\s*[-\u2013]\s*([\d.]+\s*[kmb]?)', s)
    if range_m:
        lo = _to_float(range_m.group(1).replace(' ', ''))
        hi = _to_float(range_m.group(2).replace(' ', ''))
        if lo and hi:
            return (lo + hi) / 2

    # Giá trị đơn: "50K"
    single_m = re.search(r'([\d.]+\s*[kmb]?)', s)
    if single_m:
        v = _to_float(single_m.group(1).replace(' ', ''))
        return v if v else None

    return None


def _norm_catalog_key(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip().lower())


def _decode_js_string(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except Exception:
        return raw.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")


def _extract_js_field(obj: str, field: str) -> str:
    m = re.search(rf'{re.escape(field)}:"((?:\\.|[^"\\])*)"', obj)
    return _decode_js_string(m.group(1)).strip() if m else ""


def _iter_program_objects(bundle: str) -> List[str]:
    objects: List[str] = []
    needle = '{name:"'
    pos = 0
    while True:
        start = bundle.find(needle, pos)
        if start < 0:
            break
        depth = 0
        in_str = False
        quote = ""
        esc = False
        end = -1
        for i in range(start, len(bundle)):
            ch = bundle[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == quote:
                    in_str = False
                continue
            if ch in ('"', "'"):
                in_str = True
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            obj = bundle[start:end]
            if 'category:"' in obj:
                objects.append(obj)
            pos = end
        else:
            pos = start + len(needle)
    return objects


async def _load_project_category_map(project: str) -> Dict[str, str]:
    base_url = _PROJECT_URLS.get(project)
    if not base_url:
        return {}
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            html = (await client.get(base_url)).text
            js_paths = re.findall(r'src="([^"]+/assets/[^"]+\.js|/assets/[^"]+\.js)"', html)
            if not js_paths:
                return {}
            js_url = js_paths[0]
            if js_url.startswith("/"):
                js_url = base_url.rstrip("/") + js_url
            bundle = (await client.get(js_url)).text
    except Exception as e:
        log.warning("Lovable category catalog load failed for %s: %s", project, e)
        return {}

    out: Dict[str, str] = {}
    for obj in _iter_program_objects(bundle):
        category = _extract_js_field(obj, "category")
        if not category:
            continue
        for key in (_extract_js_field(obj, "slug"), _extract_js_field(obj, "name")):
            nk = _norm_catalog_key(key)
            if nk:
                out[nk] = category
    log.info("Lovable category catalog %s: %d keys", project, len(out))
    return out


async def _eval_json(target, js: str) -> Any:
    """Eval JS, parse JSON string nếu cần. `target` = Playwright Page hoặc Frame."""
    try:
        raw = await target.evaluate(js)
    except Exception as e:
        log.debug("evaluate lỗi: %s", e)
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return None
    return raw if isinstance(raw, (list, dict)) else None


async def _scroll_and_extract(target, label: str = "page") -> List[Dict]:
    """Scroll-to-bottom + retry 8 lần để trigger lazy-load."""
    best: List[Dict] = []
    for attempt in range(8):
        await asyncio.sleep(1.5)
        try:
            await target.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        items = await _eval_json(target, _EXTRACT_JS) or []
        if len(items) > len(best):
            best = items
        if best and attempt >= 3 and len(items) == len(best):
            break
    log.info("Extract %s: %s items", label, len(best))
    return best


class LovableCrawler(BaseCrawler):
    source = "lovable"
    source_url = BASE_URL

    def __init__(self, project: str = "rewardful", resolve_homepages: bool = False, **_: object) -> None:
        if project not in _VALID_PROJECTS:
            raise ValueError(f"Lovable project không hỗ trợ: {project}")
        self.project = project
        self.resolve_homepages = bool(resolve_homepages)
        self.page_url = f"{BASE_URL}/{project}" if project != "_all" else BASE_URL
        self._category_by_key: Dict[str, str] = {}

    async def crawl(self) -> List[Dict]:
        # "_all" → chạy lần lượt tất cả project, gộp kết quả (de-dup theo external_id).
        if self.project == "_all":
            merged: Dict[str, Dict] = {}
            for proj in _REAL_PROJECTS:
                try:
                    sub = LovableCrawler(project=proj)
                    rows = await sub.crawl()
                    for r in rows:
                        merged[r["external_id"]] = r
                    log.info("Lovable _all: %s → %d rows (tổng %d)", proj, len(rows), len(merged))
                except Exception as e:
                    log.warning("Lovable _all: project %s lỗi: %s", proj, e)
            return list(merged.values())
        browser = await get_browser()
        if browser is None:
            raise RuntimeError("CloakBrowser chưa khả dụng (chưa cài cloakbrowser)")
        try:
            log.info("Lovable crawl start: %s", self.page_url)
            page = await browser.new_page()
            try:
                await page.goto(self.page_url, wait_until="networkidle", timeout=30000)
            except Exception as e:
                log.warning("goto networkidle timeout (%s) — fallback domcontentloaded", e)
                await page.goto(self.page_url, wait_until="domcontentloaded", timeout=30000)
            raw_items = await _scroll_and_extract(page, label=self.project)
            # Fallback: thử iframe nếu DOM gốc rỗng (Tolt, FirstPromoter…)
            if not raw_items:
                raw_items = await self._extract_from_iframes(browser, page)
            log.info("Lovable extracted %s items thô", len(raw_items))
            self._category_by_key = await _load_project_category_map(self.project)
            rows = [self._to_row(it) for it in raw_items if it.get("name")]
            if self.resolve_homepages:
                rows = await self._resolve_homepages(rows)
            else:
                log.info("Lovable homepage discovery skipped during crawl; run discover-homepages separately")
            return rows
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    async def _extract_from_iframes(self, browser, page) -> List[Dict]:
        """Mở iframe URL trong NEW PAGE (giống nodriver `new_tab=True`).

        Nhiều project (rewardful, firstpromoter...) iframe vào subdomain
        nhưng tự nó là full app — mở trực tiếp render nhanh & ổn hơn.
        """
        srcs = await _eval_json(
            page,
            "JSON.stringify(Array.from(document.querySelectorAll('iframe')).map(f => f.src).filter(Boolean))",
        ) or []
        log.info("Iframe srcs found: %s", srcs)
        for src in srcs:
            try:
                fpage = await browser.new_page()
                try:
                    try:
                        await fpage.goto(src, wait_until="networkidle", timeout=30000)
                    except Exception:
                        await fpage.goto(src, wait_until="domcontentloaded", timeout=30000)
                    items = await _scroll_and_extract(fpage, label=f"iframe-page {src[:60]}")
                    if items:
                        return items
                finally:
                    try:
                        await fpage.close()
                    except Exception:
                        pass
            except Exception as e:
                log.warning("iframe %s lỗi: %s", src, e)
        return []

    async def _resolve_homepages(self, rows: List[Dict]) -> List[Dict]:
        """Sau crawl: tìm trang chủ thực cho các row có intermediate affiliate URL.

        Chạy song song (semaphore=5). Log progress.
        """
        from app.services.crawlers.homepage_finder import is_intermediate_url, find_homepage

        candidates = [r for r in rows if is_intermediate_url(r.get("url") or "")]
        if not candidates:
            return rows
        log.info("Homepage discovery: %d/%d rows cần resolve", len(candidates), len(rows))
        sem = asyncio.Semaphore(5)

        async def _fix(row: Dict) -> None:
            async with sem:
                hp = await find_homepage(row.get("name") or "", row.get("url") or "")
            if hp:
                log.info("  %s → %s (was: %s)", row.get("name"), hp, row.get("url"))
                row["url"] = hp
            else:
                log.warning("  %s: no homepage found", row.get("name"))

        await asyncio.gather(*(_fix(r) for r in candidates))
        return rows

    def _to_row(self, it: Dict) -> Dict:
        name: str = (it.get("name") or "").strip()
        slug: str = (it.get("slug") or "").strip().rstrip(".")
        ext = f"{self.project}:{slug or name.lower().replace(' ', '-')}"
        keywords = it.get("keywords") or []
        url = ""
        if slug and "." in slug:
            url = f"https://{slug.split('…')[0]}"
        commission = (it.get("commission") or "").strip()
        category = (
            (it.get("category") or "").strip()
            or self._category_by_key.get(_norm_catalog_key(slug))
            or self._category_by_key.get(_norm_catalog_key(name))
            or None
        )
        return {
            "source": self.source,
            "external_id": ext,
            "name": name,
            "url": url or it.get("signup_url"),
            "signup_url": it.get("signup_url") or "",
            "category": category,
            "directory_network": self.project,
            "commission": commission or None,
            "commission_value": _parse_commission_value(commission),
            "commission_type": _norm_type(it.get("commission_type") or ""),
            "payout": None,
            "cookie_duration": (it.get("cookie_duration") or "").strip() or None,
            "description": (it.get("description") or "").strip() or None,
            "tags_json": json.dumps(keywords, ensure_ascii=False),
            "directory_traffic": (it.get("traffic") or "").strip() or None,
            "directory_popularity": (it.get("popularity") or "").strip() or None,
            "directory_status": (it.get("status") or "").strip() or None,
            # Fill traffic_score từ directory traffic (Lovable hiển thị sẵn)
            # SimilarWeb scanner sẽ ghi đè sau khi tìm được trang chủ thực
            "traffic_score": _parse_lovable_traffic(it.get("traffic") or ""),
            "traffic_period_month": (
                datetime.utcnow().strftime("%Y-%m")
                if it.get("traffic")
                else None
            ),
            "raw_json": json.dumps(it, ensure_ascii=False),
            "source_url": self.page_url,
            "crawled_at": datetime.utcnow(),
        }
