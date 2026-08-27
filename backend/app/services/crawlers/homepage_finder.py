"""Homepage discovery cho affiliate programs có intermediate URL.

Dành riêng cho Lovable source — các platform như Rewardful, Tolt, Everflow
lưu URL dạng `{slug}.getrewardful.com`, `{slug}.tolt.io`, v.v.
thay vì trang chủ thực sự của công ty.

Chiến lược: Browser Google search (CloakBrowser) — giải captcha tự động
nếu gặp (CapSolver). Chỉ dùng Google, không Bing.

Traffic scanner (SimilarWeb) phải dùng domain trang chủ, không dùng
affiliate subdomain.
"""
from __future__ import annotations

import asyncio
import base64
import random
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.logger import get_logger
from app.services.captcha.capsolver import (
    CapSolver,
    detect_captcha_on_page,
    inject_recaptcha_token,
    inject_turnstile_token,
)

log = get_logger("crawler.homepage_finder")

# Các platform affiliate có subdomain dạng {slug}.{platform}
_AFFILIATE_PLATFORMS = (
    ".getrewardful.com",
    ".tolt.io",
    ".everflowclient.io",
    ".tapfiliate.com",
    ".firstpromoter.com",
    ".leaddyno.com",
    ".promotekit.com",
    ".tapfiliate.com",
    ".partnerstack.com",
    ".partnercentric.com",
    ".osomfinance.com",
    ".growthhero.io",
)

# TLDs để thử theo độ ưu tiên
_PROBE_TLDS = (".com", ".io", ".ai", ".co", ".app", ".net", ".org")

_HTTP_TIMEOUT = 8.0
_PROBE_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _PROBE_SEMAPHORE
    if _PROBE_SEMAPHORE is None:
        _PROBE_SEMAPHORE = asyncio.Semaphore(8)  # max 8 concurrent probes
    return _PROBE_SEMAPHORE


async def _warmup_google(page) -> None:
    """Visit google.com home để set session cookies trước khi search.

    Google nhận ra "regular user" khi có session/cookies từ home page.
    Giảm khả năng bị flag bot ngay từ search đầu tiên.
    """
    try:
        current_url = page.url or ""
        if "google.com" in current_url and "sorry" not in current_url:
            return  # Đã ở trên Google, bỏ qua
        log.info("Google warmup: navigating to google.com home")
        await page.goto("https://www.google.com/", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(random.uniform(1.5, 2.5))
        # Chấp nhận cookie consent (EU/GDPR popup) nếu có
        for sel in ['button#L2AGLb', '[aria-label="Accept all"]', 'button:has-text("Accept all")', '[data-ved] button:last-child']:
            try:
                await page.click(sel, timeout=1500)
                await asyncio.sleep(0.8)
                log.debug("Google warmup: accepted consent via %s", sel)
                break
            except Exception:
                pass
        await asyncio.sleep(random.uniform(0.8, 1.5))
        log.info("Google warmup complete")
    except Exception as e:
        log.debug("Google warmup non-fatal error: %s", e)


async def _search_google_via_form(page, query: str) -> bool:
    """Gõ query vào ô tìm kiếm Google (human-like, tránh bot detection).

    Chỉ dùng khi page đang ở google.com. Trả True nếu submit thành công.
    """
    try:
        for sel in ['textarea[name="q"]', 'input[name="q"]']:
            try:
                elem = await page.query_selector(sel)
                if not elem:
                    continue
                await elem.click()
                await asyncio.sleep(random.uniform(0.2, 0.5))
                # Xóa nội dung cũ nếu có
                await page.keyboard.press("Control+a")
                await asyncio.sleep(0.1)
                # Gõ từng ký tự (số ms ngẫu nhiên giống người thật)
                await page.type(sel, query, delay=random.randint(45, 110))
                await asyncio.sleep(random.uniform(0.4, 0.9))
                await page.keyboard.press("Enter")
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
                return True
            except Exception:
                continue
        return False
    except Exception:
        return False


def is_intermediate_url(url: str) -> bool:
    """True nếu URL là subdomain affiliate platform, không phải trang chủ thực."""
    if not url:
        return False
    try:
        host = urlparse(url if "://" in url else f"https://{url}").netloc or url
        host = host.lower()
        return any(host.endswith(p) for p in _AFFILIATE_PLATFORMS)
    except Exception:
        return False


def extract_brand_slug(affiliate_url: str) -> str:
    """Lấy brand slug từ affiliate subdomain URL.

    heygen.getrewardful.com  → "heygen"
    opus-clip.getrewardful.com → "opusclip"
    loki-build.getrewardful.com → "lokibuild"
    beehiiv.getrewardful.com → "beehiiv"
    """
    if not affiliate_url:
        return ""
    try:
        host = urlparse(affiliate_url if "://" in affiliate_url else f"https://{affiliate_url}").netloc
        host = host.lower()
        for platform in _AFFILIATE_PLATFORMS:
            if host.endswith(platform):
                slug = host[: -len(platform)]
                # Normalise: remove hyphens to get clean brand slug
                return slug.replace("-", "")
        # Fallback: bare subdomain
        return host.split(".")[0].replace("-", "")
    except Exception:
        return ""


async def _probe_url(url: str) -> bool:
    """HEAD request, True nếu 2xx hoặc 3xx (redirect = trang tồn tại)."""
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AffiliateHubBot/1.0)"},
        ) as client:
            r = await client.head(url)
            return r.status_code < 500
    except Exception:
        return False


async def find_homepage_by_probe(brand_slug: str) -> Optional[str]:
    """Thử {brand}.com, {brand}.io, v.v. bằng HTTP HEAD.

    Trả URL đầu tiên respond được (< 500 HTTP status).
    """
    if not brand_slug or len(brand_slug) < 2:
        return None
    sem = _get_semaphore()

    async def _check(tld: str) -> Optional[str]:
        url = f"https://{brand_slug}{tld}"
        async with sem:
            ok = await _probe_url(url)
        return url if ok else None

    results = await asyncio.gather(*(_check(t) for t in _PROBE_TLDS), return_exceptions=True)
    for r in results:
        if isinstance(r, str):
            log.debug("Homepage probe hit: %s", r)
            return r
    return None


# Domains to skip in search results (directories, social, etc.)
_SKIP_DOMAINS = frozenset({
    "capterra", "g2.com", "trustpilot", "linkedin", "twitter",
    "facebook", "reddit", "getrewardful", "tolt.io", "crunchbase",
    "alternativeto", "producthunt", "youtube", "wikipedia",
    "tapfiliate", "firstpromoter", "leaddyno", "promotekit",
    "partnerstack", "everflowclient",
})


async def _extract_links_from_page(page, engine: str = "google") -> list:
    """Trích các href từ kết quả tìm kiếm Google trên trang đang hiển thị."""
    return await page.evaluate("""
        () => {
            const found = [];
            // Google modern: jsname="UWckNb"
            document.querySelectorAll('a[jsname="UWckNb"]').forEach(a => {
                if (a.href && a.href.startsWith('https://') && !a.href.includes('google.com'))
                    found.push(a.href);
            });
            // Fallback: #search container
            if (!found.length) {
                document.querySelectorAll('#search a[href^="https://"]').forEach(a => {
                    const h = a.href;
                    if (!h.includes('google.com') && !h.includes('webcache'))
                        found.push(h);
                });
            }
            return [...new Set(found)].slice(0, 10);
        }
    """) or []


async def _click_verify(bframe) -> None:
    """Click nút Verify trong reCAPTCHA bframe."""
    try:
        vbtn = await bframe.query_selector("#recaptcha-verify-button")
        if vbtn:
            await vbtn.click()
    except Exception:
        pass


async def _reload_challenge(bframe) -> bool:
    """Click nút reload (↻) để lấy challenge mới. Trả True nếu click được."""
    try:
        rbtn = await bframe.query_selector("#recaptcha-reload-button")
        if rbtn:
            await rbtn.click()
            return True
    except Exception:
        pass
    return False


async def _solve_captcha_on_search_page(page, engine: str, proxy_url: str = "") -> bool:
    """Detect + giải captcha trên trang Google. Trả True nếu solved, False nếu skip."""
    # Dùng cùng proxy với browser để CapSolver giải trên cùng IP → tăng tỷ lệ pass
    effective_proxy = proxy_url or ""
    capsolver = CapSolver(api_key=settings.capsolver_api_key or "", proxy_url=effective_proxy or None)
    if not capsolver.enabled:
        return False
    try:
        info = await detect_captcha_on_page(page)
        if not info:
            return False
        ctype = info.get("type", "")
        sk = info.get("sitekey", "")
        page_url = page.url
        log.info("Homepage search: captcha=%s on %s", ctype, engine)

        if ctype in ("recaptcha_v2", "recaptcha_v2_enterprise"):
            if ctype == "recaptcha_v2_enterprise":
                token = await capsolver.solve_recaptcha_v2_enterprise(page_url, sk)
            else:
                token = await capsolver.solve_recaptcha_v2(page_url, sk, invisible=bool(info.get("invisible")))
            await inject_recaptcha_token(page, token)
            await asyncio.sleep(2.0)
            return True

        if ctype in ("recaptcha_v3", "recaptcha_v3_enterprise"):
            if ctype == "recaptcha_v3_enterprise":
                token = await capsolver.solve_recaptcha_v3_enterprise(page_url, sk, action="search")
            else:
                token = await capsolver.solve_recaptcha_v3(page_url, sk, action="search")
            await inject_recaptcha_token(page, token)
            await asyncio.sleep(2.0)
            return True

        if ctype == "turnstile":
            res = await capsolver.solve_turnstile(page_url, sk, info.get("action") or "", info.get("cdata") or "")
            await inject_turnstile_token(page, res["token"])
            await asyncio.sleep(2.0)
            return True

        if ctype == "recaptcha_v2_image_challenge":
            # Google triggered an image grid challenge (xe/đèn giao thông...)
            # Dùng ReCaptchaV2Classification (sync, không cần poll) để giải.
            # page ở đây là raw Playwright page — dùng page.frames trực tiếp.
            _QMAP = {
                "cars": "/m/0k4j", "car": "/m/0k4j",
                "traffic lights": "/m/015qff", "traffic light": "/m/015qff",
                "crosswalks": "/m/014xcs", "crosswalk": "/m/014xcs",
                "bicycles": "/m/0199g", "bicycle": "/m/0199g",
                "parking meters": "/m/015qbp", "parking meter": "/m/015qbp",
                "bridges": "/m/015kr", "bridge": "/m/015kr",
                "boats": "/m/019jd", "boat": "/m/019jd",
                "taxis": "/m/0pg52", "taxi": "/m/0pg52",
                "buses": "/m/01bjv", "bus": "/m/01bjv",
                "school bus": "/m/02yvhj",
                "motorcycles": "/m/04_sv", "motorcycle": "/m/04_sv",
                "tractors": "/m/013xlm", "tractor": "/m/013xlm",
                "fire hydrants": "/m/01pns0", "fire hydrant": "/m/01pns0",
                "chimneys": "/m/01jk_4", "chimney": "/m/01jk_4",
                "stairs": "/m/01lynh", "staircase": "/m/01lynh",
                "mountains or hills": "/m/09d_r", "mountains": "/m/09d_r",
                "palm trees": "/m/0cdl1", "palm tree": "/m/0cdl1",
                "street signs": "/m/01mr2g", "street sign": "/m/01mr2g",
            }
            try:
                # Tìm bframe trong page.frames (Playwright native — cross-origin OK)
                bframe = next(
                    (f for f in page.frames if "bframe" in (f.url or "")), None
                )
                log.debug("Homepage search: frames=%s", [f.url for f in page.frames])
                if not bframe:
                    log.warning("Homepage search: bframe không tìm thấy — skip engine")
                    return False
                log.info("Homepage search: bframe.url=%s", bframe.url[:80])

                # Nếu challenge chưa mở (bframe đang ở trạng thái ban đầu), click checkbox trước
                try:
                    anchor = next(
                        (f for f in page.frames if "/recaptcha/" in (f.url or "") and "anchor" in (f.url or "")),
                        None,
                    )
                    if anchor:
                        checkbox = await anchor.query_selector("#recaptcha-anchor, .recaptcha-checkbox")
                        if checkbox:
                            log.info("Homepage search: clicking reCAPTCHA checkbox in anchor iframe")
                            await checkbox.click()
                            await asyncio.sleep(2.5)
                            # bframe có thể thay đổi URL sau khi challenge mở
                            bframe = next(
                                (f for f in page.frames if "bframe" in (f.url or "")), None
                            )
                            if bframe:
                                log.info("Homepage search: bframe after click=%s", bframe.url[:80])
                except Exception as _ae:
                    log.debug("Homepage search: anchor click error: %s", _ae)

                if not bframe:
                    log.warning("Homepage search: bframe mất sau khi click — skip engine")
                    return False

                # Wait for grid AND question element
                try:
                    await bframe.wait_for_selector(
                        "#rc-imageselect-target table, #rc-imageselect",
                        timeout=8000,
                    )
                except Exception:
                    pass

                # Wait specifically for the question strong tag (up to 6s)
                try:
                    await bframe.wait_for_selector(
                        "#rc-imageselect-desc-no-canonical strong, "
                        "#rc-imageselect-desc strong, "
                        ".rc-imageselect-desc-wrapper strong, "
                        ".rc-imageselect-desc strong",
                        timeout=6000,
                    )
                except Exception:
                    pass

                # Debug: dump bframe text to understand structure
                try:
                    _bf_text = await bframe.evaluate(
                        "() => document.body?.innerText?.slice(0, 300) || ''"
                    )
                    log.info("Homepage search: bframe body text: %s", repr(_bf_text))
                except Exception:
                    pass

                question_text = ""
                try:
                    question_text = await bframe.evaluate("""
                        () => {
                            // Try narrow strong-tag selectors first
                            const strongs = [
                                document.querySelector('#rc-imageselect-desc-no-canonical strong'),
                                document.querySelector('#rc-imageselect-desc strong'),
                                document.querySelector('.rc-imageselect-desc-wrapper strong'),
                                document.querySelector('.rc-imageselect-desc strong'),
                            ];
                            for (const el of strongs) { if (el) return el.innerText.trim().toLowerCase(); }
                            // Fallback: full paragraph text, extract word after "with"
                            const desc = document.querySelector('#rc-imageselect-desc-no-canonical') ||
                                         document.querySelector('#rc-imageselect-desc') ||
                                         document.querySelector('.rc-imageselect-desc-wrapper') ||
                                         document.querySelector('.rc-imageselect-desc');
                            if (!desc) return '';
                            const full = desc.innerText.trim().toLowerCase();
                            const m = full.match(/\\bwith\\s+([a-z][a-z\\s]+?)(?:\\s*\\.)?$/);
                            return m ? m[1].trim() : full;
                        }
                    """) or ""
                except Exception:
                    pass

                # Extract sitekey từ bframe URL nếu chưa có
                _bsk = sk
                if not _bsk:
                    _bm = re.search(r"[?&]k=([A-Za-z0-9_-]+)", bframe.url or "")
                    if _bm:
                        _bsk = _bm.group(1)

                _Q_JS = """
                    () => {
                        const strongs = [
                            document.querySelector('#rc-imageselect-desc-no-canonical strong'),
                            document.querySelector('#rc-imageselect-desc strong'),
                            document.querySelector('.rc-imageselect-desc-wrapper strong'),
                            document.querySelector('.rc-imageselect-desc strong'),
                        ];
                        for (const el of strongs) { if (el) return el.innerText.trim().toLowerCase(); }
                        const desc = document.querySelector('#rc-imageselect-desc-no-canonical') ||
                                     document.querySelector('#rc-imageselect-desc') ||
                                     document.querySelector('.rc-imageselect-desc-wrapper') ||
                                     document.querySelector('.rc-imageselect-desc');
                        if (!desc) return '';
                        const full = desc.innerText.trim().toLowerCase();
                        const m = full.match(/\\bwith\\s+([a-z][a-z\\s]+?)(?:\\s*\\.)?$/);
                        return m ? m[1].trim() : full;
                    }
                """

                async def _captcha_gone() -> bool:
                    """True nếu captcha không còn trên page chính (đã pass)."""
                    try:
                        re_info = await detect_captcha_on_page(page)
                        return not re_info
                    except Exception:
                        return False

                MAX_ROUNDS = 10
                _round = 0
                while _round < MAX_ROUNDS:
                    _round += 1

                    # Re-extract question mỗi vòng (challenge động có thể đổi câu hỏi)
                    try:
                        question_text = (await bframe.evaluate(_Q_JS)) or question_text
                    except Exception:
                        pass
                    question_label = _QMAP.get(question_text, question_text)

                    # Screenshot grid
                    try:
                        grid_elem = await bframe.query_selector(
                            "#rc-imageselect-target table, table.rc-imageselect-table"
                        )
                        screenshot_bytes = (
                            await grid_elem.screenshot() if grid_elem
                            else await page.screenshot()
                        )
                        b64_img = base64.b64encode(screenshot_bytes).decode()
                    except Exception as _se:
                        log.warning("Homepage search: grid screenshot round %d failed: %s", _round, _se)
                        break

                    # ReCaptchaV2Classification — sync, returns immediately
                    # Chỉ gọi API khi question_label là Google label code (/m/...)
                    # Nếu không nhận ra câu hỏi → reload challenge thay vì gọi API với label rỗng
                    if not question_label or not question_label.startswith("/m/"):
                        log.info(
                            "Homepage search: round %d unknown question %r → reload challenge",
                            _round, question_label,
                        )
                        if not await _reload_challenge(bframe):
                            break
                        await asyncio.sleep(1.5)
                        continue
                    try:
                        sol = await capsolver.solve_recaptcha_v2_classification(
                            image_base64=b64_img,
                            question=question_label,
                            website_url=page_url,
                            website_key=_bsk,
                        )
                    except Exception as _ce:
                        log.warning("Homepage search: classification round %d failed: %s", _round, _ce)
                        break

                    objects = sol.get("objects") or []
                    sol_type = sol.get("type") or "multi"
                    has_obj = sol.get("hasObject")
                    log.info(
                        "Homepage search: image round %d q=%r type=%s objects=%s",
                        _round, question_label, sol_type, objects,
                    )

                    # --- SINGLE (1 ảnh lớn, hỏi có/không) ---
                    if sol_type == "single":
                        if has_obj:
                            try:
                                main_img = await bframe.query_selector(
                                    ".rc-image-tile-wrapper img, #rc-imageselect img"
                                )
                                if main_img:
                                    await main_img.click()
                                    await asyncio.sleep(0.5)
                            except Exception:
                                pass
                        await _click_verify(bframe)
                        await asyncio.sleep(2.0)
                        if await _captcha_gone():
                            log.info("Homepage search: image challenge SOLVED (single)")
                            return True
                        continue

                    # --- MULTI rỗng: CapSolver không thấy object ---
                    if not objects:
                        # Reload challenge để lấy ảnh mới thay vì verify rỗng
                        log.info("Homepage search: round %d empty → reload challenge", _round)
                        if not await _reload_challenge(bframe):
                            break
                        await asyncio.sleep(1.5)
                        continue

                    # --- MULTI: click các tile theo index ---
                    tiles = await bframe.query_selector_all("td.rc-imageselect-tile")
                    for idx in objects:
                        if 0 <= idx < len(tiles):
                            try:
                                await tiles[idx].click()
                                await asyncio.sleep(0.35)
                            except Exception:
                                pass

                    await asyncio.sleep(1.0)

                    # Challenge động (4x4 select-more): tile mới xuất hiện → tiếp tục vòng
                    try:
                        is_dynamic = await bframe.evaluate(
                            "() => !!document.querySelector('.rc-imageselect-dynamic-selected')"
                        )
                    except Exception:
                        is_dynamic = False
                    if is_dynamic:
                        await asyncio.sleep(1.5)
                        continue

                    # Verify rồi kiểm tra đã pass chưa
                    await _click_verify(bframe)
                    await asyncio.sleep(2.0)
                    if await _captcha_gone():
                        log.info("Homepage search: image challenge SOLVED (multi)")
                        return True
                    # Chưa pass — challenge mới hiện ra, lặp lại

                log.warning("Homepage search: image challenge không pass sau %d vòng", _round)
                return False

            except Exception as _ie:
                log.warning("Homepage search: image challenge solve failed: %s", _ie)
                return False

        log.warning("Homepage search: unknown captcha type %s on %s — skip engine", ctype, engine)
        return False
    except Exception as e:
        log.warning("Homepage search: captcha solve failed on %s: %s", engine, e)
        return False


async def _google_search_links(
    page,
    q_raw: str,
    engine: str = "google",
    proxy_url: str = "",
    *,
    use_form: bool = False,
    num: int = 5,
) -> list:
    """Chạy 1 truy vấn Google trong CloakBrowser, giải captcha nếu gặp, trả về
    danh sách link kết quả organic. Core dùng chung cho tìm trang chủ và dò
    affiliate (warmup đã thực hiện trước đó nếu chạy batch trên cùng page).

    `use_form`: gõ vào ô search (human-like) thay vì navigate URL thẳng.
    """
    from urllib.parse import quote_plus
    # gl=us → Google xử lý như US user, pws=0 → tắt personalised search
    q = quote_plus(q_raw)
    url = f"https://www.google.com/search?q={q}&num={num}&hl=en&gl=us&pws=0"
    try:
        # Delay tự nhiên hơn trước khi search (giống người đọc kết quả trước)
        await asyncio.sleep(random.uniform(2.0, 4.5))

        if use_form and "google.com" in (page.url or "") and "sorry" not in (page.url or ""):
            # Dùng form search — natural hơn URL navigation
            success = await _search_google_via_form(page, q_raw)
            if not success:
                log.debug("form search failed for %r — fallback to URL", q_raw)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        else:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        await asyncio.sleep(random.uniform(1.5, 2.8))

        # Simulate reading: scroll xuống một chút
        try:
            scroll_px = random.randint(150, 450)
            await page.evaluate(f"window.scrollBy(0, {scroll_px})")
            await asyncio.sleep(random.uniform(0.4, 0.9))
        except Exception:
            pass

        # Google /sorry page (unusual traffic gate) → cần giải captcha trước
        if "google.com/sorry" in (page.url or "") or "google.com/captcha" in (page.url or "").lower():
            log.warning("Google search: /sorry page for %r — solving captcha", q_raw)
            solved = await _solve_captcha_on_search_page(page, engine, proxy_url=proxy_url)
            if not solved:
                log.warning("Google search: /sorry captcha unsolvable for %r", q_raw)
                return []
            await asyncio.sleep(random.uniform(2.5, 4.0))
            # Navigate lại sau khi pass /sorry
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(1.5, 2.5))

        # Kiểm tra + giải captcha inline — retry tối đa 3 lần
        for _attempt in range(3):
            info = await detect_captcha_on_page(page)
            if not info:
                break
            log.info("Google search: captcha attempt %d for %r (type=%s)", _attempt + 1, q_raw, info.get("type"))
            solved = await _solve_captcha_on_search_page(page, engine, proxy_url=proxy_url)
            if not solved:
                log.warning("Google search: unsolvable captcha on %s for %r", engine, q_raw)
                return []
            # Reload trang tìm kiếm sau khi giải captcha
            await asyncio.sleep(random.uniform(2.0, 3.5))
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(1.2, 2.0))
        else:
            # Vẫn bị captcha sau 3 lần → bỏ qua
            if await detect_captcha_on_page(page):
                log.warning("Google search: still captcha after 3 solves for %r", q_raw)
                return []

        return await _extract_links_from_page(page, engine)
    except Exception as e:
        log.warning("Google search failed for %r: %s", q_raw, e)
        return []


async def _search_one_engine(
    page,
    program_name: str,
    engine: str = "google",
    proxy_url: str = "",
    *,
    use_form: bool = False,
) -> Optional[str]:
    """Tìm homepage trên Google (query '{name} official site'). Giải captcha nếu
    gặp. Trả URL trang chủ (bỏ qua directory/social) hoặc None."""
    links = await _google_search_links(
        page, f"{program_name} official site", engine, proxy_url, use_form=use_form, num=5,
    )
    for link in links:
        host = urlparse(link).netloc.lower()
        if not any(s in host for s in _SKIP_DOMAINS):
            homepage = f"https://{host}/"
            log.info("Browser(%s) found: %s → %s", engine, program_name, homepage)
            return homepage
    log.debug("Browser(%s): no result for %r (got %d links)", engine, program_name, len(links))
    return None


async def search_google_links(
    query: str, page=None, proxy_url: str = "", num: int = 8,
) -> list:
    """Public: browser Google search cho `query` bất kỳ → list link kết quả.
    Miễn phí (không SerpAPI); chỉ tốn CapSolver khi gặp captcha. `page` có sẵn
    (batch) → tái dùng (warmup đã làm trước); None → tự mở browser + warmup + đóng.
    """
    if page is not None:
        return await _google_search_links(page, query, "google", proxy_url, use_form=False, num=num)
    from app.services.browser.session import get_browser
    browser = await get_browser(headless=True)  # không proxy — VN proxy chặn Google
    if browser is None:
        log.warning("CloakBrowser không khả dụng — skip google search")
        return []
    pg = await browser.new_page()
    try:
        await _warmup_google(pg)
        return await _google_search_links(pg, query, "google", proxy_url, use_form=True, num=num)
    finally:
        try:
            await pg.close()
        except Exception:
            pass
        try:
            await browser.close()
        except Exception:
            pass



async def find_homepage_by_browser(program_name: str, page=None, proxy_url: str = "") -> Optional[str]:
    """CloakBrowser search Google để tìm trang chủ. Tự động giải captcha.

    Chỉ dùng Google (không Bing). Nếu `page` đã có sẵn (batch mode),
    tái sử dụng page đó thay vì mở mới.
    `proxy_url`: proxy URL cho CapSolver (giải captcha cùng IP). Không dùng cho browser.
    """
    own_browser = False
    own_page = False
    browser = None

    if page is None:
        from app.services.browser.session import get_browser
        # Không dùng proxy cho browser — VN proxy block CONNECT tunnel của Google
        # Quét trang chủ luôn chạy ẩn (headless) bất kể HEADLESS global.
        browser = await get_browser(headless=True)
        if browser is None:
            log.warning("CloakBrowser không khả dụng — skip browser search")
            return None
        own_browser = True
        page = await browser.new_page()
        own_page = True
        # Warmup: visit google.com để set session cookies trước search đầu tiên
        await _warmup_google(page)

    try:
        return await _search_one_engine(page, program_name, "google", proxy_url=proxy_url, use_form=own_page)
    except Exception as e:
        log.warning("Browser search failed for %r: %s", program_name, e)
        return None
    finally:
        if own_page:
            try:
                await page.close()
            except Exception:
                pass
        if own_browser and browser:
            try:
                await browser.close()
            except Exception:
                pass


async def find_homepages_browser_batch(
    programs: list,
    delay: float = 2.0,
    proxy_url: str = "",
) -> dict:
    """Batch Google search via MỘT CloakBrowser duy nhất. Sequential để tránh rate-limit.

    programs: list of (name, affiliate_url) tuples.
    `proxy_url`: proxy URL cho CapSolver (không dùng cho browser — VN proxy block Google).
    Trả về {affiliate_url: homepage_url}.
    """
    from app.services.browser.session import get_browser

    # Không dùng proxy cho browser — VN proxy block Google CONNECT tunnel
    # Quét trang chủ luôn chạy ẩn (headless) bất kể HEADLESS global.
    browser = await get_browser(headless=True)
    if not browser:
        log.warning("CloakBrowser không khả dụng cho batch browser search")
        return {}

    results: dict = {}
    try:
        page = await browser.new_page()
        # Warmup: visit google.com để set session cookies trước search batch
        await _warmup_google(page)
        _had_captcha = False
        _first_search = True
        for name, affiliate_url in programs:
            try:
                # Sau khi gặp captcha ở lần trước → nghỉ lâu hơn để Google không flag IP
                if _had_captcha:
                    extra = random.uniform(10.0, 18.0)
                    log.info("Batch: post-captcha cooldown %.1fs", extra)
                    await asyncio.sleep(extra)
                    _had_captcha = False

                homepage = await _search_one_engine(
                    page, name, "google", proxy_url=proxy_url,
                    use_form=_first_search,
                )
                _first_search = False
                if homepage:
                    results[affiliate_url] = homepage
                # Random jitter delay — tránh pattern đều đặn dễ bị detect
                jitter = random.uniform(max(delay, 3.5), max(delay, 3.5) + 3.5)
                await asyncio.sleep(jitter)
            except Exception as e:
                log.warning("Batch browser error for %r: %s", name, e)
                _had_captcha = True  # Có thể bị captcha gây exception
        try:
            await page.close()
        except Exception:
            pass
    except Exception as e:
        log.error("Batch browser fatal error: %s", e)
    finally:
        try:
            await browser.close()
        except Exception:
            pass
    return results


async def find_homepage_by_serpapi(program_name: str) -> Optional[str]:
    """Tìm trang chủ công ty qua SerpAPI Google search.

    Query: "{program_name}" official website
    Trả về URL trang chủ từ kết quả organic đầu tiên.
    """
    if not program_name:
        return None
    try:
        from app.services.serpapi.client import _call

        q = f'"{program_name}" official website'
        data = await _call({"engine": "google", "q": q, "num": 3})
        orgs = data.get("organic_results") or []

        # Ưu tiên kết quả khớp tên
        name_lower = program_name.lower().replace(" ", "")
        for res in orgs:
            link: str = res.get("link") or ""
            if not link.startswith("http"):
                continue
            # Skip các trang trung gian, directories, social
            host = urlparse(link).netloc.lower()
            skip = any(
                s in host
                for s in (
                    "capterra", "g2.com", "trustpilot", "linkedin", "twitter",
                    "facebook", "reddit", "getrewardful", "tolt.io", "crunchbase",
                    "alternativeto", "producthunt", "youtube",
                )
            )
            if skip:
                continue
            # Lấy kết quả đầu tiên không bị skip
            homepage = f"{urlparse(link).scheme}://{urlparse(link).netloc}/"
            log.info("SerpAPI found homepage: %s → %s", program_name, homepage)
            return homepage

        return None
    except Exception as e:
        log.warning("SerpAPI homepage search failed for %r: %s", program_name, e)
        return None


async def find_homepage(
    program_name: str,
    affiliate_url: str = "",
    method: str = "auto",
    proxy_url: str = "",
) -> Optional[str]:
    """Tìm trang chủ thực của program. Không dùng SerpAPI (tốn tiền).

    method: "auto" | "browser" — đều dùng CloakBrowser Google search,
    giải captcha tự động bằng CapSolver. Chỉ Google, không Bing.
    `proxy_url`: proxy URL cho CapSolver. Browser không dùng proxy.
    """
    return await find_homepage_by_browser(program_name, proxy_url=proxy_url)
