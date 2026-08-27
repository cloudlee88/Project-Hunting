"""Shared CloakBrowser pool for utility fetches (affiliate detection).

Broker homepages are often Cloudflare-fronted or block datacenter/headless
traffic. This keeps a small pool of CloakBrowsers — one per proxy IP — and
rotates requests across them (≈ rotating proxy), so no single IP gets
rate-limited. Falls back to a fast httpx path.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

# Browser pool is capped low for RAM (each CloakBrowser ~200-300MB). httpx still
# rotates across ALL configured proxies, so IP diversity for path-probes stays high.
MAX_POOL = 4

_pool: list[dict] = []          # [{"browser":.., "proxy":..}]
_proxy_urls: list[str] = []     # configured pool (empty = direct, no proxy)
_pool_built = False
_rr = 0                         # round-robin cursor
_lock = asyncio.Lock()

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_BLOCK_SIGNALS = (
    "just a moment", "cf-browser-verification", "checking your browser",
    "cf_chl_opt", "ray id", "enable javascript and cookies",
)
_BROWSER_ERROR_SIGNALS = (
    "the chromium authors", "checking the proxy and the firewall",
    "err_connection", "err_timed_out", "err_name_not_resolved",
    "err_address_unreachable", "dns_probe", "err_network", "togglehelpbox",
)


def _looks_blocked(html: str | None) -> bool:
    if not html or len(html) < 2000:
        return True
    head = html[:5000].lower()
    if any(s in head for s in _BROWSER_ERROR_SIGNALS):
        return True
    return sum(1 for s in _BLOCK_SIGNALS if s in head) >= 2


async def set_proxies(proxy_urls: list[str] | None) -> None:
    """Configure the rotating proxy pool. Rebuilds browsers if the set changed."""
    global _proxy_urls, _pool_built
    async with _lock:
        urls = [u for u in (proxy_urls or []) if u]
        if urls != _proxy_urls:
            await _close_all_locked()
            _proxy_urls = urls
            _pool_built = False


def _next_proxy() -> str | None:
    global _rr
    if not _proxy_urls:
        return None
    p = _proxy_urls[_rr % len(_proxy_urls)]
    _rr += 1
    return p


async def _ensure_pool_locked() -> None:
    global _pool_built
    if _pool_built:
        return
    _pool_built = True
    from app.services.browser.session import get_browser
    slots = _proxy_urls[:MAX_POOL] if _proxy_urls else [None]
    for proxy in slots:
        try:
            b = await get_browser(headless=True, proxy_url=proxy or None)
            if b is not None:
                _pool.append({"browser": b, "proxy": proxy})
        except Exception as e:
            log.warning("[affiliate] browser start failed (proxy=%s): %s", bool(proxy), e)
    log.info("[affiliate] browser pool ready: %d (proxies=%d)", len(_pool), len(_proxy_urls))


async def fetch_httpx(url: str, timeout: float = 12.0) -> str | None:
    proxy = _next_proxy()
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=_HEADERS, proxy=proxy or None,
        ) as c:
            r = await c.get(url)
            if r.status_code == 200:
                return r.text
    except Exception as e:
        log.debug("[affiliate] httpx %s: %s", url, e)
    return None


async def fetch_rendered(url: str, wait: float = 2.5, timeout: float = 25.0) -> str | None:
    """Fetch via a pooled CloakBrowser (rotating IP). None if blocked/error page."""
    global _rr
    async with _lock:
        await _ensure_pool_locked()
        if not _pool:
            return None
        entry = _pool[_rr % len(_pool)]
        _rr += 1
    browser = entry["browser"]
    page = None
    try:
        page = await browser.new_page()
        async def _go():
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
            except Exception:
                pass
            await asyncio.sleep(wait)
            return await page.content()
        html = await asyncio.wait_for(_go(), timeout=timeout + 5)
        return None if _looks_blocked(html) else html
    except Exception as e:
        log.debug("[affiliate] browser %s: %s", url, e)
        return None
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass


async def fetch_html(url: str) -> str | None:
    """httpx fast-path → rotating CloakBrowser fallback (one retry) when blocked."""
    html = await fetch_httpx(url)
    if html and not _looks_blocked(html):
        return html
    html = await fetch_rendered(url)
    if html:
        return html
    await asyncio.sleep(1.5)
    return await fetch_rendered(url, wait=3.5)  # retry rotates to the next IP


async def _close_all_locked() -> None:
    global _pool_built
    for entry in _pool:
        try:
            await entry["browser"].close()
        except Exception:
            pass
    _pool.clear()
    _pool_built = False


async def close_shared_browser() -> None:
    async with _lock:
        await _close_all_locked()
