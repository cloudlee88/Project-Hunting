import re
from urllib.parse import urlparse

STATIC_FILE_EXT = re.compile(
    r'\.(css|js|png|jpe?g|gif|svg|webp|ico|pdf|woff2?|ttf|eot|map|json)(\?|$)', re.I
)
UI_PATH_PATTERNS = re.compile(
    r'/(login|register|sign-?up|search|download|contact|cart|sitemap|rss|api|feed|tag|tags|category|categories|author|page|cdn-cgi)(/|\?|$)'
    r'|/(privacy|terms|cookie|disclaimer|about|faq|help)([\-_][-\w]*)?(/|\.|$)',
    re.I,
)


def _first_segment(url: str) -> str:
    try:
        segs = [s for s in urlparse(url).path.strip('/').split('/') if s]
        return segs[0].lower() if segs else ""
    except Exception:
        return ""


# Trang chi tiết (item/detail) thường nằm ở segment KHÁC trang listing:
# toolify liệt kê ở /category/… hoặc /new nhưng trang tool thật ở /tool/<slug>;
# tương tự /product, /review, /app… Đây CHÍNH là nội dung cần lấy nên không được
# coi là "nhảy sang section lạc đề" (xem luật section_anchor bên dưới).
_DETAIL_SEGMENTS = {
    "tool", "tools", "product", "products", "item", "items",
    "review", "reviews", "app", "apps", "software", "service", "services",
    "company", "companies", "listing", "gpt", "gpts",
    "p",          # capterra: trang sản phẩm ở /p/<id>/<Name>/
    "posts", "post",  # producthunt & tương tự
}


def is_junk_link(href: str, source_url: str, section_anchor: str | None = None) -> bool:
    if not href or href.startswith('#') or href.startswith('javascript:') or href.startswith('mailto:') or href.startswith('tel:'):
        return True
    if STATIC_FILE_EXT.search(href):
        return True
    try:
        parsed = urlparse(href)
        if UI_PATH_PATTERNS.search(parsed.path):
            return True
    except Exception:
        return True
    if href.rstrip('/') == source_url.rstrip('/'):
        return True
    # Stay on-topic: if the source lives under a section (e.g. /forex-reviews),
    # skip internal links that jump to a DIFFERENT top section (/tools, /community,
    # /book, /article…). Avoids wasting requests — and rate-limit budget — on pages
    # that never contain the projects we're after.
    if section_anchor:
        anchor_seg = _first_segment(section_anchor)
        href_seg = _first_segment(href)
        if (anchor_seg and href_seg and href_seg != anchor_seg
                and href_seg not in _DETAIL_SEGMENTS):
            return True
    return False
