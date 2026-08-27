from __future__ import annotations
from urllib.parse import urlparse
from bs4 import BeautifulSoup, Tag

PRIMARY_LABEL_HINTS = [
    "official website", "official site", "company website",
    "company url", "homepage", "visit website", "website:",
    "web site", "trang web", "trang chủ",
]


def _get_root_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc or ""
        return host.removeprefix("www.").lower()
    except Exception:
        return ""


def _is_valid_outbound(href: str, exclude_domains: set[str]) -> bool:
    if not href or not href.startswith("http"):
        return False
    domain = _get_root_domain(href)
    if not domain:
        return False
    for excl in exclude_domains:
        if domain == excl or domain.endswith("." + excl):
            return False
    return True


def find_labeled_outbound_link(html: str, exclude_domains: set[str]) -> dict | None:
    """
    Tìm outbound link có label ngữ nghĩa gần đó khớp PRIMARY_LABEL_HINTS.
    Chiến lược: tìm text node chứa hint → tìm <a href> gần nhất (sibling, parent, table row, dt/dd).
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return None

    hints_lower = [h.lower() for h in PRIMARY_LABEL_HINTS]

    def text_has_hint(text: str) -> bool:
        t = text.lower().strip()
        return any(h in t for h in hints_lower)

    # Tìm tất cả text nodes / elements chứa hint
    for element in soup.find_all(string=True):
        if not text_has_hint(element):
            continue
        parent = element.parent
        if not parent:
            continue

        # Tìm <a> trong cùng container (sibling or descendant of parent/grandparent)
        candidates: list[Tag] = []

        # 1. Sibling của parent
        for sib in (parent.next_siblings, parent.previous_siblings):
            for node in sib:
                if isinstance(node, Tag):
                    a = node.find("a", href=True) if node.name != "a" else node
                    if a and isinstance(a, Tag):
                        candidates.append(a)
                    break  # chỉ lấy 1 sibling gần nhất

        # 2. Parent's parent children (table row / dt-dd / div row)
        grandparent = parent.parent
        if grandparent:
            for a in grandparent.find_all("a", href=True, limit=5):
                if isinstance(a, Tag):
                    candidates.append(a)

        for a_tag in candidates:
            href = a_tag.get("href", "")
            if isinstance(href, list):
                href = href[0] if href else ""
            if _is_valid_outbound(href, exclude_domains):
                text = a_tag.get_text(strip=True) or href
                return {"url": href, "label": element.strip(), "text": text}

    return None


def find_any_outbound_link(html: str, exclude_domains: set[str]) -> dict | None:
    """Fallback: lấy outbound link đầu tiên hợp lệ, ưu tiên link gần H1."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return None

    h1 = soup.find("h1")
    near_h1_candidates: list[dict] = []
    all_candidates: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if isinstance(href, list):
            href = href[0] if href else ""
        if not _is_valid_outbound(href, exclude_domains):
            continue
        text = a.get_text(strip=True) or href
        entry = {"url": href, "label": "", "text": text, "node": a}

        if h1:
            # Kiểm tra link có nằm trước nội dung chính không (gần H1)
            try:
                h1_pos = str(soup).find(str(h1))
                link_pos = str(soup).find(str(a))
                if 0 <= link_pos - h1_pos < 2000:
                    near_h1_candidates.append(entry)
                    continue
            except Exception:
                pass
        all_candidates.append(entry)

    if near_h1_candidates:
        return near_h1_candidates[0]
    if all_candidates:
        return all_candidates[0]
    return None


def is_near_h1(node: Tag | None, html: str) -> bool:
    if node is None:
        return False
    try:
        soup = BeautifulSoup(html, "lxml")
        h1 = soup.find("h1")
        if not h1:
            return False
        h1_pos = str(soup).find(str(h1))
        node_pos = str(soup).find(str(node))
        return 0 <= node_pos - h1_pos < 2000
    except Exception:
        return False
