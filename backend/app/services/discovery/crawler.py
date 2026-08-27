from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import re
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.models.discovery import DiscoveryCandidate, DiscoverySource
from .filter import is_junk_link
from .label_matcher import (
    find_any_outbound_link,
    find_labeled_outbound_link,
    is_near_h1,
)

log = logging.getLogger(__name__)

MAX_DEPTH = 2                # max depth of internal page recursion
MAX_INTERNAL_PER_PAGE = 40   # internal links to follow per page
MAX_TOTAL_PAGES = 6000       # safety cap — full paginated site (listing + all review pages)
# Trên trang phân trang: nếu link outbound trực tiếp đã phủ ≥ tỷ lệ này so với số
# link review/detail → coi như mỗi item đã có link domain thật, BỎ QUA trang chi tiết
# (toolify ~0.95). Dưới ngưỡng → đi vào trang chi tiết (forexpeacearmy ~0, aixploria ~0.26).
LISTING_DIRECT_COVER_RATIO = 0.7
# Một số site (vd toolify.ai /category/*, capterra) nhận diện trình duyệt HEADLESS và trả
# trang "rỗng lưới" — chỉ có khung/menu/facet, KHÔNG có lưới sản phẩm (vẫn qua Cloudflare nên
# không báo chặn). Nếu 1 trang LISTING render ra ít hơn ngưỡng này item (direct + review) →
# coi là bị chặn mềm → thử lại bằng trình duyệt HEADED (trình duyệt thật lấy được đủ dữ liệu).
# 15: bắt được render rỗng-lưới (thường <10) mà không kích hoạt nhầm với render đủ (>25 item).
LISTING_MIN_ITEMS = 15
FETCH_TIMEOUT = 20.0
BROWSER_WAIT_SEC = 2.5       # JS render wait per page
CONCURRENT_PAGES = 8         # concurrent httpx requests
BROWSER_POOL_SIZE = 2        # parallel stealth browsers (each serialized internally)
POLITE_DELAY_SEC = 1.5       # base pause before each browser fetch (avoid rate-limit)
POLITE_JITTER_SEC = 1.5      # random extra delay (0..this) — human-like, smooths bursts
BLOCK_BACKOFF_SEC = [8.0, 20.0, 45.0]  # retry waits when a block/rate-limit page is hit
MAX_CONSECUTIVE_BLOCKS = 8   # abort the crawl if this many fetches in a row are blocked
BLOCK_RATE_MIN_SAMPLE = 5    # min browser fetches before judging overall failure rate
BLOCK_RATE_THRESHOLD = 0.75  # abort if ≥75% of fetches fail (proxy/IP too rate-limited)
NAV_TIMEOUT_SEC = 30.0       # hard cap on a single navigation+render (dead proxy can hang forever)
MAX_PROXY_POOL = 10          # cap on concurrent browsers/IPs in the proxy pool (RAM)
PER_PROXY_MAX_BLOCKS = 4     # drop an IP from the pool after this many consecutive blocks on it

# Infinite-scroll listings (SPA như toolify.ai) render chỉ batch đầu trong SSR HTML
# rồi tải thêm khi cuộn → phải cuộn để lấy hết. Chỉ áp dụng cho trang LISTING
# (source page + pagination), không cho trang review/detail. No-op với trang tĩnh
# (dừng ngay khi DOM không lớn thêm).
SCROLL_MAX_ROUNDS = 80       # số bước cuộn tối đa trên 1 trang listing
SCROLL_WAIT_SEC = 2.2        # chờ lazy-load khi đã tới đáy trang
SCROLL_STEP_WAIT_SEC = 0.5   # chờ ngắn giữa các bước cuộn (chưa tới đáy)
SCROLL_SETTLE_ROUNDS = 3     # dừng sau N lần tới đáy liên tiếp mà DOM không lớn thêm
SCROLL_INITIAL_SETTLE_SEC = 2.0  # chờ hydration xong trước khi bắt đầu cuộn
SCROLL_TIMEOUT_SEC = 120.0   # ngân sách thời gian thêm cho pha cuộn
LOAD_MORE_MAX_CLICKS = 40    # số lần bấm nút "Show more"/"Load more" tối đa trên 1 trang listing
CF_SOLVE_TIMEOUT_SEC = 60.0  # ngân sách thêm khi giải tường Cloudflare (CapSolver + reload)

# Source ids the user asked to stop mid-crawl. The crawler polls this and winds
# down gracefully (saving whatever it already found).
_STOP_REQUESTED: set[int] = set()


def request_stop(source_id: int) -> None:
    _STOP_REQUESTED.add(source_id)


def is_stop_requested(source_id: int) -> bool:
    return source_id in _STOP_REQUESTED


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # KHÔNG tự khai Accept-Encoding: httpx chỉ khai đúng codec nó giải được. Khai
    # 'br' bằng tay mà thiếu package brotli → r.text là bytes nén → 0 thẻ <a> →
    # trang nào cũng bị coi là JS-render và bị đẩy sang browser vô ích.
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}


# ─── Block / bot-wall / rate-limit detection ─────────────────────────────────

# Site-level rate-limit / block pages (not Cloudflare JS challenges). These are
# served when an IP makes too many requests too fast. The body text is the tell;
# the page itself can be large (obfuscated payload) so size alone won't catch it.
_BLOCK_PHRASES = [
    "not available. please come back later",
    "please come back later",
    "too many requests",
    "rate limit",
    "access denied",
    "you have been blocked",
    "temporarily unavailable",
]
# Cloudflare JS interstitial markers
_CF_PHRASES = [
    "just a moment", "cf-browser-verification", "cf_chl_opt",
    "checking your browser", "enable javascript and cookies",
    "ray id", "cdn-cgi/challenge-platform",
]
# Chromium's OWN error page — served when the navigation fails at the network
# level (connection refused / timed out / DNS), which is how a hard IP-level
# rate-limit shows up. It's a large page (inline CSS) so size won't catch it.
_BROWSER_ERROR_PHRASES = [
    "the chromium authors",
    "checking the proxy and the firewall",
    "err_connection", "err_timed_out", "err_connection_refused",
    "err_connection_closed", "err_name_not_resolved", "err_address_unreachable",
    "err_too_many", "err_network", "dns_probe", "togglehelpbox",
]


def _is_browser_error_page(html: str) -> bool:
    """True if `html` is the browser's own network-error interstitial."""
    lower = html[:6000].lower()
    return any(p in lower for p in _BROWSER_ERROR_PHRASES)


def _is_cf_blocked(html: str) -> bool:
    """True if the page is a bot-wall, CF challenge, site rate-limit, or the
    browser's own network-error page (hard block at the connection level)."""
    if len(html) < 4000:
        return True
    if _is_browser_error_page(html):
        return True
    lower = html.lower()
    head = lower[:8000]
    # Rate-limit / block page: 1 clear phrase là đủ, NHƯNG chỉ soi phần đầu trang.
    # Trang block/rate-limit thật rất nhỏ (đã bắt ở len<4000) và để thông báo ở
    # đầu; nếu soi toàn bộ body thì 1 trang listing lớn có tool tên/mô tả chứa
    # "rate limit"… sẽ bị nhầm là bị chặn (vd toolify.ai/new 637 tool).
    if any(p in head for p in _BLOCK_PHRASES):
        return True
    # CF challenge: needs 2 corroborating markers to avoid false positives.
    return sum(1 for s in _CF_PHRASES if s in head[:5000]) >= 2


_MIN_HTTPX_LINKS = 3          # dưới ngưỡng này coi như trang JS-render → cần browser
_ANCHOR_RE = re.compile(r'<a\s[^>]*href', re.I)


def _has_enough_links(html: str) -> bool:
    """A JS-rendered SPA shell returns HTTP 200 over httpx but has ~no <a> links
    (nội dung do JS render). Treat such pages as 'needs browser' so we render them
    instead of extracting 0 links and finding 0 candidates."""
    return len(_ANCHOR_RE.findall(html or "")) >= _MIN_HTTPX_LINKS


# ─── Proxy support ────────────────────────────────────────────────────────────

def _parse_proxy(proxy_url: str | None) -> dict | None:
    """Parse 'scheme://[user:pass@]host:port' → dict, or None if empty/invalid."""
    if not proxy_url or not proxy_url.strip():
        return None
    m = re.match(r'(?P<scheme>https?|socks5h?)://(?:(?P<user>[^:@/]+):(?P<pwd>[^@/]*)@)?(?P<host>[^:/@]+):(?P<port>\d+)',
                 proxy_url.strip(), re.I)
    if not m:
        log.warning("[discovery] proxy URL không hợp lệ, bỏ qua: %s", proxy_url)
        return None
    return {
        "scheme": m.group("scheme").lower(),
        "host": m.group("host"),
        "port": m.group("port"),
        "username": m.group("user") or "",
        "password": m.group("pwd") or "",
    }


async def check_proxy_alive(proxy_url: str, timeout: float = 12.0) -> tuple[bool, str]:
    """Quick health check: route a request through the proxy. Returns (ok, info)."""
    if not proxy_url:
        return True, "no proxy"
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout, follow_redirects=True) as client:
            r = await client.get("https://api.ipify.org?format=json")
            if r.status_code == 200:
                ip = r.json().get("ip", "?")
                return True, f"OK (exit IP {ip})"
            return False, f"HTTP {r.status_code}"
    except httpx.ProxyError as e:
        return False, f"proxy error: {e}"
    except httpx.ConnectTimeout:
        return False, "kết nối proxy timeout"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _make_proxy_auth_extension(scheme: str, host: str, port: str, user: str, pwd: str) -> str:
    """Create an unpacked Chrome extension that points the browser at the proxy
    AND auto-answers its Basic-auth challenge. Returns the extension dir path.

    This is the reliable way to use an authenticated proxy with Chromium —
    far more robust than CDP Fetch interception (which hangs on many proxies)."""
    import tempfile, json as _json
    ext_dir = tempfile.mkdtemp(prefix="disc_proxy_")
    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Discovery Proxy Auth",
        "permissions": [
            "proxy", "tabs", "unlimitedStorage", "storage",
            "<all_urls>", "webRequest", "webRequestBlocking",
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "22.0.0",
    }
    bg = """
var config = {
  mode: "fixed_servers",
  rules: { singleProxy: { scheme: "%s", host: "%s", port: parseInt("%s") }, bypassList: ["localhost"] }
};
chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
chrome.webRequest.onAuthRequired.addListener(
  function(details) { return { authCredentials: { username: "%s", password: "%s" } }; },
  { urls: ["<all_urls>"] },
  ["blocking"]
);
""" % ("https" if scheme.startswith("https") else "http", host, port, user, pwd)
    with open(f"{ext_dir}/manifest.json", "w") as f:
        _json.dump(manifest, f)
    with open(f"{ext_dir}/background.js", "w") as f:
        f.write(bg)
    return ext_dir


# ─── Crawl session — carries shared browser + state ───────────────────────────

@dataclass
class _PoolEntry:
    browser: Any
    proxy_url: str | None
    label: str
    blocks: int = 0          # consecutive blocks on THIS IP
    dead: bool = False       # dropped from rotation (IP too rate-limited / failed)


@dataclass
class _CrawlCtx:
    source_id: int
    source_root_url: str
    blacklist: set[str]
    user_id: int | None = None      # chủ sở hữu nguồn → dedupe + gán candidate theo user
    # Pool of proxy URLs (one CloakBrowser/IP). Empty list = crawl without proxy.
    proxy_urls: list[str] = field(default_factory=list)
    proxy_labels: list[str] = field(default_factory=list)
    max_listing_pages: int | None = None  # cap on paginated listing pages (None = all)
    max_total_pages: int = MAX_TOTAL_PAGES  # hard cap on total fetches this crawl
    incremental: bool = False           # only fetch pages until one is all-known, then stop
    visited: set[str] = field(default_factory=set)
    pages_fetched: int = 0
    use_browser: bool = False
    consecutive_blocks: int = 0     # reset on any successful fetch (global)
    browser_attempts: int = 0       # total browser fetches attempted
    browser_failures: int = 0       # browser fetches that failed after all retries
    aborted: bool = False           # set when rate-limiting is hopeless
    proxy_dead_reason: str | None = None   # lý do khi TẤT CẢ proxy chết ở health-check (vd "402 Payment Required")
    cf_seen: bool = False           # đã gặp tường Cloudflare ở ít nhất 1 trang
    solver_used: bool = False       # đã gọi CapSolver để giải tường CF
    solver_ok: bool = False         # CapSolver giải xong và trang qua được
    # cf_clearance đã giải được, khoá theo (root domain + proxy) vì cookie gắn theo IP.
    # browser.new_page() tạo context MỚI nên cookie KHÔNG tự chia sẻ giữa các trang →
    # phải tự tiêm lại, nếu không mỗi trang lại phải gọi CapSolver (tốn credit).
    cf_cookies: dict[str, list[dict]] = field(default_factory=dict)
    # Khoá mà CapSolver đã thử và KHÔNG giải được — đừng gọi lại (mỗi lượt tốn credit).
    cf_solve_failed: set[str] = field(default_factory=set)
    # CloakBrowser pool — each entry is one browser bound to one proxy IP.
    # Requests round-robin across entries via the _free queue, so load is spread
    # over all IPs (≈ DIY rotating proxy). Dead IPs are dropped automatically.
    _pool: list = field(default_factory=list, repr=False)        # list[_PoolEntry]
    _free: Any = field(default=None, repr=False)                 # asyncio.Queue of live indices
    _pool_init: bool = False
    _init_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Trình duyệt HEADED dùng lại cho fallback khi site trả trang rỗng lưới với headless
    # (xem LISTING_MIN_ITEMS). Tạo lười (chỉ khi cần), đóng ở stop(). Nếu không tạo được
    # (môi trường không màn hình) → _headed_disabled để thôi thử lại vô ích.
    _headed_browser: Any = field(default=None, repr=False)
    _headed_disabled: bool = False
    _headed_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _http_sem: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(CONCURRENT_PAGES)
    )

    @property
    def source_domain(self) -> str:
        return _get_root_domain(self.source_root_url)

    @property
    def should_stop(self) -> bool:
        return self.source_id in _STOP_REQUESTED

    @property
    def at_page_limit(self) -> bool:
        return self.aborted or self.should_stop or self.pages_fetched >= self.max_total_pages

    @property
    def live_browsers(self) -> int:
        return sum(1 for e in self._pool if not e.dead)

    async def _ensure_pool(self) -> bool:
        """Lazy-init the browser pool (init-locked). Returns True if ≥1 browser up."""
        async with self._init_lock:
            if self._pool_init:
                return self.live_browsers > 0
            self._pool_init = True
            await self._build_pool()
            return self.live_browsers > 0

    async def _build_pool(self) -> None:
        from app.services.browser.session import get_browser

        # Decide the (proxy, label) slots. No proxy → BROWSER_POOL_SIZE plain browsers.
        if self.proxy_urls:
            # Health-check all proxies in parallel; keep only the live ones.
            checks = await asyncio.gather(
                *[check_proxy_alive(u) for u in self.proxy_urls], return_exceptions=True
            )
            slots: list[tuple[str | None, str]] = []
            dead_reasons: list[str] = []
            for i, u in enumerate(self.proxy_urls):
                ok = isinstance(checks[i], tuple) and checks[i][0]
                label = self.proxy_labels[i] if i < len(self.proxy_labels) else f"proxy{i+1}"
                if ok:
                    slots.append((u, label))
                else:
                    info = str(checks[i][1] if isinstance(checks[i], tuple) else checks[i])
                    dead_reasons.append(info)
                    log.warning("[discovery] proxy chết, bỏ qua: %s (%s)", label, info)
            slots = slots[:MAX_PROXY_POOL]
            if not slots:
                # Ghi lại lý do phổ biến nhất để báo cho user (vd 402 Payment Required = hết hạn mức proxy).
                if dead_reasons:
                    from collections import Counter
                    self.proxy_dead_reason = Counter(dead_reasons).most_common(1)[0][0][:120]
                log.error("[discovery] không có proxy nào sống trong pool (%s)", self.proxy_dead_reason or "?")
                return
        else:
            slots = [(None, "direct")] * BROWSER_POOL_SIZE

        self._free = asyncio.Queue()
        for proxy_url, label in slots:
            try:
                browser = await get_browser(headless=True, proxy_url=proxy_url or None)
                if browser is None:
                    continue
                idx = len(self._pool)
                self._pool.append(_PoolEntry(browser=browser, proxy_url=proxy_url, label=label))
                self._free.put_nowait(idx)
            except Exception as e:
                log.warning("[discovery] khởi tạo browser '%s' lỗi: %s", label, e)
        log.info("[discovery] pool sẵn sàng: %d browser (proxy pool=%d)",
                 self.live_browsers, len(self.proxy_urls))

    async def fetch(self, url: str, scroll: bool = False) -> str | None:
        """CloakBrowser-pool cho trang LISTING (scroll=True) — httpx KHÔNG chạy JS nên bỏ sót
        phần infinite-scroll (vd appsumo httpx chỉ ra 40/117 sản phẩm). Trang chi tiết
        (scroll=False) thử httpx trước cho NHANH, chỉ chuyển browser khi httpx fail/JS-render.

        Lưu ý: trang listing dùng browser NHƯNG không latch use_browser → trang chi tiết vẫn
        được thử httpx nhanh (site như appsumo có detail tĩnh, httpx tải tốt)."""
        if self.at_page_limit:
            return None
        # Trang chi tiết: httpx-first.
        if not scroll and not self.use_browser:
            async with self._http_sem:
                self.pages_fetched += 1
                html = await _fetch_httpx(url)
            if html and not _is_cf_blocked(html) and _has_enough_links(html):
                return html
            self.use_browser = True
            log.info("[discovery] latching browser mode for site (httpx failed/JS-render): %s", url)
            return await self._fetch_pool(url, scroll=scroll)
        # Trang listing (scroll=True) hoặc đã latch browser → dùng pool trực tiếp.
        self.pages_fetched += 1
        html = await self._fetch_pool(url, scroll=scroll)
        # An toàn: nếu browser hỏng trên trang listing chưa latch → thử httpx (site chỉ hợp httpx).
        if html is None and scroll and not self.use_browser:
            fb = await _fetch_httpx(url)
            if fb and not _is_cf_blocked(fb) and _has_enough_links(fb):
                log.info("[discovery] listing: browser lỗi → dùng tạm httpx (có thể thiếu phần infinite-scroll): %s", url)
                return fb
        return html

    async def _fetch_pool(self, url: str, scroll: bool = False) -> str | None:
        if not await self._ensure_pool():
            return None
        if self._free is None:
            return None
        # Block until an IP is free; spreads load round-robin across the pool.
        try:
            idx = await self._free.get()
        except Exception:
            return None
        entry: _PoolEntry = self._pool[idx]
        requeued = False
        try:
            await asyncio.sleep(POLITE_DELAY_SEC + random.uniform(0, POLITE_JITTER_SEC))
            self.browser_attempts += 1
            html = await _nav_with_timeout(entry.browser, url, scroll=scroll,
                                           proxy_url=entry.proxy_url, ctx=self)
            if html is None or _is_cf_blocked(html):
                # Tường CF mà thiếu (proxy + CapSolver key) → không có đường nào qua;
                # backoff 8+20+45s chỉ bắt job chờ vô ích (job #54/#55 mất ~4 phút).
                # Schedule rỗng → nhánh `else` của for vẫn chạy → bookkeeping giữ nguyên.
                schedule = [] if (self.cf_seen and _cf_unsolvable(self)) else BLOCK_BACKOFF_SEC
                if not schedule:
                    log.error("[discovery] [%s] tường Cloudflare không giải được "
                              "(cần proxy + CapSolver key) → dừng ngay, bỏ backoff: %s",
                              entry.label, url)
                for backoff in schedule:
                    if self.aborted or self.should_stop:
                        return None
                    log.warning("[discovery] [%s] blocked, backoff %.0fs: %s", entry.label, backoff, url)
                    await asyncio.sleep(backoff)
                    html = await _nav_with_timeout(entry.browser, url, scroll=scroll,
                                                   proxy_url=entry.proxy_url, ctx=self)
                    if html is not None and not _is_cf_blocked(html):
                        break
                else:
                    # Full failure on this IP.
                    self.consecutive_blocks += 1
                    self.browser_failures += 1
                    entry.blocks += 1
                    if entry.blocks >= PER_PROXY_MAX_BLOCKS and len(self._pool) > 1:
                        entry.dead = True
                        log.error("[discovery] IP '%s' bị chặn liên tục → loại khỏi pool (còn %d IP)",
                                  entry.label, self.live_browsers)
                        try:
                            await entry.browser.close()
                        except Exception:
                            pass
                    self._maybe_abort()
                    return None
            # Success → reset counters for this IP and globally.
            entry.blocks = 0
            self.consecutive_blocks = 0
            return html
        finally:
            # Return the IP to rotation unless it was just marked dead.
            if not entry.dead:
                self._free.put_nowait(idx)
                requeued = True
            # If every IP died, give up.
            if not requeued and self.live_browsers == 0 and not self.aborted:
                self.aborted = True
                log.error("[discovery] aborting — tất cả IP trong pool đã bị chặn")

    def _maybe_abort(self) -> None:
        """Abort if rate-limiting is hopeless across the whole pool."""
        if self.aborted:
            return
        fail_rate = self.browser_failures / max(self.browser_attempts, 1)
        too_many_consecutive = self.consecutive_blocks >= MAX_CONSECUTIVE_BLOCKS
        high_fail_rate = self.browser_attempts >= BLOCK_RATE_MIN_SAMPLE and fail_rate >= BLOCK_RATE_THRESHOLD
        no_live = self.live_browsers == 0
        if too_many_consecutive or high_fail_rate or no_live:
            self.aborted = True
            log.error("[discovery] aborting — %d/%d fetches blocked (%.0f%%), live IPs=%d",
                      self.browser_failures, self.browser_attempts, fail_rate * 100, self.live_browsers)

    # Back-compat alias (callers still use the old name)
    async def _fetch_nodriver(self, url: str, scroll: bool = False) -> str | None:
        return await self._fetch_pool(url, scroll=scroll)

    async def _fetch_headed(self, url: str, scroll: bool = True) -> str | None:
        """Tải trang bằng trình duyệt HEADED (không headless). Dùng cho site trả trang
        rỗng lưới với headless (toolify /category/ nhận diện bot qua fingerprint headless).
        Tạo 1 browser headed dùng lại cho cả lần quét (dùng proxy đầu tiên nếu có). Trả
        None nếu không tạo được browser (vd server không có màn hình) và tắt thử lại sau."""
        if self._headed_disabled:
            return None
        async with self._headed_lock:
            if self._headed_browser is None:
                from app.services.browser.session import get_browser
                proxy = self.proxy_urls[0] if self.proxy_urls else None
                try:
                    self._headed_browser = await get_browser(headless=False, proxy_url=proxy)
                except Exception as e:
                    log.warning("[discovery] không tạo được trình duyệt headed: %s", e)
                    self._headed_browser = None
                if self._headed_browser is None:
                    self._headed_disabled = True
                    return None
                log.info("[discovery] đã bật trình duyệt HEADED cho fallback (site chặn headless)")
        proxy = self.proxy_urls[0] if self.proxy_urls else None
        self.browser_attempts += 1
        # ctx=self: đường headed cũng ghi nhận cf_seen + cache/tái dùng cf_clearance
        # (CapSolver) — capterra vừa render gridless với headless vừa dựng tường CF.
        return await _nav_with_timeout(self._headed_browser, url, scroll=scroll, proxy_url=proxy, ctx=self)

    async def stop(self) -> None:
        for entry in self._pool:
            if entry.browser is not None and not entry.dead:
                try:
                    await entry.browser.close()
                except Exception:
                    pass
        self._pool.clear()
        if self._headed_browser is not None:
            try:
                await self._headed_browser.close()
            except Exception:
                pass
            self._headed_browser = None


CONTENT_READY_BYTES = 45000   # most real content pages exceed this once rendered


def _cf_cookie_key(url: str, proxy_url: str | None) -> str:
    """Khoá cache cf_clearance: cookie gắn theo (domain, IP) nên phải khoá theo cả hai."""
    return f"{_get_root_domain(url)}|{proxy_url or 'direct'}"


def _cf_unsolvable(ctx: "_CrawlCtx") -> bool:
    """True khi ta KHÔNG có cách giải tường CF: CapSolver AntiCloudflareTask bắt buộc
    proxy (cf_clearance gắn theo IP) VÀ cần API key. Thiếu một trong hai thì backoff
    8+20+45s cũng không đổi được kết quả — nên dừng ngay thay vì bắt user chờ 4 phút."""
    from app.core.config import settings
    return not (ctx.proxy_urls and settings.capsolver_api_key)


async def _solve_cf_challenge(page, url: str, proxy_url: str,
                              ctx: "_CrawlCtx | None" = None) -> str | None:
    """Giải tường Cloudflare TOÀN TRANG (interstitial 'Just a moment') qua CapSolver
    AntiCloudflareTask: solve trên ĐÚNG proxy đang dùng → cf_clearance gắn theo IP →
    set cookie vào context → reload. Chỉ chạy khi có proxy + CapSolver key. Với site
    mà proxy residential không bị challenge thì hàm này không được gọi (không cần).

    Cookie giải được lưu vào `ctx.cf_cookies` để các trang sau tiêm lại (mỗi
    new_page() là context mới, không tự thừa hưởng cookie) → 1 lượt solve/domain/IP."""
    from app.core.config import settings
    if not (proxy_url and settings.capsolver_api_key):
        return None
    ck_key = _cf_cookie_key(url, proxy_url)
    if ctx is not None:
        if ck_key in ctx.cf_solve_failed:
            return None          # đã thử và không giải được → đừng đốt credit lần nữa
        ctx.solver_used = True
    try:
        from app.services.captcha.capsolver import CapSolver
        ua = await page.evaluate("navigator.userAgent")
        html = await page.evaluate("document.documentElement.outerHTML")
        cs = CapSolver(api_key=settings.capsolver_api_key, proxy_url=proxy_url)
        res = await cs.solve_cloudflare_challenge(url, user_agent=ua, html=html)
        cookies = res.get("cookies") or {}
        if not cookies:
            if ctx is not None:
                ctx.cf_solve_failed.add(ck_key)
            return None
        host = urlparse(url).hostname or ""
        base = ("." + ".".join(host.split(".")[-2:])) if host.count(".") >= 1 else host
        cl = [{"name": n, "value": str(v), "domain": base, "path": "/", "secure": True}
              for n, v in cookies.items()]
        await page.context.add_cookies(cl)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=int(NAV_TIMEOUT_SEC * 1000))
        except Exception:
            pass
        html2 = await _poll_rendered_html(page)
        if html2 and not _is_cf_blocked(html2):
            if ctx is not None:
                ctx.solver_ok = True
                ctx.cf_cookies[ck_key] = cl   # dùng lại cho MỌI trang sau của domain này
            log.info("[discovery] CF challenge SOLVED (cf_clearance) → %s", url)
            return html2
        log.warning("[discovery] CF challenge: solve xong nhưng vẫn bị chặn: %s", url)
        if ctx is not None:
            ctx.cf_solve_failed.add(ck_key)
        return None
    except Exception as e:
        log.warning("[discovery] CF challenge solve lỗi (%s): %s", url, e)
        if ctx is not None:
            ctx.cf_solve_failed.add(ck_key)
        return None


async def _nav_with_timeout(browser, url: str, scroll: bool = False, proxy_url: str | None = None,
                            ctx: "_CrawlCtx | None" = None) -> str | None:
    """Open a page, navigate + render under a hard timeout, return outerHTML,
    always close the page. A dead/slow proxy can hang navigation forever; the
    timeout caps it so the crawl can backoff/abort instead of freezing.
    `scroll=True` cuộn để kích hoạt lazy-load trên trang listing SPA.
    `proxy_url` → cho phép giải tường Cloudflare qua CapSolver khi gặp interstitial."""
    page = None
    try:
        async def _go():
            nonlocal page
            page = await browser.new_page()
            # Tiêm lại cf_clearance đã giải trước đó: new_page() là context MỚI nên
            # không thừa hưởng cookie → không tiêm thì trang nào cũng gặp lại tường.
            if ctx is not None:
                cached = ctx.cf_cookies.get(_cf_cookie_key(url, proxy_url))
                if cached:
                    try:
                        await page.context.add_cookies(cached)
                    except Exception:
                        pass
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=int(NAV_TIMEOUT_SEC * 1000))
            except Exception:
                pass  # poll the DOM anyway — partial content may be enough
            html = await _poll_rendered_html(page)
            if html and _is_cf_blocked(html):
                if ctx is not None:
                    ctx.cf_seen = True
                # CapSolver AntiCloudflareTask BẮT BUỘC có proxy (cf_clearance gắn theo
                # IP) → chế độ IP trực tiếp không giải được, chỉ ghi nhận để báo user.
                if proxy_url:
                    solved = await _solve_cf_challenge(page, url, proxy_url, ctx=ctx)
                    if solved:
                        html = solved
            if scroll and html and not _is_cf_blocked(html):
                html = await _scroll_to_load(page, html)
            return html
        budget = NAV_TIMEOUT_SEC + 5 + (SCROLL_TIMEOUT_SEC if scroll else 0) + (CF_SOLVE_TIMEOUT_SEC if proxy_url else 0)
        return await asyncio.wait_for(_go(), timeout=budget)
    except asyncio.TimeoutError:
        log.warning("[discovery] navigation timeout — proxy/site không phản hồi: %s", url)
        return None
    except Exception as e:
        log.debug("[discovery] navigation error %s: %s", url, e)
        return None
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass


async def _poll_rendered_html(page, max_wait: float = 12.0, interval: float = 0.4) -> str | None:
    """
    Return the page's live outerHTML as soon as it has rendered, instead of
    waiting for the full load event (which can take 15s+ on ad-heavy pages).

    A cold browser hitting Cloudflare first shows a small "Just a moment"
    challenge page that clears to real content after a few seconds — we must
    NOT return that as content. _is_cf_blocked() acts as a "not ready yet" gate,
    and we keep polling (up to max_wait) for the challenge to clear.

    Hết thời gian mà tường vẫn đứng → trả về CHÍNH trang tường đó (KHÔNG phải None)
    để caller phân biệt được "tường Cloudflare" với "không tải được gì". Trước đây
    trả None nên `_nav_with_timeout` không bao giờ thấy tường → cờ cf_seen không bật
    và CapSolver KHÔNG BAO GIỜ được gọi (nhánh giải CF là dead code).

    Early-return triggers (whichever first, once NOT CF-blocked):
      1. HTML exceeds CONTENT_READY_BYTES — real content has rendered.
      2. HTML length stable across two reads (delta < 3%) and non-trivial.
      3. max_wait reached → return whatever we have.
    """
    prev_len = 0
    last_html = ""
    blocked_html = ""     # trang tường/quá nhỏ đọc được gần nhất (fallback)
    steps = max(1, int(max_wait / interval))
    for _ in range(steps):
        await asyncio.sleep(interval)
        try:
            html = await page.evaluate("document.documentElement.outerHTML")
        except Exception:
            continue
        if not isinstance(html, str) or not html:
            continue
        # Cloudflare challenge still showing → keep waiting for it to clear
        if _is_cf_blocked(html):
            blocked_html = html
            prev_len = 0
            continue
        last_html = html
        cur_len = len(html)
        # 1. Content rendered (size threshold) → return immediately
        if cur_len >= CONTENT_READY_BYTES:
            return html
        # 2. Stable + substantial → content settled, return
        if cur_len > 15000 and prev_len > 0 and abs(cur_len - prev_len) / max(prev_len, 1) < 0.03:
            return html
        prev_len = cur_len
    return last_html or blocked_html or None


async def _click_load_more(page) -> bool:
    """Tìm & bấm nút 'Show more'/'Load more' (click-to-load, KHÔNG phải infinite-scroll —
    vd saashub: <a id="load_more_services_btn" href="javascript://">Show more</a>). Nút này
    XHR tải thêm item rồi chèn vào DOM. Trả True nếu đã bấm được 1 nút đang hiển thị."""
    try:
        return bool(await page.evaluate(
            "(() => {"
            "  const cands=[...document.querySelectorAll('a,button,[role=button],input[type=button],input[type=submit]')];"
            "  const btn=cands.find(e=>{"
            "    const t=((e.innerText||e.value||'')+'').trim().toLowerCase();"
            "    const idc=((e.id||'')+' '+((e.className||'')+'')).toLowerCase();"
            "    const byText=/^(show|load|see|view)\\s+more\\b/.test(t) || /^more( results| items)?$/.test(t);"
            "    const byAttr=/load[_-]?more|show[_-]?more/.test(idc);"
            "    return (byText||byAttr) && e.offsetParent!==null && !e.disabled;"
            "  });"
            "  if(btn){ btn.scrollIntoView({block:'center'}); btn.click(); return true; }"
            "  return false;"
            "})()"
        ))
    except Exception:
        return False


async def _scroll_to_load(page, html: str) -> str:
    """Kích hoạt infinite-scroll (SPA như toolify.ai chỉ render batch đầu trong
    SSR, phần còn lại tải khi cuộn tới đáy). Cuộn TỪNG BƯỚC theo chiều cao viewport
    để sentinel lazy-load đi qua viewport (cuộn thẳng tới đáy rồi đứng yên KHÔNG
    kích hoạt lại observer). Khi tới đáy mà DOM không lớn thêm, thử BẤM nút 'Show more'
    /'Load more' (click-to-load như saashub) trước khi kết luận đã hết. Chỉ tính 'đã ổn
    định' khi ĐÃ tới đáy, không lớn thêm VÀ không còn nút để bấm, SCROLL_SETTLE_ROUNDS lần
    liên tiếp. No-op với trang tĩnh. Lỗi eval → trả HTML tốt nhất đang có."""
    await asyncio.sleep(SCROLL_INITIAL_SETTLE_SEC)  # để SPA hydrate xong trước khi cuộn
    try:
        html = await page.evaluate("document.documentElement.outerHTML") or html
    except Exception:
        pass
    last_len = len(html)
    stable = 0
    rounds = 0
    clicks = 0
    for rounds in range(1, SCROLL_MAX_ROUNDS + 1):
        try:
            at_bottom = await page.evaluate(
                "(() => { window.scrollBy(0, Math.floor(window.innerHeight * 0.9)); "
                "return (window.innerHeight + window.scrollY) >= (document.body.scrollHeight - 8); })()"
            )
        except Exception:
            break
        await asyncio.sleep(SCROLL_WAIT_SEC if at_bottom else SCROLL_STEP_WAIT_SEC)
        try:
            cur = await page.evaluate("document.documentElement.outerHTML")
        except Exception:
            break
        if not isinstance(cur, str) or not cur:
            break
        html = cur
        grew = len(cur) > last_len * 1.01   # DOM lớn thêm >1% → còn nội dung tải về
        last_len = len(cur)
        if at_bottom:
            if grew:
                stable = 0
            else:
                # Tới đáy, cuộn không ra thêm → thử BẤM nút "Show more" (click-to-load).
                if clicks < LOAD_MORE_MAX_CLICKS and await _click_load_more(page):
                    clicks += 1
                    await asyncio.sleep(SCROLL_WAIT_SEC)
                    stable = 0
                    continue
                stable += 1
                if stable >= SCROLL_SETTLE_ROUNDS:
                    break
    log.info("[discovery] scroll-load: %d bước cuộn, %d lần bấm 'Show more', HTML cuối %d bytes",
             rounds, clicks, len(html))
    return html


# ─── httpx (fast path) ───────────────────────────────────────────────────────

async def _fetch_httpx(url: str) -> str | None:
    """Returns HTML string, empty string on CF/403 (→ trigger browser), None on real error."""
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers=_HEADERS,
        ) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (403, 429, 503):
                return ""  # signal → browser fallback
    except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout):
        log.debug("[discovery] httpx timeout→browser: %s", url)
        return ""   # trigger browser
    except Exception as e:
        log.debug("[discovery] httpx error %s: %s", url, e)
    return None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_root_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc or ""
        return host.removeprefix("www.").lower().split(":")[0]
    except Exception:
        return ""


def _get_title(html: str) -> str | None:
    try:
        soup = BeautifulSoup(html, "lxml")
        t = soup.find("title")
        return t.get_text(strip=True)[:255] if t else None
    except Exception:
        return None


# ─── Redirect-wrapped outbound links ─────────────────────────────────────────
# Nhiều directory KHÔNG link thẳng ra domain thật của sản phẩm mà bọc qua link
# chuyển hướng NỘI BỘ (cùng domain nguồn) → crawler tưởng là link nội bộ, bỏ sót
# domain thật. Ví dụ saasworthy: /redir.php?...&rtoken=<base64 JSON có field 'ru'=URL>.
# Hai hàm dưới giải ra URL đích thật để lấy domain.
_REDIRECT_URL_PARAMS = ("url", "u", "redirect", "redirect_url", "redirecturl", "target",
                        "goto", "dest", "destination", "to", "out", "link", "next", "ru")
_REDIRECT_TOKEN_PARAMS = ("rtoken", "token", "data", "payload", "q", "r", "ru", "url", "redirect")
# Chỉ parse lại HTML khi trang THỰC SỰ có dấu hiệu link chuyển hướng (khỏi tốn công).
_REDIRECT_HINT = re.compile(r"/redir|/redirect|/goto|/out[/?]|claim-bx|get-listed|/visit\b|"
                            r"[?&](?:redirect|redirect_url|rtoken|goto|dest|destination)=", re.I)
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>\\)]+")
# Path của link chuyển hướng NGOÀI domain nguồn vẫn đáng giải URL đích (vd capterra bọc URL
# thật qua gartner: digitalmarkets.gartner.com/get-listed/claim-bx?url=<realURL>&name=…).
_WRAPPER_PATH = re.compile(r"claim|get-listed|/visit|/redir|/goto|/out|/away|/link/", re.I)


def _is_redirect_wrapper(full_url: str, src_domain: str) -> bool:
    """Link đáng giải URL đích: (a) cùng domain nguồn (redirect nội bộ), HOẶC (b) path có dấu
    hiệu wrapper (claim/get-listed/visit…) dù khác domain (vd gartner claim-bx cho capterra)."""
    d = _get_root_domain(full_url)
    if not d:
        return False
    if d == src_domain:
        return True
    try:
        return bool(_WRAPPER_PATH.search(urlparse(full_url).path or ""))
    except Exception:
        return False


def _resolve_redirect_url(href: str) -> str | None:
    """Nếu href là link chuyển hướng bọc 1 URL đích thật → trả URL đó, ngược lại None.
    Xử lý: (1) tham số query là URL http trực tiếp (?url=/?u=…); (2) tham số dạng base64
    (rtoken…) giải ra JSON có field URL, hoặc text CHÍNH LÀ 1 URL. Bảo thủ để tránh
    dương tính giả (không nhận URL 'lẫn' trong rác nhị phân)."""
    try:
        q = parse_qs(urlparse(href).query)
    except Exception:
        return None
    if not q:
        return None
    # (1) tham số chứa URL http trực tiếp — chỉ ở các tên tham số kiểu redirect.
    for k in _REDIRECT_URL_PARAMS:
        for v in q.get(k, []):
            v = unquote(v or "").strip()
            if v.startswith(("http://", "https://")) and _get_root_domain(v):
                return v
    # (2) tham số base64 (rtoken…) → JSON có URL, hoặc text CHÍNH LÀ URL.
    for k in _REDIRECT_TOKEN_PARAMS:
        for v in q.get(k, []):
            v = unquote(v or "").strip()
            if len(v) < 16:
                continue
            for pad in ("", "=", "==", "==="):
                try:
                    raw = base64.urlsafe_b64decode(v + pad).decode("utf-8", "strict")
                except Exception:
                    continue
                try:
                    obj = json.loads(raw)
                    if isinstance(obj, dict):
                        for kk in ("ru", "url", "u", "redirect", "target", "link", "dest"):
                            tv = obj.get(kk)
                            if isinstance(tv, str) and tv.startswith("http") and _get_root_domain(tv):
                                return tv
                except Exception:
                    pass
                s = raw.strip()
                if s.startswith(("http://", "https://")) and _URL_IN_TEXT.fullmatch(s) and _get_root_domain(s):
                    return s
                break   # decode được base64 nhưng không phải URL/JSON-URL → thôi
    return None


def _rewrite_redirect_hrefs(html: str, base_url: str) -> str:
    """Đổi các link chuyển hướng nội bộ trong HTML thành URL đích THẬT, để mọi bước trích
    outbound (label matcher, fallback, _extract_all_links) nhìn thấy domain thật. No-op
    nếu trang không có dấu hiệu chuyển hướng (khỏi parse lại HTML nặng)."""
    if not html or not _REDIRECT_HINT.search(html):
        return html
    src_domain = _get_root_domain(base_url)
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return html
    changed = False
    for a in soup.find_all("a", href=True):
        raw = a.get("href", "")
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        if not raw:
            continue
        full = urljoin(base_url, raw)
        # link redirect nội bộ HOẶC wrapper ngoài domain (gartner claim-bx…)
        if not _is_redirect_wrapper(full, src_domain):
            continue
        target = _resolve_redirect_url(full)
        if target and _get_root_domain(target) and _get_root_domain(target) != src_domain:
            a["href"] = target
            changed = True
    return str(soup) if changed else html


def _find_redirect_outbound(html: str, base_url: str, exclude: set[str]) -> dict | None:
    """Trên trang chi tiết sản phẩm, tìm link 'Visit Website' bọc qua chuyển hướng → trả
    {url, text} của domain đích ĐẦU TIÊN hợp lệ. Bọc NỘI BỘ (saasworthy /redir.php?…rtoken=…)
    HOẶC wrapper ngoài domain (capterra→gartner claim-bx?url=<realURL>). Ưu tiên cao hơn
    fallback outbound chung (tránh bị domain trung gian gartner/g2 chen lên). No-op nếu trang
    không có dấu hiệu chuyển hướng."""
    if not html or not _REDIRECT_HINT.search(html):
        return None
    src = _get_root_domain(base_url)
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return None
    for a in soup.find_all("a", href=True):
        raw = a.get("href", "")
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        if not raw:
            continue
        full = urljoin(base_url, raw)
        if not _is_redirect_wrapper(full, src):   # redirect nội bộ HOẶC wrapper ngoài domain
            continue
        target = _resolve_redirect_url(full)
        if not target:
            continue
        dom = _get_root_domain(target)
        if not dom or dom == src or dom in exclude:
            continue
        text = a.get_text(strip=True)[:200]
        # Anchor text rỗng/generic (URL, "Manage this listing", "Visit website"…) → ưu tiên
        # tham số name= của wrapper (gartner …&name=Softr), nếu không có thì dùng tên domain.
        if text.startswith("http") or not text or re.search(r"manage|claim|listing|visit\s*website|get\s*listed", text, re.I):
            try:
                nm = parse_qs(urlparse(full).query).get("name", [""])[0].strip()
            except Exception:
                nm = ""
            text = nm or dom
        return {"url": target, "text": text}
    return None


def _extract_all_links(html: str, base_url: str) -> list[dict]:
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return []
    links: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        raw = a.get("href", "")
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        if not raw:
            continue
        href = urljoin(base_url, raw).split("#")[0].rstrip("/") or raw
        if href in seen or not href.startswith("http"):
            continue
        seen.add(href)
        links.append({"href": href, "text": a.get_text(strip=True)[:200]})
    return links


# ─── DB helpers ──────────────────────────────────────────────────────────────

async def _save_candidate_if_new(
    source_id: int,
    raw_url: str,
    domain: str,
    level: int,
    depth: int,
    detection_method: str,
    suggested_name: str | None,
    source_page_url: str | None,
    source_page_title: str | None,
    is_primary: bool = False,
    user_id: int | None = None,
    ad_days_shown: int | None = None,
    ad_first_shown: datetime | None = None,
    ad_last_shown: datetime | None = None,
) -> bool:
    async with SessionLocal() as s:
        # Dedupe theo (user_id, domain): 2 user quét trùng nguồn vẫn ra đủ dự án.
        existing = (await s.execute(
            select(DiscoveryCandidate.id).where(
                DiscoveryCandidate.domain == domain,
                DiscoveryCandidate.user_id == user_id,
            )
        )).scalar_one_or_none()
        if existing is not None:
            return False
        now = datetime.utcnow()
        s.add(DiscoveryCandidate(
            user_id=user_id,
            source_id=source_id,
            raw_url=raw_url,
            domain=domain,
            level=level,
            depth=depth,
            is_primary=is_primary,
            detection_method=detection_method,
            suggested_name=(suggested_name or "")[:255],
            source_page_url=source_page_url,
            source_page_title=source_page_title,
            ad_days_shown=ad_days_shown,
            ad_first_shown=ad_first_shown,
            ad_last_shown=ad_last_shown,
            status="discovered",
            created_at=now,
            updated_at=now,
        ))
        try:
            await s.commit()
            return True
        except Exception:
            await s.rollback()
            return False


async def _get_blacklist() -> set[str]:
    async with SessionLocal() as s:
        rows = (await s.execute(text("SELECT domain FROM domain_blacklist"))).fetchall()
    return {r[0].lower() for r in rows}


# ─── Core: process one inner page ────────────────────────────────────────────
#
# Strategy:
#   Level A — direct outbound found directly on source page (in _crawl_source_page)
#   Level B — outbound found on an internal page (label_match > fallback_outbound)
#   Level C — outbound found 2 hops deep (review page inside a list page)
#
# _process_inner is called for internal pages. It:
#   1. Saves any outbound links it finds (label → fallback priority)
#   2. ALWAYS follows sub-internal links if depth < MAX_DEPTH
#      → this lets us go: popular-list → individual-review → broker-homepage

_ALTTO_SOFTWARE_PATH = re.compile(r'^/software/[^/]+/?$')


def _altto_about_url(url: str) -> str | None:
    """alternativeto giấu website sản phẩm trong JSON ở /software/<slug>, nhưng trang
    /software/<slug>/about/ có link 'Official Website' SẠCH → chuyển sang /about/.
    Trả None nếu không phải trang /software/<slug> của alternativeto."""
    try:
        p = urlparse(url)
    except Exception:
        return None
    if "alternativeto.net" not in p.netloc or not _ALTTO_SOFTWARE_PATH.match(p.path):
        return None
    slug = p.path.strip("/").split("/")[1]
    return f"{p.scheme}://{p.netloc}/software/{slug}/about/"


def _alternativeto_official_site(html: str) -> str | None:
    """Lấy href 'Official Website' trong mục 'Official Links' của trang /about/ (domain
    sản phẩm sạch). Neo vào tiêu đề 'Official Links' rồi lấy external href đầu tiên —
    tránh generic fallback vớ nhầm quảng cáo (protonvpn…) ở đầu trang."""
    i = html.find("Official Links")
    if i < 0:
        return None
    window = html[i:i + 1200]
    for m in re.finditer(r'href="(https?://[^"]+)"', window):
        u = m.group(1)
        if "alternativeto.net" in u:
            continue
        if re.search(r'facebook|twitter|linkedin|youtube|instagram|/cdn|gstatic|'
                     r'googlesyndication|googletagmanager|apple\.com|play\.google|'
                     r'reddit|bsky|mas\.to|threads\.', u, re.I):
            continue
        return u
    return None


async def _process_inner(ctx: _CrawlCtx, inner_url: str, depth: int) -> int:
    # alternativeto giấu website sản phẩm trong JSON ở /software/<slug>; trang /about/
    # có link 'Official Website' sạch → chuyển sang /about/ để lấy đúng domain.
    inner_url = _altto_about_url(inner_url) or inner_url
    if inner_url in ctx.visited or ctx.at_page_limit:
        return 0
    ctx.visited.add(inner_url)

    inner_html = await ctx.fetch(inner_url)
    if not inner_html:
        return 0

    # Auto-switch to browser mode if CF detected
    if not ctx.use_browser and _is_cf_blocked(inner_html):
        log.info("[discovery] CF→browser switch at depth=%d %s", depth, inner_url)
        ctx.use_browser = True
        inner_html = await ctx._fetch_nodriver(inner_url)
        if not inner_html:
            return 0

    exclude = {ctx.source_domain}
    page_title = _get_title(inner_html)
    new_count = 0

    # ── 0a. alternativeto: 'Official Website' ở trang /about/ (domain sản phẩm sạch) ──
    #   Ưu tiên cao nhất cho alternativeto: trang để website trong JSON + có quảng cáo
    #   (protonvpn…) ở đầu → generic fallback dễ vớ nhầm. Lấy đúng link trong Official Links.
    altto_site = _alternativeto_official_site(inner_html) if "alternativeto.net" in inner_url else None
    if altto_site:
        domain = _get_root_domain(altto_site)
        if domain and domain not in exclude and domain not in ctx.blacklist:
            saved = await _save_candidate_if_new(
                source_id=ctx.source_id,
                raw_url=altto_site,
                domain=domain,
                level=2,
                depth=depth,
                detection_method="altto_official",
                suggested_name=page_title,
                source_page_url=inner_url,
                source_page_title=page_title,
                is_primary=True,
                user_id=ctx.user_id,
            )
            if saved:
                new_count += 1

    # ── 0. Redirect-wrapped outbound (saasworthy /redir.php?…rtoken=<b64 URL>) ──────
    #   Ưu tiên CAO NHẤT: đây là link 'Visit Website' đích thật của trang chi tiết, tránh
    #   bị domain thương hiệu của chính directory (vd saasworthy.ai) chen lên fallback.
    redir = None
    if not altto_site:
        redir = _find_redirect_outbound(inner_html, inner_url, exclude | ctx.blacklist)
    if redir:
        domain = _get_root_domain(redir["url"])
        if domain:
            saved = await _save_candidate_if_new(
                source_id=ctx.source_id,
                raw_url=redir["url"],
                domain=domain,
                level=2,
                depth=depth,
                detection_method="redirect_outbound",
                suggested_name=redir["text"],
                source_page_url=inner_url,
                source_page_title=page_title,
                is_primary=True,
                user_id=ctx.user_id,
            )
            if saved:
                new_count += 1

    # ── 1. Label hints (highest confidence: "Company Website", "Official Site", …) ──
    labeled = None
    if not altto_site and not redir:
        labeled = find_labeled_outbound_link(inner_html, exclude | ctx.blacklist)
    if labeled:
        domain = _get_root_domain(labeled["url"])
        if domain:
            saved = await _save_candidate_if_new(
                source_id=ctx.source_id,
                raw_url=labeled["url"],
                domain=domain,
                level=2,
                depth=depth,
                detection_method="label_match",
                suggested_name=labeled["text"],
                source_page_url=inner_url,
                source_page_title=page_title,
                is_primary=True,
                user_id=ctx.user_id,
            )
            if saved:
                new_count += 1

    # ── 2. Fallback outbound (first external link, near H1 preferred) ──────────────
    if not altto_site and not redir and not labeled:
        fallback = find_any_outbound_link(inner_html, exclude | ctx.blacklist)
        if fallback:
            domain = _get_root_domain(fallback["url"])
            if domain:
                near = is_near_h1(fallback.get("node"), inner_html)
                saved = await _save_candidate_if_new(
                    source_id=ctx.source_id,
                    raw_url=fallback["url"],
                    domain=domain,
                    level=2,
                    depth=depth,
                    detection_method="fallback_outbound",
                    suggested_name=fallback["text"],
                    source_page_url=inner_url,
                    source_page_title=page_title,
                    is_primary=near,
                    user_id=ctx.user_id,
                )
                if saved:
                    new_count += 1

    # ── 3. ALWAYS recurse into sub-internal links (list page → review pages) ───────
    #    This is the key difference vs. old code: we do NOT stop after finding outbound.
    #    Enables: popular-list page (saves 1) → 20 review pages (saves 20 more).
    if depth < MAX_DEPTH and not ctx.at_page_limit:
        all_links = _extract_all_links(inner_html, inner_url)
        sub_links: list[str] = []
        seen_sub: set[str] = set()
        for link in all_links:
            href = link["href"]
            if _get_root_domain(href) != ctx.source_domain:
                continue
            if is_junk_link(href, inner_url, section_anchor=ctx.source_root_url):
                continue
            if href in ctx.visited or href in seen_sub:
                continue
            seen_sub.add(href)
            sub_links.append(href)

        sub_links = sub_links[:MAX_INTERNAL_PER_PAGE]
        if sub_links:
            log.debug("[discovery] depth=%d → %d sub-links at %s", depth, len(sub_links), inner_url)
            tasks = [_process_inner(ctx, u, depth + 1) for u in sub_links]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, int):
                    new_count += r


    return new_count


# ─── Pagination expansion ────────────────────────────────────────────────────

def _generate_pagination_urls(base_url: str, html: str, max_pages: int | None = None) -> list[str]:
    """
    If this is a paginated listing (e.g. ?page=1&per-page=10), detect the total
    page count from "Page X of N" text and generate the next listing page URLs.
    `max_pages` caps the total number of listing pages crawled (incl. the source page).
    Returns [] if not a paginated listing or only 1 page.

    Ba kiểu:
    • Phân trang theo PATH `.../page/N/` (WordPress/aixploria…) → đi tiến path.
    • Query pin page > 1 ('Page X of N') → đi tiến từ trang đó.
    • Crawl từ đầu → renumber từ page 2, per-page=50 (ít request hơn).
    """
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    parsed = urlparse(base_url)

    # ── (A) Phân trang theo PATH: .../page/N/ (không có ?page=, không 'Page X of N') ──
    pm = re.search(r'/page/(\d+)/?$', parsed.path)
    if pm:
        start = int(pm.group(1))
        html_max = max((int(x) for x in re.findall(r'/page/(\d+)', html)), default=0)
        if max_pages and max_pages > 0:
            last = start + max_pages - 1
            if html_max > start:
                last = min(last, html_max)   # đừng vượt số trang thật (nếu biết)
        elif html_max > start:
            last = html_max
        else:
            return []   # không rõ tổng số trang → không tự đi
        if last <= start:
            return []
        prefix = parsed.path[:pm.start()]
        trail = "/" if pm.group(0).endswith("/") else ""
        urls = [urlunparse(parsed._replace(path=f"{prefix}/page/{p}{trail}"))
                for p in range(start + 1, last + 1)]
        log.info("[discovery] pagination (path /page/N/, từ trang %d → %d): %d trang", start, last, len(urls))
        return urls

    # ── (B)/(C) Query-based pagination (`?page=N` hoặc `?p=N`) ──
    qs = parse_qs(parsed.query, keep_blank_values=True)

    # Tên tham số phân trang: mặc định 'page'; nhiều directory (alternativeto) dùng 'p'.
    # Chọn 'p' nếu URL đã ghim ?p=, hoặc trang có ≥2 link ?p=N khác nhau (đủ chắc là bộ
    # điều hướng phân trang, tránh 1 param 'p' lạc).
    if qs.get("p") and not qs.get("page"):
        page_param = "p"
    elif not qs.get("page") and len({int(n) for n in re.findall(r'[?&]p=(\d+)', html)}) >= 2:
        page_param = "p"
    else:
        page_param = "page"

    def _build(page_num: int, per_page: int | None = None) -> str:
        new_qs = {k: v[0] for k, v in qs.items()}
        new_qs[page_param] = str(page_num)
        if per_page is not None:
            new_qs["per-page"] = str(per_page)
        return urlunparse(parsed._replace(query=urlencode(new_qs)))

    start_page = 1
    if qs.get(page_param):
        try:
            start_page = max(1, int(qs[page_param][0]))
        except ValueError:
            start_page = 1

    # Tổng số trang: ưu tiên text 'Page X of N'; nếu không có thì suy ra từ các link
    # '?page=N' trên trang (toolify có ?page=2..37 nhưng KHÔNG in 'Page X of N').
    total_match = re.search(r'Page\s+\d+\s+of\s+(\d+)', html, re.I)
    native_query_pages = False   # True = phân trang ?page=N của site (per-page cố định)
    if total_match:
        total_pages = int(total_match.group(1))   # total pages at the URL's current per-page
    else:
        nums = {int(n) for n in re.findall(rf'[?&]{page_param}=(\d+)', html)}
        nums.discard(start_page)
        # Cần ≥2 số trang khác nhau để chắc là bộ điều hướng phân trang (tránh 1 param lạc);
        # cap 1000 phòng số rác gây quét vô hạn.
        if len(nums) >= 2 and 1 < max(nums) <= 1000:
            total_pages = max(nums)
            native_query_pages = True
        else:
            return []
    if total_pages <= 1:
        return []

    # ── Positional start (user ghim page>1) HOẶC phân trang ?page=N của site:
    #    đi tiến tuần tự, GIỮ NGUYÊN per-page của site (không renumber). ──
    if start_page > 1 or native_query_pages:
        last_page = total_pages
        if max_pages and max_pages > 0:
            last_page = min(last_page, start_page + max_pages - 1)  # source page counts as 1
        if last_page <= start_page:
            return []
        log.info("[discovery] pagination (forward from page %d): crawling pages %d–%d of %d",
                 start_page, start_page + 1, last_page, total_pages)
        return [_build(p) for p in range(start_page + 1, last_page + 1)]

    # ── Crawl from the top ('Page X of N'): renumber + per-page=50 for fewer requests ──
    current_per_page = int(qs.get("per-page", ["10"])[0])
    big_per_page = 50
    total_items = total_pages * current_per_page
    total_pages_big = (total_items + big_per_page - 1) // big_per_page
    if max_pages and max_pages > 0:
        total_pages_big = min(total_pages_big, max_pages)  # counts page 1 (the source page)
    if total_pages_big <= 1:
        return []

    log.info("[discovery] pagination detected: %d items → crawling %d listing pages (per-page=50)%s",
             total_items, total_pages_big, f", capped at {max_pages}" if max_pages else "")
    return [_build(p, big_per_page) for p in range(2, total_pages_big + 1)]


# ─── Core: crawl the source page (depth=1) ───────────────────────────────────

class CrawlBlockedError(RuntimeError):
    """Raised when the target site rate-limits/blocks us so hard the crawl can't proceed."""


def _diag(ctx: _CrawlCtx) -> str:
    """Số liệu chẩn đoán kèm vào thông báo lỗi — để user (và log) biết thất bại nằm ở
    tầng nào mà không phải đi soi log server."""
    parts = [f"{ctx.browser_failures}/{ctx.browser_attempts} lần fetch qua browser thất bại"]
    if ctx.cf_seen:
        parts.append("có gặp tường Cloudflare")
    if ctx.solver_ok:
        parts.append("CapSolver giải được CF")
    elif ctx.solver_used:
        parts.append("CapSolver đã thử nhưng KHÔNG qua")
    if ctx.proxy_urls:
        parts.append(f"pool {ctx.live_browsers}/{len(ctx.proxy_urls)} IP còn sống")
    return " · ".join(parts)


def _source_unreachable_reason(ctx: _CrawlCtx) -> str:
    """Thông báo cho user khi trang nguồn không tải được. Bốn ca có nguyên nhân và cách
    xử lý KHÁC nhau — trước đây mọi ca có proxy đều báo 'site chặn IP proxy', nên lỗi
    không khởi tạo được browser trên server bị chẩn đoán sai thành lỗi proxy."""
    diag = _diag(ctx)

    # Ca 1: đã chọn proxy nhưng pool rỗng vì mọi proxy chết ở health-check
    # (vd 402 Payment Required = hết tiền/hết hạn mức). KHÔNG phải lỗi site.
    if ctx.proxy_urls and ctx.proxy_dead_reason:
        return (
            f"Tất cả {len(ctx.proxy_urls)} proxy đều lỗi ({ctx.proxy_dead_reason}) → không có proxy "
            f"khả dụng. Nếu là '402 Payment Required' thì gói proxy đã HẾT TIỀN/HẾT HẠN MỨC — nạp/gia "
            f"hạn proxy. Hoặc bỏ chọn proxy nếu site không chặn theo IP, rồi thử lại."
        )

    # Ca 2: proxy sống (hoặc không dùng proxy) nhưng KHÔNG launch được Chromium nào —
    # thiếu binary CloakBrowser, thiếu lib hệ thống, /dev/shm nhỏ, hết RAM, hoặc chạy
    # uvicorn với uvloop (phải dùng --loop asyncio). Đây KHÔNG phải lỗi proxy/site.
    if ctx.live_browsers == 0 and ctx.browser_attempts == 0:
        return (
            "Không khởi tạo được browser trên server (pool rỗng) — KHÔNG phải lỗi proxy hay site. "
            "Kiểm tra: đã cài cloakbrowser chưa, thiếu lib hệ thống Chromium, /dev/shm quá nhỏ, "
            "hết RAM, hoặc uvicorn chạy uvloop (phải thêm --loop asyncio). Xem log dòng "
            "'khởi tạo browser ... lỗi' / 'pool sẵn sàng: 0 browser'."
        )

    # Ca 3: gặp tường Cloudflare mà đang chạy IP trực tiếp → CapSolver không giải được
    # (AntiCloudflareTask bắt buộc bind proxy), nên phải quét lại có proxy.
    if ctx.cf_seen and not ctx.proxy_urls:
        return (
            f"Trang nguồn dựng tường Cloudflare và crawl đang chạy IP trực tiếp nên không thể "
            f"giải (CapSolver yêu cầu proxy để gắn cf_clearance theo IP). Hãy chọn proxy rồi "
            f"quét lại. [{diag}]"
        )

    # Ca 4: fetch qua browser thất bại — site chặn/rate-limit IP đang dùng.
    if ctx.proxy_urls:
        return (
            f"Đã thử qua proxy nhưng trang nguồn không tải được — site chặn IP proxy, hoặc trang "
            f"render quá nặng/chậm nên timeout khi đi qua proxy. Thử: proxy residential, BỎ proxy "
            f"nếu site không chặn theo IP, hoặc thử lại sau. [{diag}]"
        )
    return (
        f"Trang nguồn không truy cập được — site đang chặn/giới hạn tần suất (rate-limit). "
        f"Hãy thử lại sau, hoặc dùng proxy. [{diag}]"
    )


async def _warmup(ctx: _CrawlCtx, source_url: str) -> None:
    """Best-effort visit to the site root to establish a browser session before
    hammering the listing. Failures here are non-fatal — the real fetch retries.
    Uses ctx.fetch so httpx-friendly sites aren't forced into the browser."""
    root = f"{urlparse(source_url).scheme}://{urlparse(source_url).netloc}/"
    if root.rstrip("/") == source_url.rstrip("/"):
        return  # source IS the root, nothing to warm up
    try:
        ctx.visited.add(root)  # don't re-crawl the root as a candidate page
        html = await ctx.fetch(root)
        if html and not _is_cf_blocked(html):
            log.info("[discovery] warm-up OK: %s", root)
            ctx.consecutive_blocks = 0
        else:
            log.info("[discovery] warm-up inconclusive: %s", root)
    except Exception as e:
        log.debug("[discovery] warm-up error: %s", e)


def _is_listing_variant(href: str, listing_url: str) -> bool:
    """True nếu href trỏ về CHÍNH trang listing (chỉ khác page=/sort=) — tức link
    điều hướng phân trang, KHÔNG phải trang review. Việc đi trang do
    _generate_pagination_urls đảm nhiệm; nếu để chúng lọt vào danh sách review thì
    sẽ (1) tốn lượt fetch cho trang không được yêu cầu và (2) thêm chính các trang
    10/11 vào ctx.visited → khiến bộ đi-trang bỏ qua chúng."""
    try:
        a, b = urlparse(href), urlparse(listing_url)
    except Exception:
        return False
    return a.netloc == b.netloc and a.path.rstrip("/") == b.path.rstrip("/")


# Segment cha cho biết NGUỒN là trang danh mục (không phải trang chi tiết). Chỉ khi nguồn
# là danh mục thì link ngang hàng mới là "danh mục khác" cần bỏ; nếu nguồn là trang chi tiết
# (vd alternativeto /software/<slug>) thì link ngang hàng CHÍNH LÀ item cần lấy.
_LISTING_SEGMENTS = {"list", "lists", "collection", "collections", "browse", "directory", "best", "top"}


def _is_sibling_listing(href: str, source_url: str) -> bool:
    """True nếu href là trang danh mục NGANG HÀNG với nguồn (cùng netloc, cùng path cha,
    cùng độ sâu, khác lá) — vd nguồn /list/text-analysis, href /list/seo-software. Đây là
    danh mục KHÁC (niche khác), KHÔNG phải trang chi tiết của nguồn → bỏ qua để (1) khỏi phí
    fetch cả trăm link sidebar (ăn hết ngân sách, không tới được trang 2-3) và (2) tránh lẫn
    dự án của niche khác.

    CHỈ áp dụng khi NGUỒN là trang danh mục (segment cha ∈ _LISTING_SEGMENTS). Nếu nguồn là
    trang chi tiết mà các item cùng pattern (vd alternativeto /software/softr → /software/grist)
    thì sibling chính là item cần lấy → KHÔNG bỏ."""
    try:
        a, b = urlparse(href), urlparse(source_url)
    except Exception:
        return False
    if a.netloc != b.netloc:
        return False
    pa = [s for s in a.path.split("/") if s]
    pb = [s for s in b.path.split("/") if s]
    # Nguồn phải là trang DANH MỤC thì link ngang hàng mới là "danh mục khác" cần bỏ:
    #   • /list/<niche>, /collection/<x>…  → segment cha ∈ _LISTING_SEGMENTS (saasworthy)
    #   • /best-proxy-software, /top-10-…   → lá bắt đầu best-/top- (saashub)
    # Nguồn là trang chi tiết (alternativeto /software/softr) → KHÔNG bỏ sibling.
    parent_is_listing = len(pb) >= 2 and pb[-2].lower() in _LISTING_SEGMENTS
    leaf_is_listing = bool(pb) and re.match(r'(best|top)[\-_]', pb[-1].lower())
    if not (parent_is_listing or leaf_is_listing):
        return False
    return len(pa) == len(pb) and pa[:-1] == pb[:-1] and pa[-1:] != pb[-1:]


async def _process_review_batch(ctx: _CrawlCtx, links: list[str], depth: int) -> int:
    tasks = [_process_inner(ctx, u, depth=depth) for u in links]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    total = 0
    for r in results:
        if isinstance(r, int):
            total += r
        elif isinstance(r, Exception):
            log.debug("[discovery] inner task error: %s", r)
    return total


async def _scan_listing_links(ctx: _CrawlCtx, page_url: str, html: str) -> tuple[int, list[str], int]:
    """Quét 1 trang listing: lưu MỌI link outbound trực tiếp (level=1). Trả về
    (số candidate MỚI, list link review/detail chưa thăm — đã dedupe, SỐ link outbound
    trực tiếp CÓ TRÊN TRANG). direct_found tính cả link đã có trong DB (dùng cho quyết
    định bỏ qua trang chi tiết — để lần quét lại vẫn nhận ra trang đã có link trực tiếp)."""
    html = _rewrite_redirect_hrefs(html, page_url)   # link chuyển hướng nội bộ → domain thật
    all_links = _extract_all_links(html, page_url)
    internal_candidates: list[str] = []
    new_count = 0
    direct_found = 0
    for link in all_links:
        href = link["href"]
        link_domain = _get_root_domain(href)
        if not link_domain:
            continue
        if link_domain != ctx.source_domain:
            # Direct outbound (level=1)
            is_bl = any(link_domain == b or link_domain.endswith("." + b) for b in ctx.blacklist)
            if not is_bl:
                direct_found += 1
                saved = await _save_candidate_if_new(
                    source_id=ctx.source_id, raw_url=href, domain=link_domain,
                    level=1, depth=1, detection_method="direct_outbound",
                    suggested_name=link["text"], source_page_url=page_url,
                    source_page_title=None, user_id=ctx.user_id,
                )
                if saved:
                    new_count += 1
            continue
        # Internal link — bỏ link phân trang/sort của chính trang listing (do
        # _generate_pagination_urls xử lý riêng) và link danh mục NGANG HÀNG (sidebar sang
        # niche khác — vd saasworthy /list/seo-software khi nguồn là /list/text-analysis).
        if (not is_junk_link(href, page_url, section_anchor=ctx.source_root_url)
                and not _is_listing_variant(href, page_url)
                and not _is_sibling_listing(href, ctx.source_root_url)
                and href not in ctx.visited):
            internal_candidates.append(href)
    seen: set[str] = set()
    review_links: list[str] = []
    for u in internal_candidates:
        if u not in seen:
            seen.add(u)
            review_links.append(u)
    return new_count, review_links, direct_found


async def _follow_reviews_if_sparse(ctx: _CrawlCtx, page_url: str, direct_count: int,
                                    review_links: list[str]) -> int:
    """Đi vào các trang review/detail CHỈ KHI link outbound trực tiếp phủ ít (<ngưỡng)
    so với số link review — tức sản phẩm nằm sau trang chi tiết (forexpeacearmy/
    aixploria). Directory kiểu toolify (mỗi thẻ đã có link domain thật, direct ≥ ~review)
    → BỎ QUA trang chi tiết → tiết kiệm ~20 request/trang (tránh Cloudflare chặn IP vì
    quá nhiều request). Trả về số candidate mới thu thêm từ các trang review."""
    if not review_links:
        return 0
    # alternativeto: link 'direct' trên listing chỉ là footer/social/ads (rác); website
    # sản phẩm nằm sau /software/<slug>/about/ → LUÔN vào trang chi tiết (bỏ qua ngưỡng phủ).
    force_reviews = "alternativeto.net" in page_url
    if force_reviews or direct_count < LISTING_DIRECT_COVER_RATIO * len(review_links):
        log.info("[discovery] %s: %d direct < %.0f%% of %d review → đi vào trang chi tiết",
                 page_url, direct_count, LISTING_DIRECT_COVER_RATIO * 100, len(review_links))
        return await _process_review_batch(ctx, review_links, depth=MAX_DEPTH)
    log.info("[discovery] %s: %d direct phủ ≥%.0f%% of %d review → bỏ qua trang chi tiết (nhanh)",
             page_url, direct_count, LISTING_DIRECT_COVER_RATIO * 100, len(review_links))
    return 0


async def _maybe_headed_rescan(
    ctx: _CrawlCtx, page_url: str, html: str, new_count: int,
    review_links: list[str], direct_found: int,
) -> tuple[str, int, list[str], int]:
    """Nếu trang listing render (headless) ra QUÁ ÍT item (site trả trang rỗng lưới cho
    bot, vd toolify /category/) → thử lại bằng trình duyệt HEADED và dùng kết quả tốt hơn.
    Trả (html, new_count, review_links, direct_found) đã cập nhật. An toàn khi quét lại:
    _scan_listing_links dedupe theo (user, domain) nên không đếm/lưu trùng."""
    if direct_found + len(review_links) >= LISTING_MIN_ITEMS:
        return html, new_count, review_links, direct_found
    headed_html = await ctx._fetch_headed(page_url, scroll=True)
    if not headed_html:
        return html, new_count, review_links, direct_found
    n2, rl2, df2 = await _scan_listing_links(ctx, page_url, headed_html)
    if df2 + len(rl2) <= direct_found + len(review_links):
        return html, new_count, review_links, direct_found   # headed không tốt hơn → giữ nguyên
    log.info("[discovery] headed fallback CỨU trang rỗng lưới: %s → %d direct + %d review "
             "(headless chỉ %d direct + %d review)",
             page_url, df2, len(rl2), direct_found, len(review_links))
    return headed_html, new_count + n2, rl2, df2


async def _crawl_listing_page(ctx: _CrawlCtx, page_url: str) -> int:
    """Xử lý 1 trang phân trang NHƯ trang listing (không phải trang review).

    Lấy hết outbound trực tiếp; CHỈ đi vào từng trang review/detail khi các link
    trực tiếp CHƯA phủ hết trang (direct < review) — tức sản phẩm nằm sau trang chi
    tiết (vd forexpeacearmy/aixploria). Directory kiểu toolify (mỗi thẻ tool đã có
    link ra domain thật) → direct ≥ review → BỎ QUA trang /tool/ → nhanh như /new
    (trước đây mỗi trang phân trang phải mở thêm ~20 trang /tool/)."""
    if page_url in ctx.visited or ctx.at_page_limit:
        return 0
    ctx.visited.add(page_url)
    html = await ctx.fetch(page_url, scroll=True)
    if not html:
        return 0
    if not ctx.use_browser and _is_cf_blocked(html):
        ctx.use_browser = True
        html = await ctx._fetch_nodriver(page_url, scroll=True)
        if not html:
            return 0
    new_count, review_links, direct_found = await _scan_listing_links(ctx, page_url, html)
    html, new_count, review_links, direct_found = await _maybe_headed_rescan(
        ctx, page_url, html, new_count, review_links, direct_found)
    new_count += await _follow_reviews_if_sparse(ctx, page_url, direct_found, review_links)
    return new_count


async def _crawl_source_page(ctx: _CrawlCtx, page_url: str) -> int:
    ctx.visited.add(page_url)

    html = await ctx.fetch(page_url, scroll=True)
    if not html:
        log.warning("[discovery] source page unreachable: %s | %s", page_url, _diag(ctx))
        raise CrawlBlockedError(_source_unreachable_reason(ctx))

    # CF detection on source page → browser mode for whole session
    if not ctx.use_browser and _is_cf_blocked(html):
        log.info("[discovery] CF on source → full browser mode for %s", page_url)
        html = await ctx._fetch_nodriver(page_url, scroll=True)
        if not html:
            return 0
        ctx.use_browser = True

    log.info("[discovery] source page: %d raw links | %s",
             len(_extract_all_links(html, page_url)), page_url)
    new_count, review_links, direct_found = await _scan_listing_links(ctx, page_url, html)
    # Site trả trang rỗng lưới cho headless (toolify /category/) → thử lại bằng headed.
    html, new_count, review_links, direct_found = await _maybe_headed_rescan(
        ctx, page_url, html, new_count, review_links, direct_found)

    # ── Incremental mode: sequential pages, stop at first all-known page ──
    if ctx.incremental:
        # Chỉ đi vào trang chi tiết khi link trực tiếp phủ ít (forexpeacearmy…).
        # Directory kiểu toolify → bỏ qua /tool/ → ít request → không bị CF chặn IP.
        new_count += await _follow_reviews_if_sparse(ctx, page_url, direct_found, review_links)
        new_count += await _crawl_pagination_incremental(ctx, page_url, html)
        return new_count

    # ── Full mode: trang nguồn (page 1) + toàn bộ trang phân trang ──
    pagination_urls = _generate_pagination_urls(page_url, html, max_pages=ctx.max_listing_pages)
    pagination_urls = [pu for pu in pagination_urls if pu not in ctx.visited]

    log.info("[discovery] direct_outbound=%d | page1_reviews=%d | pagination_pages=%d",
             new_count, len(review_links), len(pagination_urls))

    # Trang nguồn (page 1): đi vào trang chi tiết CHỈ KHI link trực tiếp phủ ít (an toàn
    # cho forexpeacearmy/aixploria). Toolify (mỗi thẻ đã có link domain) → bỏ qua /tool/.
    new_count += await _follow_reviews_if_sparse(ctx, page_url, direct_found, review_links)
    # Trang phân trang: xử lý NHƯ trang listing (lấy hết outbound trực tiếp; chỉ mở
    # trang chi tiết khi direct thưa). Đây là chỗ tiết kiệm lớn cho toolify.
    for purl in pagination_urls:
        if ctx.at_page_limit:
            break
        new_count += await _crawl_listing_page(ctx, purl)
    return new_count


async def _page_already_crawled(source_id: int, page_url: str) -> bool:
    """True nếu trang listing này ĐÃ từng sinh candidate cho source ở lần quét trước.
    Kiểm tra theo source_page_url + source_id (KHÔNG theo domain) nên KHÔNG bị nhầm bởi
    dedup domain toàn cục, cũng KHÔNG bị nhầm khi trang bị chặn/rỗng (không lưu gì →
    coi như chưa quét → không dừng nhầm)."""
    async with SessionLocal() as s:
        row = (await s.execute(
            select(DiscoveryCandidate.id).where(
                DiscoveryCandidate.source_id == source_id,
                DiscoveryCandidate.source_page_url == page_url,
            ).limit(1)
        )).scalar_one_or_none()
    return row is not None


async def _crawl_pagination_incremental(ctx: _CrawlCtx, page1_url: str, page1_html: str) -> int:
    """Đi tuần tự các trang phân trang (mới→cũ). DỪNG khi gặp trang ĐÃ quét ở lần trước
    (đã sinh candidate cho source này) — phần còn lại là cũ/đã biết.

    Dùng chung `_crawl_listing_page` với chế độ full: lấy hết outbound trực tiếp và chỉ
    mở trang chi tiết khi link trực tiếp thưa (toolify → bỏ qua /tool/ → ít request,
    tránh Cloudflare chặn IP). Điều kiện dừng dựa trên source_page_url (không dựa trên
    '0 candidate mới') để KHÔNG dừng nhầm khi trang bị Cloudflare chặn hoặc khi domain
    đã tồn tại từ nguồn khác (dedup toàn cục)."""
    pages = _generate_pagination_urls(page1_url, page1_html, max_pages=ctx.max_listing_pages)
    if not pages:
        return 0
    log.info("[discovery] incremental: tối đa %d trang phân trang, dừng khi gặp trang đã quét lần trước", len(pages))
    new_count = 0
    for purl in pages:
        if ctx.at_page_limit:
            break
        if await _page_already_crawled(ctx.source_id, purl):
            log.info("[discovery] incremental STOP — %s đã quét lần trước, dừng quét thêm", purl)
            break
        new_count += await _crawl_listing_page(ctx, purl)
    return new_count


# ─── Public entry point ───────────────────────────────────────────────────────

async def crawl_discovery_source(
    source_id: int,
    proxy_urls: list[str] | None = None,
    proxy_labels: list[str] | None = None,
    max_listing_pages: int | None = None,
    incremental: bool = False,
) -> int:
    """Entry point. Returns count of new candidates discovered.

    proxy_urls: pool of 'scheme://[user:pass@]host:port' — requests round-robin
                across them (≈ rotating proxy). Empty/None = no proxy.
    max_listing_pages: cap on paginated listing pages (None = crawl all).
    incremental: only crawl newest-first until a listing page has no unseen
                 brokers, then stop (fast periodic re-scan).
    """
    proxy_urls = [u for u in (proxy_urls or []) if u]
    proxy_labels = proxy_labels or []

    async with SessionLocal() as s:
        source = (await s.execute(
            select(DiscoverySource).where(DiscoverySource.id == source_id)
        )).scalar_one_or_none()
        if not source:
            raise ValueError(f"DiscoverySource #{source_id} not found")

    blacklist = await _get_blacklist()

    # Safety cap on TOTAL fetches: ~55 review pages per listing page + slack.
    # Keeps "N trang" honest even if some pages fan out more than expected.
    if max_listing_pages and max_listing_pages > 0:
        total_cap = min(MAX_TOTAL_PAGES, max_listing_pages * 55 + 20)
    else:
        total_cap = MAX_TOTAL_PAGES

    ctx = _CrawlCtx(
        source_id=source_id,
        source_root_url=source.url,
        blacklist=blacklist,
        user_id=source.user_id,
        proxy_urls=proxy_urls,
        proxy_labels=proxy_labels,
        max_listing_pages=max_listing_pages,
        max_total_pages=total_cap,
        incremental=incremental,
    )

    # Quét nguồn ĐÚNG URL user nhập — KHÔNG nhồi thêm tham số sort.
    # Trước đây chế độ incremental tự thêm `sort=-updated_at` để item mới lên đầu.
    # `-updated_at` là quy ước của MỘT họ site, không phải chuẩn chung, và đo được nó
    # PHÁ chính các nguồn đang dùng:
    #   • toolify.ai/category/…?sort=-updated_at        → HTTP 400 Bad Request
    #   • topai.tools/category/…?sort=-updated_at       → listing rỗng (235KB → 15KB,
    #                                                     131 anchor → 23, 0 outbound)
    # Bỏ nó KHÔNG ảnh hưởng tính đúng của incremental: điều kiện dừng là
    # `_page_already_crawled(source_page_url)` — không phụ thuộc thứ tự sort. Sort chỉ
    # là tối ưu (gặp trang đã biết sớm hơn), và không đáng để đánh đổi bằng nguồn vỡ.
    start_url = source.url

    log.info("[discovery] START #%d (%s): %s",
             source_id, "incremental" if incremental else "full", start_url)
    stopped_by_user = False
    try:
        # Warm-up: visit the site root first so the browser picks up cookies /
        # clears any first-visit bot check, making the real source request look
        # like normal continued browsing (less likely to be rate-limited).
        await _warmup(ctx, source.url)
        new_count = await _crawl_source_page(ctx, start_url)
    except CrawlBlockedError:
        # Source unreachable etc. — but if the user asked to stop, treat as stop.
        if ctx.should_stop:
            new_count = 0
            stopped_by_user = True
        else:
            raise
    finally:
        await ctx.stop()

    if ctx.should_stop:
        stopped_by_user = True
    _STOP_REQUESTED.discard(source_id)

    async with SessionLocal() as s:
        src = (await s.execute(
            select(DiscoverySource).where(DiscoverySource.id == source_id)
        )).scalar_one_or_none()
        if src:
            src.last_crawled_at = datetime.utcnow()
            src.total_candidates_found = (src.total_candidates_found or 0) + new_count
            await s.commit()

    if stopped_by_user:
        log.info("[discovery] STOPPED by user #%d → đã lưu %d dự án (pages=%d)",
                 source_id, new_count, ctx.pages_fetched)
        return new_count

    # If we bailed out early due to relentless rate-limiting, surface it so the
    # job is marked failed (with the partial count already persisted).
    if ctx.aborted:
        rate = ctx.browser_failures / max(ctx.browser_attempts, 1) * 100
        if ctx.proxy_urls:
            proxy_hint = (
                f"Cả pool {len(ctx.proxy_urls)} proxy đều bị chặn — proxy có thể đã bị "
                "đánh dấu sẵn; thử proxy mới/xoay (rotating residential)."
            )
        else:
            proxy_hint = "Chạy không proxy — thêm pool proxy, hoặc thử lại lúc IP nguội."
        raise CrawlBlockedError(
            f"Crawl dừng sớm: {ctx.browser_failures}/{ctx.browser_attempts} trang bị chặn "
            f"({rate:.0f}%), đã lưu {new_count} dự án. {proxy_hint} [{_diag(ctx)}]"
        )

    log.info("[discovery] DONE #%d → %d new candidates (pages=%d)",
             source_id, new_count, ctx.pages_fetched)
    return new_count
