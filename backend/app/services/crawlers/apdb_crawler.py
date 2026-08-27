"""APDB crawler — HTML scraper cho Affiliate Program Database.

Trang dùng Cloudflare Bot Management → TLS fingerprinting chặn httpx kể cả khi có
cookie. Giải pháp: dùng CloakBrowser (Playwright stealth) cho toàn bộ navigation,
lấy HTML qua page.content() rồi parse BeautifulSoup.

Luồng: categories index (67 trang) → programs per category → dedup → detail parse.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.core.logger import get_logger
from .base import BaseCrawler

log = get_logger("crawler.apdb")

BASE_URL = "https://www.affiliateprogramdb.com"
NICHES_URL = f"{BASE_URL}/affiliate-marketing-niches/"
NAV_DELAY = 1.5   # giây giữa mỗi page.goto()
NAV_TIMEOUT = 25000  # ms timeout cho mỗi goto

# Category bị loại khỏi vai trò "primary" (meta/cross-cutting)
_EXCLUDED_PRIMARY: Set[str] = {
    "best", "global",
    "pay per sale (cps)", "pay per lead (cpl)", "pay per action (cpa)",
    "pay per click (cpc)", "pay per impression (cpm)",
    "digital product", "physical product", "digital service", "physical service",
    "one-time commission", "recurring commission", "lifetime commission",
    "highest paying", "multi-tier", "all-in-one",
    "us", "canadian", "uk", "european", "australia & new zealand",
}

APDB_MAX_CHOICES = [
    {"value": "50",  "label": "Thử nghiệm — 50 chương trình"},
    {"value": "200", "label": "Trung bình — 200 chương trình"},
    {"value": "all", "label": "Đầy đủ — Tất cả (~500+)"},
]


# ─── helpers ────────────────────────────────────────────────────────────────

def _coerce_max(value: Any) -> int:
    if value in (None, "", "all"):
        return 0
    try:
        return max(0, int(str(value).strip()))
    except ValueError:
        return 0


def _slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.split("/")[-1] or re.sub(r"[^a-z0-9]+", "-", url.lower())[:120]


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if url and not url.startswith("http"):
        url = "https://" + url
    return url


def _clean_category(name: str) -> str:
    """Bỏ suffix ' Affiliate Programs' / ' Programs' khỏi tên category."""
    name = re.sub(r"\s+affiliate\s+programs?$", "", name, flags=re.I).strip()
    name = re.sub(r"\s+programs?$", "", name, flags=re.I).strip()
    return name


def _pick_primary_category(categories: List[str]) -> Optional[str]:
    cleaned = [_clean_category(c) for c in categories]
    specific = [c for c in cleaned if c.lower() not in _EXCLUDED_PRIMARY]
    return specific[0] if specific else (cleaned[0] if cleaned else None)


def _page_count(soup: BeautifulSoup, pattern: str) -> int:
    """Extract N từ 'Page 1 of N' hay 'you are on page 1 of N'."""
    text = soup.get_text(" ")
    m = re.search(pattern, text, re.I)
    return int(m.group(1)) if m else 1


# ─── HTML parsing ────────────────────────────────────────────────────────────

def _find_url_from_text(page_text: str, *labels: str) -> Optional[str]:
    """Extract URL sau một label trong plain text của trang.

    APDB lưu URLs dạng text thuần (không phải anchor tag):
        Affiliate Program URL:\\n    https://www.eightcap.partners/
    """
    for label in labels:
        pattern = re.escape(label) + r"[:\s]+(https?://[^\s<>\"']+)"
        m = re.search(pattern, page_text, re.I)
        if m:
            url = m.group(1).rstrip(".,;)/")
            if url and "affiliateprogramdb" not in url:
                return url
    return None


def _find_text_after_label(soup: BeautifulSoup, *labels: str) -> Optional[str]:
    """Tìm text content sau một label trong trang."""
    for label in labels:
        for dt in soup.find_all("dt"):
            if label.lower() in dt.get_text().lower():
                dd = dt.find_next_sibling("dd")
                if dd:
                    txt = dd.get_text(" ", strip=True)
                    if txt:
                        return txt
        for tag in soup.find_all(["h2", "h3", "h4", "strong", "b"]):
            if label.lower() in tag.get_text().lower():
                nxt = tag.find_next_sibling()
                if nxt:
                    txt = nxt.get_text(" ", strip=True)
                    if txt and len(txt) > 5:
                        return txt
                parent = tag.parent
                if parent:
                    full = parent.get_text(" ", strip=True)
                    lbl = tag.get_text(strip=True)
                    after = full[full.lower().find(lbl.lower()) + len(lbl):].strip().lstrip(":").strip()
                    if len(after) > 10:
                        return after
    return None


def _find_links_in_section(soup: BeautifulSoup, *labels: str) -> List[str]:
    """Lấy danh sách link text trong section sau label."""
    for label in labels:
        label_elem = None
        for tag in soup.find_all(["dt", "h3", "h4", "strong", "b", "span", "p"]):
            if label.lower() in tag.get_text().lower():
                label_elem = tag
                break
        if not label_elem:
            continue
        nxt = label_elem.find_next_sibling()
        if nxt:
            links = [a.get_text(strip=True) for a in nxt.find_all("a") if a.get_text(strip=True)]
            if links:
                return [_clean_category(l) for l in links]
        parent = label_elem.parent
        if parent:
            links = [a.get_text(strip=True) for a in parent.find_all("a") if len(a.get_text(strip=True)) > 1]
            if links:
                return [_clean_category(l) for l in links]
    return []


def _parse_category_index_page(html: str) -> List[Dict]:
    """Parse trang index categories → list (name, url, count).

    Category URL pattern: /{slug}-affiliate-programs/ hoặc /{slug}-programs/
    Loại bỏ nav links như /payment-partner/, /newsletter/, /our-program/ etc.
    """
    soup = BeautifulSoup(html, "html.parser")
    categories = []
    seen: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = href if href.startswith("http") else urljoin(BASE_URL, href)
        path = urlparse(full).path
        if (
            "affiliateprogramdb.com" in full
            and path.count("/") == 2
            and not path.startswith("/brands/")
            and path.endswith("-programs/")   # pattern thực tế: /forex-affiliate-programs/
            and full not in seen
        ):
            seen.add(full)
            name = a.get_text(strip=True)
            parent = a.parent
            count_text = parent.get_text(" ") if parent else ""
            m = re.search(r"(\d+)\s*compan", count_text, re.I)
            count = int(m.group(1)) if m else 0
            if name:
                categories.append({"name": name, "url": full, "count": count})
    return categories


def _parse_program_list_page(html: str, base_url: str) -> List[str]:
    """Parse trang danh sách program trong category → list detail_url."""
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    seen: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = href if href.startswith("http") else urljoin(base_url, href)
        path = urlparse(full).path
        if path.startswith("/brands/") and path.count("/") == 3 and full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


def _parse_detail_page(html: str, detail_url: str) -> Optional[Dict]:
    """Parse trang chi tiết program."""
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else ""
    if not name:
        return None

    # URLs trên APDB là plain text, không phải anchor tag → dùng regex trên full text
    page_text = soup.get_text(" ")
    affiliate_url = _find_url_from_text(page_text, "Affiliate Program URL", "Affiliate URL")
    homepage_url = _find_url_from_text(page_text, "Company URL", "Homepage URL", "Company Website")

    # Nếu plain text không lấy được, fallback về anchor tag trên page
    if not homepage_url:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if (
                href.startswith("http")
                and "affiliateprogramdb" not in href
                and a.get_text(strip=True).startswith("http")  # link text = URL itself
            ):
                homepage_url = href
                break

    company_desc = _find_text_after_label(soup, "About The Company", "About the Company")
    commission_text = _find_text_after_label(soup, "About The Affiliate Program", "About The Program", "About the Program")
    categories = _find_links_in_section(soup, "Related Niches", "Niches", "Categories")
    program_types = _find_links_in_section(soup, "Program Type", "Program Types")
    geo_tags = _find_links_in_section(soup, "Company Type", "Geo", "Region")

    desc_parts = []
    if company_desc:
        desc_parts.append(company_desc)
    if commission_text:
        desc_parts.append(commission_text)

    tags = list({t for t in (geo_tags + program_types) if t})

    return {
        "source": "apdb",
        "external_id": _slug_from_url(detail_url),
        "name": name,
        "url": homepage_url,
        "signup_url": affiliate_url or "",
        "category": _pick_primary_category(categories),
        "description": "\n\n".join(desc_parts) if desc_parts else None,
        "tags_json": json.dumps(tags, ensure_ascii=False) if tags else None,
        "source_url": detail_url,
        "directory_status": "active",
        "crawled_at": datetime.utcnow(),
    }


# ─── browser navigation helper ───────────────────────────────────────────────

async def _nav(page: Any, url: str) -> Optional[str]:
    """Điều hướng tới URL, trả HTML string hoặc None nếu fail.

    networkidle timeout thường xảy ra do analytics/tracking requests chưa kết thúc —
    bắt exception, đợi thêm 4s cho CF challenge tự resolve, rồi lấy content.
    Retry 1 lần nếu lần đầu vẫn còn challenge page.
    """
    await asyncio.sleep(NAV_DELAY)
    for attempt in range(2):
        try:
            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
            except Exception:
                # Timeout thường do analytics — đợi CF JS resolve xong
                await asyncio.sleep(4)

            html = await page.content()
            if html and "just a moment" not in html.lower()[:2000]:
                return html

            # Vẫn còn challenge page
            if attempt == 0:
                log.info("APDB: CF challenge tại %s, đợi thêm 10s (attempt %d)...", url, attempt + 1)
                await asyncio.sleep(10)
            else:
                log.warning("APDB: CF challenge không giải được tại %s — bỏ qua", url)
                return None
        except Exception as e:
            log.warning("APDB nav fail [%d] %s: %s", attempt + 1, url, e)
            if attempt == 0:
                await asyncio.sleep(5)
            else:
                return None
    return None


# ─── crawler class ────────────────────────────────────────────────────────────

class APDBCrawler(BaseCrawler):
    source = "apdb"
    source_url = BASE_URL

    def __init__(self, max_programs: Any = "all", **_: object) -> None:
        self.max_programs = _coerce_max(max_programs)

    async def crawl(self) -> List[Dict]:
        log.info("APDB crawl start — max_programs=%s", self.max_programs or "ALL")

        from app.services.browser.session import get_browser
        browser = await get_browser(headless=True)
        if browser is None:
            raise RuntimeError("CloakBrowser chưa khả dụng — không thể crawl APDB (Cloudflare protection)")

        try:
            page = await browser.new_page()
            rows = await self._do_crawl(page)
        finally:
            try:
                await browser.close()
            except Exception:
                pass

        log.info("APDB crawl xong: %d programs", len(rows))
        return rows

    async def _do_crawl(self, page: Any) -> List[Dict]:
        # Bước 1: lấy tất cả category URLs
        category_urls = await self._get_all_categories(page)
        log.info("APDB: tìm được %d categories", len(category_urls))

        if not category_urls:
            log.error("APDB: không có categories — CloakBrowser có thể chưa bypass được CF")
            return []

        # Bước 2: lấy detail_url từ từng category, dedup trong bộ nhớ
        detail_map: Dict[str, List[str]] = {}  # detail_url → [category_names]
        for cat_url, cat_name in category_urls:
            new_urls = await self._get_category_programs(page, cat_url)
            for url in new_urls:
                if url not in detail_map:
                    detail_map[url] = []
                if cat_name not in detail_map[url]:
                    detail_map[url].append(cat_name)
            if self.max_programs and len(detail_map) >= self.max_programs:
                break

        unique_urls = list(detail_map.keys())
        if self.max_programs:
            unique_urls = unique_urls[: self.max_programs]
        log.info("APDB: %d unique program URLs sau dedup", len(unique_urls))

        # Bước 3: crawl từng detail page
        rows: List[Dict] = []
        for idx, detail_url in enumerate(unique_urls):
            html = await _nav(page, detail_url)
            if not html:
                log.warning("APDB [%d/%d] skip: %s", idx + 1, len(unique_urls), detail_url)
                continue
            row = _parse_detail_page(html, detail_url)
            if row:
                if not row.get("category") and detail_map.get(detail_url):
                    row["category"] = _pick_primary_category(detail_map[detail_url])
                rows.append(row)
                log.info("APDB [%d/%d] OK: %s | cat=%s", idx + 1, len(unique_urls), row["name"][:40], row.get("category"))
            else:
                log.warning("APDB [%d/%d] parse fail: %s", idx + 1, len(unique_urls), detail_url)

        return rows

    async def _get_all_categories(self, page: Any) -> List[tuple]:
        """Trả list (category_url, category_name)."""
        results: List[tuple] = []
        seen_urls: Set[str] = set()

        html = await _nav(page, NICHES_URL)
        if not html:
            return results

        soup = BeautifulSoup(html, "html.parser")
        total_pages = _page_count(soup, r"page\s+1\s+of\s+(\d+)")
        log.info("APDB: %d trang category index", total_pages)

        for c in _parse_category_index_page(html):
            if c["url"] not in seen_urls:
                seen_urls.add(c["url"])
                results.append((c["url"], c["name"]))

        for page_num in range(2, total_pages + 1):
            html = await _nav(page, f"{NICHES_URL}{page_num}/")
            if not html:
                continue
            for c in _parse_category_index_page(html):
                if c["url"] not in seen_urls:
                    seen_urls.add(c["url"])
                    results.append((c["url"], c["name"]))

        return results

    async def _get_category_programs(self, page: Any, cat_url: str) -> List[str]:
        """Trả list detail_url trong 1 category (kể cả phân trang)."""
        detail_urls: List[str] = []
        seen: Set[str] = set()

        html = await _nav(page, cat_url)
        if not html:
            return detail_urls

        soup = BeautifulSoup(html, "html.parser")
        total_pages = _page_count(soup, r"page\s+1\s+of\s+(\d+)")

        for u in _parse_program_list_page(html, cat_url):
            if u not in seen:
                seen.add(u)
                detail_urls.append(u)

        for page_num in range(2, total_pages + 1):
            html = await _nav(page, f"{cat_url.rstrip('/')}/{page_num}/")
            if not html:
                continue
            for u in _parse_program_list_page(html, cat_url):
                if u not in seen:
                    seen.add(u)
                    detail_urls.append(u)

        return detail_urls
