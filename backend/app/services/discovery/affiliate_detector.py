from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.discovery import DiscoveryCandidate
from app.models.affiliate_program import AffiliateProgram
from app.services.discovery import browser_fetch

log = logging.getLogger(__name__)

# Link-text signals for an affiliate / partner program (checked in link text).
AFFILIATE_TEXT_KEYWORDS = [
    "affiliate program", "affiliate programme", "affiliates", "affiliate",
    "partner program", "partnership", "partners", "partner",
    "referral program", "refer a friend", "referral",
    "introducing broker", "ib program", "ib partner",
    "become a partner", "become an affiliate", "join our affiliate",
    "ambassador", "associates program", "earn commission",
    # Vietnamese
    "đối tác", "tiếp thị liên kết", "cộng tác viên", "chương trình liên kết",
]

# URL-path signals (checked in href). `affil[a-z-]*` also catches common
# misspellings some brokers actually use (e.g. coinexx.com/affilate).
AFFILIATE_HREF_RE = re.compile(
    r"/(affil[a-z]*(-?program(me)?)?|"          # affiliate, affilate, affiliate-program…
    r"partner[a-z]*(-?program)?|partnership[a-z]*|"
    r"referral[a-z]*|refer-a-friend|"
    r"ambassador[a-z]*|associates|"
    r"ib|introducing-brokers?)"
    r"([/\-_?#.]|$)",
    re.I,
)

# Path guesses to probe directly if no link is found on the homepage.
# Includes the common 'affilate' misspelling.
AFFILIATE_PATH_GUESSES = [
    "/affiliate", "/affilate", "/affiliates", "/affiliate-program", "/affiliates-program",
    "/partners", "/partner-program", "/partner", "/partnership", "/partnerships",
    "/referral", "/referral-program", "/ib", "/introducing-broker",
    "/become-an-affiliate", "/affiliate-programme", "/ib-program",
]


def _name_from_domain(domain: str) -> str:
    """Tên dự án = domain đã bỏ đuôi TLD (bounce.com → bounce, datafa.st → datafa).
    Quy tắc đặt tên dùng chung cho MỌI nguồn discovery."""
    d = (domain or "").strip().lower()
    if d.startswith("www."):
        d = d[4:]
    name = re.sub(r"\.[a-z]{2,}$", "", d).strip()
    return name or d


def _score_link(text: str, href: str, in_footer: bool) -> int:
    """Higher = more likely a real affiliate-program link."""
    t = (text or "").lower().strip()
    h = (href or "").lower()
    score = 0
    if AFFILIATE_HREF_RE.search(urlparse(h).path or h):
        score += 5                       # path like /affiliate is the strongest signal
    for kw in AFFILIATE_TEXT_KEYWORDS:
        if kw in t:
            score += 4 if len(kw) > 8 else 3   # phrase > single word
            break
    if in_footer:
        score += 2                       # affiliate links usually live in the footer
    # Penalise obvious false positives (business partners / partner logos pages)
    if "our partners" in t or "trusted partners" in t or "technology partner" in t:
        score -= 3
    return score


def _find_affiliate_link(html: str, base_url: str) -> str | None:
    """Scan every link for affiliate/partner signals; prefer footer + path match."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return None

    # Mark links inside <footer> (or elements whose class/id mentions footer).
    footer_links = set()
    for footer in soup.find_all(["footer"]) + soup.select('[class*="footer"], [id*="footer"]'):
        for a in footer.find_all("a", href=True):
            footer_links.add(id(a))

    best: tuple[int, str] | None = None
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if isinstance(href, list):
            href = href[0] if href else ""
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = a.get_text(" ", strip=True)
        score = _score_link(text, href, id(a) in footer_links)
        if score >= 4:
            abs_url = urljoin(base_url, href)
            if best is None or score > best[0]:
                best = (score, abs_url)
    return best[1] if best else None


def _clean_name(candidate: DiscoveryCandidate) -> str:
    """Đặt tên dự án theo domain (bỏ đuôi TLD) — nhất quán cho MỌI nguồn discovery.
    KHÔNG dùng suggested_name (vd tên nhà quảng cáo Google Ads như 'THẠCH VŨ')."""
    return _name_from_domain(candidate.domain)


async def _promote_to_affiliate_programs(candidate: DiscoveryCandidate, session: AsyncSession) -> int | None:
    name = _clean_name(candidate)
    # Nguồn Google Ads → gắn nhãn 'google_ads' (phân biệt với discovery thường);
    # candidate được tạo với detection_method='google_ads' ở export-to-discovery.
    prog_source = "google_ads" if candidate.detection_method == "google_ads" else "discovery"
    external_id = f"{prog_source}_{candidate.domain}"

    # Inherit the category configured on the discovery source (if any).
    from app.models.discovery import DiscoverySource
    src = (await session.execute(
        select(DiscoverySource).where(DiscoverySource.id == candidate.source_id)
    )).scalar_one_or_none()
    # Per-row (import CSV có cột category/sub_category/field) ưu tiên; rỗng → dùng của nguồn.
    category = getattr(candidate, "category", None) or ((src.category or None) if src else None)       # nhóm rộng
    sub_category = getattr(candidate, "sub_category", None) or ((src.name or None) if src else None)   # ngách hẹp
    field = getattr(candidate, "field", None) or ((src.field or None) if src else None)               # lĩnh vực (CRM/HR…)

    # Google Ads: đồng bộ số ngày hiển thị + lần đầu/cuối quảng cáo từ candidate.
    ad_days = getattr(candidate, "ad_days_shown", None)
    ad_first = getattr(candidate, "ad_first_shown", None)
    ad_last = getattr(candidate, "ad_last_shown", None)

    now = datetime.utcnow()
    insert_values = dict(
        source=prog_source,
        external_id=external_id,
        name=name,
        category=category,
        sub_category=sub_category,
        field=field,
        ad_days_shown=ad_days,
        ad_first_shown=ad_first,
        ad_last_shown=ad_last,
        url=candidate.homepage_url or candidate.raw_url,
        signup_url=candidate.affiliate_url,
    )
    update_set = {"signup_url": candidate.affiliate_url, "name": name,
                  "category": category, "sub_category": sub_category, "updated_at": now}
    # Chỉ ghi đè ad-info khi candidate có (tránh xoá dữ liệu cũ lúc re-promote).
    if ad_days is not None or ad_first is not None or ad_last is not None:
        update_set["ad_days_shown"] = ad_days
        update_set["ad_first_shown"] = ad_first
        update_set["ad_last_shown"] = ad_last
    # Carry traffic across if the candidate was scanned before promotion — but only
    # when present, so re-promoting doesn't wipe an existing program's traffic.
    if candidate.traffic_monthly:
        tv = float(candidate.traffic_monthly)
        insert_values["traffic_score"] = tv
        update_set["traffic_score"] = tv
        update_set["traffic_scanned_at"] = now

    stmt = sqlite_insert(AffiliateProgram).values(**insert_values).on_conflict_do_update(
        index_elements=["source", "external_id"],
        set_=update_set,
    )
    await session.execute(stmt)
    await session.commit()

    prog = (await session.execute(
        select(AffiliateProgram).where(
            AffiliateProgram.source == prog_source,
            AffiliateProgram.external_id == external_id,
        )
    )).scalar_one_or_none()
    return prog.id if prog else None


# Cụm từ nhận diện trang lỗi/soft-404 (SPA trả HTTP 200 nhưng nội dung là trang 404).
_DEAD_LINK_PHRASES = (
    "page not found", "doesn't exist", "does not exist", "page you're looking for",
    "page you are looking for", "404 error", "error 404", "oops! the page", "404!",
)


async def check_affiliate_link_status(url: str) -> str | None:
    """Đánh giá link affiliate: render trang (CloakBrowser) rồi tìm dấu hiệu 404.
    Trả:
      'ok'      = trang sống
      'dead'    = 404 / không tồn tại
      'unknown' = ĐÃ thử nhưng không kết luận được (render lỗi / site chặn / timeout)
                  — phân biệt với None (= chưa kiểm tra bao giờ).
      None      = không có link để đánh giá.

    Chỉ xét NỘI DUNG HIỂN THỊ (bỏ <script>/<style> trước): framework như Next.js
    nhúng sẵn text của component 404 trong payload JSON của <script>, nên nếu quét
    cả HTML thô sẽ báo nhầm trang sống (vd genlook.app/affiliates) thành 404."""
    if not url:
        return None
    html = await browser_fetch.fetch_html(url)
    if not html:
        return "unknown"   # render thất bại / bị chặn → chưa kết luận (thử lại với proxy)
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "template"]):
            tag.decompose()
        visible = soup.get_text(" ", strip=True).lower()
    except Exception:
        return "unknown"   # render được nhưng parse lỗi → chưa kết luận
    # Render dở/bị chặn (rất ít text hiển thị) → chưa kết luận, KHÔNG gộp với "chưa quét".
    if len(visible) < 200:
        return "unknown"
    return "dead" if any(p in visible for p in _DEAD_LINK_PHRASES) else "ok"


async def detect_affiliate(candidate_id: int, session: AsyncSession) -> None:
    c = (await session.execute(
        select(DiscoveryCandidate).where(DiscoveryCandidate.id == candidate_id)
    )).scalar_one_or_none()
    if not c:
        return

    base_url = c.homepage_url or c.raw_url
    found_link: str | None = None
    method = ""

    # 1. Fetch homepage (CloakBrowser-backed → bypasses Cloudflare) and scan
    #    every link for affiliate/partner signals, preferring the footer.
    html = await browser_fetch.fetch_html(base_url)
    if html:
        found_link = _find_affiliate_link(html, base_url)
        if found_link:
            method = "html_link"

    # 2. Fallback: probe common affiliate paths directly (via browser fetch —
    #    a page that renders is a real affiliate page).
    if not found_link:
        for path in AFFILIATE_PATH_GUESSES:
            test_url = urljoin(base_url, path)
            page_html = await browser_fetch.fetch_httpx(test_url) or ""
            if page_html and "<title" in page_html.lower() and "404" not in page_html[:600].lower():
                # confirm the page actually looks affiliate-related
                low = page_html[:8000].lower()
                if any(k in low for k in ("affiliate", "partner", "referral", "commission")):
                    found_link = test_url
                    method = "path_guess"
                    break

    now = datetime.utcnow()
    if found_link:
        c.affiliate_url = found_link
        c.affiliate_detection_method = method
        c.affiliate_url_status = await check_affiliate_link_status(found_link)
        c.status = "affiliate_found"
        c.updated_at = now
        await session.commit()

        prog_id = await _promote_to_affiliate_programs(c, session)
        if prog_id:
            c.status = "promoted"
            c.promoted_program_id = prog_id
            c.updated_at = datetime.utcnow()
            await session.commit()
        log.info("[discovery] affiliate found for %s → %s (%s)", c.domain, found_link, method)
    else:
        c.status = "no_affiliate_found"
        c.updated_at = now
        await session.commit()
        log.info("[discovery] no affiliate for %s", c.domain)


def _bare_host(h: str) -> str:
    h = (h or "").lower()
    return h[4:] if h.startswith("www.") else h


def _pick_affiliate_from_search(domain: str, links: list) -> str | None:
    """Từ kết quả Google, chọn link affiliate tốt nhất NẰM TRÊN chính domain ứng
    viên (loại reddit/review/directory). Ưu tiên path khớp mẫu affiliate."""
    d = _bare_host(domain)
    if not d:
        return None
    on_domain = [l for l in links
                 if (lambda h: h == d or h.endswith("." + d))(_bare_host(urlparse(l).netloc))]
    if not on_domain:
        return None
    # 1) path khớp regex affiliate (vd /help-center/.../affiliate-program/)
    for l in on_domain:
        if AFFILIATE_HREF_RE.search(urlparse(l).path or ""):
            return l
    # 2) URL có chứa từ khoá affiliate/partner/referral
    for l in on_domain:
        low = l.lower()
        if any(k in low for k in ("affiliate", "partner", "referral", "ambassador", "/ib")):
            return l
    return None


async def detect_affiliate_via_search(
    candidate_id: int, session: AsyncSession, page=None, proxy_url: str = "",
) -> bool:
    """Fallback: dò trang affiliate qua Google search (CloakBrowser — miễn phí như
    'Tìm trang chủ'). Dùng khi site chặn fetch (vd Cloudflare) hoặc trang affiliate
    nằm ở path sâu/lạ mà quét trang chủ + đoán path không thấy.

    `page`: nếu chạy batch thì truyền page dùng chung (đã warmup) để tránh bị Google
    chặn và tiết kiệm. Trả True nếu tìm thấy + promote."""
    from app.services.crawlers import homepage_finder

    c = (await session.execute(
        select(DiscoveryCandidate).where(DiscoveryCandidate.id == candidate_id)
    )).scalar_one_or_none()
    if not c:
        return False

    query = f"{c.domain} affiliate program"
    links = await homepage_finder.search_google_links(query, page=page, proxy_url=proxy_url)
    found = _pick_affiliate_from_search(c.domain, links)

    now = datetime.utcnow()
    if not found:
        c.status = "no_affiliate_found"
        c.updated_at = now
        await session.commit()
        log.info("[discovery] no affiliate via search for %s (links=%d)", c.domain, len(links))
        return False

    c.affiliate_url = found
    c.affiliate_detection_method = "search_engine"
    c.affiliate_url_status = await check_affiliate_link_status(found)
    c.status = "affiliate_found"
    c.updated_at = now
    await session.commit()

    prog_id = await _promote_to_affiliate_programs(c, session)
    if prog_id:
        c.status = "promoted"
        c.promoted_program_id = prog_id
        c.updated_at = datetime.utcnow()
        await session.commit()
    log.info("[discovery] affiliate via search for %s → %s", c.domain, found)
    return True
