"""Quét ngày tạo / hết hạn domain.

Chiến lược 2 lớp:
1) RDAP (https://rdap.org/domain/{d}) — nhanh, JSON, chuẩn hoá. Tốt cho gTLD
   (.com/.net/.org/.ai/.app…). Tự retry lỗi tạm (429/5xx/timeout).
2) WHOIS port-43 (fallback) — khi RDAP KHÔNG có record (nhiều ccTLD như .io/.co/.me
   không có RDAP qua rdap.org → 404). Tra whois.iana.org để tìm whois server của TLD
   (cache theo TLD) rồi hỏi trực tiếp, parse "Creation Date"/"Registry Expiry Date".

Trả status: ok | not_found | error (lỗi tạm, nên thử lại). Không cần thư viện ngoài.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse

import httpx
from dateutil import parser as _dateparser

logger = logging.getLogger("whois.scanner")

_RDAP_URL = "https://rdap.org/domain/{domain}"
_TIMEOUT = 20.0
_MAX_ATTEMPTS = 3
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
_HEADERS = {
    "Accept": "application/rdap+json",
    "User-Agent": "Mozilla/5.0 (AffiliateHub RDAP client)",
}

# WHOIS port-43
_WHOIS_TIMEOUT = 15.0
_WHOIS_SERVER_CACHE: dict[str, str | None] = {}
_WHOIS_CACHE_LOCK = asyncio.Lock()
_CREATED_KEYS = ("creation date", "created on", "created date", "registered on",
                 "registration date", "registration time", "domain registration date",
                 "created", "registered")
_EXPIRES_KEYS = ("registry expiry date", "registrar registration expiration date",
                 "expiry date", "expiration date", "expires on", "expire date",
                 "renewal date", "paid-till", "expires", "expiry")


def _extract_domain(url_or_domain: str) -> str | None:
    """Registrable domain từ URL/domain (bỏ scheme, path, www, port). 2 nhãn cuối."""
    if not url_or_domain:
        return None
    s = url_or_domain.strip()
    if "://" not in s:
        s = "http://" + s
    host = (urlparse(s).hostname or "").lower().removeprefix("www.")
    if not host:
        return None
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _parse_event(events, action: str) -> datetime | None:
    for e in events or []:
        if (e.get("eventAction") or "").lower() == action:
            raw = (e.get("eventDate") or "").replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(raw).replace(tzinfo=None)
            except Exception:
                return None
    return None


# ─── Lớp 1: RDAP ────────────────────────────────────────────────────────────────

async def _scan_rdap(domain: str) -> dict:
    base = {"domain": domain, "created": None, "expires": None, "found": False}
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            await asyncio.sleep(1.5 * attempt)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True, headers=_HEADERS) as c:
                r = await c.get(_RDAP_URL.format(domain=domain))
        except Exception as e:
            logger.debug("[whois] RDAP %s attempt %d lỗi mạng: %s", domain, attempt, e)
            continue
        if r.status_code == 200:
            try:
                events = r.json().get("events", [])
            except Exception:
                return {**base, "status": "not_found"}
            created = _parse_event(events, "registration")
            expires = _parse_event(events, "expiration")
            found = bool(created or expires)
            return {"domain": domain, "created": created, "expires": expires,
                    "found": found, "status": "ok" if found else "not_found"}
        if r.status_code == 404:
            return {**base, "status": "not_found"}   # TLD/domain không có RDAP record
        if r.status_code in _TRANSIENT_STATUS:
            ra = r.headers.get("retry-after")
            if ra and ra.isdigit():
                await asyncio.sleep(min(int(ra), 10))
            continue
        logger.debug("[whois] RDAP %s HTTP %s", domain, r.status_code)
    return {**base, "status": "error"}


# ─── Lớp 2: WHOIS port-43 (fallback) ─────────────────────────────────────────────

async def _whois_query(server: str, query: str) -> str:
    reader, writer = await asyncio.open_connection(server, 43)
    try:
        writer.write((query + "\r\n").encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.read(-1), timeout=_WHOIS_TIMEOUT)
        return data.decode(errors="ignore")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _whois_server_for(tld: str) -> str | None:
    """whois server của TLD (tra whois.iana.org, cache theo TLD)."""
    async with _WHOIS_CACHE_LOCK:
        if tld in _WHOIS_SERVER_CACHE:
            return _WHOIS_SERVER_CACHE[tld]
    server = None
    try:
        txt = await _whois_query("whois.iana.org", tld)
        for line in txt.splitlines():
            if line.lower().startswith("whois:"):
                server = line.split(":", 1)[1].strip()
                break
    except Exception as e:
        logger.debug("[whois] IANA lookup .%s lỗi: %s", tld, e)
    async with _WHOIS_CACHE_LOCK:
        _WHOIS_SERVER_CACHE[tld] = server
    return server


def _parse_date(val: str) -> datetime | None:
    val = (val or "").strip()
    if not val:
        return None
    for candidate in (val, val.split()[0] if val.split() else val):
        try:
            return _dateparser.parse(candidate, fuzzy=False).replace(tzinfo=None)
        except Exception:
            continue
    return None


def _extract_whois_date(text: str, keys: tuple[str, ...]) -> datetime | None:
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        kl = key.strip().lower()
        if val.strip() and any(k in kl for k in keys):
            dt = _parse_date(val)
            if dt:
                return dt
    return None


async def _scan_whois_port43(domain: str) -> tuple[datetime | None, datetime | None]:
    tld = domain.rsplit(".", 1)[-1]
    server = await _whois_server_for(tld)
    if not server:
        return None, None
    try:
        txt = await _whois_query(server, domain)
    except Exception as e:
        logger.debug("[whois] port43 %s @%s lỗi: %s", domain, server, e)
        return None, None
    return _extract_whois_date(txt, _CREATED_KEYS), _extract_whois_date(txt, _EXPIRES_KEYS)


# ─── Public ──────────────────────────────────────────────────────────────────────

async def scan_whois(url: str) -> dict:
    """Tra ngày tạo/hết hạn domain: RDAP trước, WHOIS port-43 nếu RDAP không có record.
    Trả {domain, created, expires, found, status} — status: ok | not_found | error."""
    domain = _extract_domain(url)
    if not domain:
        return {"domain": domain, "created": None, "expires": None, "found": False, "status": "not_found"}

    result = await _scan_rdap(domain)
    if result["status"] == "ok":
        return result

    # RDAP không có record (not_found) HOẶC lỗi tạm → thử WHOIS port-43.
    try:
        created, expires = await _scan_whois_port43(domain)
    except Exception as e:
        logger.debug("[whois] fallback %s lỗi: %s", domain, e)
        created = expires = None
    if created or expires:
        return {"domain": domain, "created": created, "expires": expires,
                "found": True, "status": "ok"}

    return result   # giữ nguyên not_found / error để re-scan sau
