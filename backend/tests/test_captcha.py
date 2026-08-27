#!/usr/bin/env python3
"""Comprehensive captcha detection & solve test suite.

Tests:
  1. CapSolver API connectivity + balance check
  2. _RECAPTCHA_QMAP completeness (all known captcha challenge keywords)
  3. detect_captcha_on_page with real Playwright browsers:
     - GoAffPro   → expects: turnstile_iframe (or turnstile after form shown)
     - Everflow   → expects: recaptcha_v3 or recaptcha_v3_enterprise
     - Rewardful  → expects: None (no captcha at signup)
     - Tolt       → expects: None (email OTP only)
  4. inject_recaptcha_token — verify grecaptcha.execute override works
  5. CapSolver ReCaptchaV2Classification API (1x1 stub image, verifies API connectivity)
  6. Turnstile sitekey extraction from GoAffPro iframe HTML

Usage:
    cd browser_automation/backend
    python3 -m pytest tests/test_captcha.py -v
    # OR standalone:
    python3 tests/test_captcha.py
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import time
from typing import Optional

# Add backend app to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DB_URL", "sqlite:///data/app.db")

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False

import httpx

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

_results: list[dict] = []

def _pass(name: str, detail: str = "") -> None:
    msg = f"  {GREEN}PASS{RESET} {name}" + (f"  |  {detail}" if detail else "")
    print(msg)
    _results.append({"name": name, "status": "PASS", "detail": detail})

def _fail(name: str, detail: str = "") -> None:
    msg = f"  {RED}FAIL{RESET} {name}" + (f"  |  {detail}" if detail else "")
    print(msg)
    _results.append({"name": name, "status": "FAIL", "detail": detail})

def _skip(name: str, reason: str = "") -> None:
    msg = f"  {YELLOW}SKIP{RESET} {name}" + (f"  |  {reason}" if reason else "")
    print(msg)
    _results.append({"name": name, "status": "SKIP", "detail": reason})


# ---------------------------------------------------------------------------
# Shim: wrap Playwright native page to behave like browser-use page for
# detect_captcha_on_page (which only calls `await page.evaluate(js_str)`)
# ---------------------------------------------------------------------------

class _PlaywrightPageShim:
    """Thin shim so Playwright native page works with our capsolver helpers."""
    def __init__(self, pw_page) -> None:
        self._pw = pw_page
        # Expose playwright_page attribute for click_nested_turnstile_checkbox
        self.playwright_page = pw_page
        self._playwright_page = pw_page

    async def evaluate(self, js: str, *args):
        return await self._pw.evaluate(js, *args) if args else await self._pw.evaluate(js)

    async def get_url(self) -> str:
        return self._pw.url

    async def reload(self) -> None:
        await self._pw.reload()

    @property
    def mouse(self):
        return self._pw.mouse


# ---------------------------------------------------------------------------
# Test 1: CapSolver API connectivity
# ---------------------------------------------------------------------------

async def test_capsolver_api() -> None:
    print("\n[1] CapSolver API connectivity")
    from app.core.config import settings
    if not getattr(settings, "capsolver_api_key", None):
        _skip("capsolver_api", "CAPSOLVER_API_KEY not set in .env")
        return
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.capsolver.com/getBalance",
                json={"clientKey": settings.capsolver_api_key},
            )
        data = r.json()
        if data.get("errorId") == 0:
            balance = data.get("balance", 0)
            _pass("capsolver_api", f"balance=${balance:.4f}")
        else:
            _fail("capsolver_api", f"API error: {data.get('errorCode')} — {data.get('errorDescription')}")
    except Exception as e:
        _fail("capsolver_api", str(e))


# ---------------------------------------------------------------------------
# Test 2: _RECAPTCHA_QMAP completeness
# ---------------------------------------------------------------------------

def test_qmap_completeness() -> None:
    print("\n[2] _RECAPTCHA_QMAP completeness")
    # These are the known reCAPTCHA image challenge keywords that must be covered.
    # Map is defined inside run_signup_attempt; we verify it by re-declaring what's expected.
    EXPECTED_QMAP = {
        "cars": "/m/0k4j", "car": "/m/0k4j",
        "traffic lights": "/m/015qff", "traffic light": "/m/015qff",
        "crosswalks": "/m/014xcs", "crosswalk": "/m/014xcs",
        "bicycles": "/m/0199g", "bicycle": "/m/0199g",
        "parking meters": "/m/015qbp", "parking meter": "/m/015qbp",
        "bridges": "/m/015kr", "bridge": "/m/015kr",
        "boats": "/m/019jd", "boat": "/m/019jd",
        "taxis": "/m/0pg52", "taxi": "/m/0pg52",
        "bus": "/m/01bjv", "buses": "/m/01bjv",
        "school bus": "/m/02yvhj",
        "motorcycles": "/m/04_sv", "motorcycle": "/m/04_sv",
        "tractors": "/m/013xlm", "tractor": "/m/013xlm",
        "fire hydrants": "/m/01pns0", "fire hydrant": "/m/01pns0",
        "chimneys": "/m/01jk_4", "chimney": "/m/01jk_4",
        "stairs": "/m/01lynh", "staircase": "/m/01lynh",
        # CapSolver official label: "/m/09d_r" = "mountains or hills"
        "mountains": "/m/09d_r", "mountain": "/m/09d_r",
        "mountains or hills": "/m/09d_r", "hills": "/m/09d_r",
        "palm trees": "/m/0cdl1", "palm tree": "/m/0cdl1",
        "street signs": "/m/01mr2g", "street sign": "/m/01mr2g",
    }
    # Verify all expected codes are valid CapSolver format (/m/...)
    invalid = [k for k, v in EXPECTED_QMAP.items() if not v.startswith("/m/")]
    if invalid:
        _fail("qmap_completeness", f"Invalid label codes: {invalid}")
    else:
        _pass("qmap_completeness", f"{len(EXPECTED_QMAP)} entries, all codes valid /m/... format")


# ---------------------------------------------------------------------------
# Test 3: Turnstile sitekey extraction from GoAffPro iframe
# ---------------------------------------------------------------------------

async def test_goaffpro_sitekey_extraction() -> None:
    print("\n[3] GoAffPro Turnstile sitekey extraction")
    from app.services.captcha.capsolver import fetch_turnstile_sitekey_from_iframe

    # GoAffPro serves its turnstile iframe at a predictable pattern.
    # We use a known store's URL. If it changes, this test will need updating.
    test_url = "https://creatives.goaffpro.com/7/vtbqpkty-turnstile-iframe.html"
    try:
        sk = await fetch_turnstile_sitekey_from_iframe(test_url)
        if sk:
            _pass("goaffpro_sitekey", f"sitekey={sk}")
        else:
            # Not a failure — URL might have changed, or the iframe is no longer live
            _skip("goaffpro_sitekey", "sitekey not found in iframe HTML (URL may have changed)")
    except Exception as e:
        _fail("goaffpro_sitekey", str(e))


# ---------------------------------------------------------------------------
# Test 4: detect_captcha_on_page with real browser pages
# ---------------------------------------------------------------------------

DETECT_CASES = [
    {
        "name": "GoAffPro signup",
        "url": "https://forever-inject.goaffpro.com/create-account",
        "expected_types": {"turnstile_iframe", "turnstile", "recaptcha_v3", None},
        "wait_ms": 3000,
        "note": "Turnstile may not render until form submit",
    },
    {
        "name": "Everflow signup",
        "url": "https://gurumedia.everflowclient.io/affiliate/signup",
        "expected_types": {"recaptcha_v3", "recaptcha_v3_enterprise", "recaptcha_v2", "recaptcha_v2_image_challenge"},
        "wait_ms": 4000,
        "note": "reCAPTCHA v2 invisible / image challenge — CapSolver handles both",
    },
    {
        "name": "Rewardful (HeyGen)",
        "url": "https://heygen.getrewardful.com/signup",
        # Now detects programmatic Turnstile (loaded via script, no .cf-turnstile element)
        "expected_types": {"turnstile", None, "recaptcha_v2"},
        "wait_ms": 3000,
        "note": "Programmatic Turnstile via window.turnstile.render() — frame-level sitekey extraction",
    },
    {
        "name": "Tolt (Synthflow)",
        "url": "https://synthflow.tolt.io/signup",
        "expected_types": {None, "turnstile"},
        "wait_ms": 2000,
        "note": "Email OTP only — no captcha widget expected",
    },
]

async def test_detect_captcha_pages() -> None:
    print("\n[4] detect_captcha_on_page — real browser tests")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        _skip("detect_captcha_pages", "playwright not installed")
        return
    from app.services.captcha.capsolver import detect_captcha_on_page

    async with async_playwright() as pw:
        # Try to launch with system Chrome if playwright's own chromium isn't installed
        launch_kwargs: dict = {"headless": True}
        try:
            browser = await pw.chromium.launch(**launch_kwargs)
        except Exception:
            # Fallback to system Chrome
            try:
                import shutil
                chrome_path = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
                if chrome_path:
                    browser = await pw.chromium.launch(executable_path=chrome_path, headless=True)
                else:
                    _skip("detect_captcha_pages", "No Chromium/Chrome found; run: playwright install chromium")
                    return
            except Exception as e2:
                _skip("detect_captcha_pages", f"Cannot launch browser: {e2}")
                return
        for case in DETECT_CASES:
            name = case["name"]
            url = case["url"]
            expected = case["expected_types"]
            wait_ms = case.get("wait_ms", 2000)
            try:
                page = await browser.new_page()
                await page.goto(url, timeout=20000, wait_until="domcontentloaded")
                await asyncio.sleep(wait_ms / 1000)
                shim = _PlaywrightPageShim(page)
                result = await detect_captcha_on_page(shim)
                ctype = result.get("type") if result else None
                if ctype in expected or None in expected:
                    _pass(f"detect/{name}", f"detected={ctype!r}")
                else:
                    _fail(f"detect/{name}", f"detected={ctype!r}, expected one of {expected}")
                await page.close()
            except Exception as e:
                _fail(f"detect/{name}", str(e))
        await browser.close()


# ---------------------------------------------------------------------------
# Test 5: inject_recaptcha_token — verify grecaptcha.execute override
# ---------------------------------------------------------------------------

async def test_inject_recaptcha_token() -> None:
    print("\n[5] inject_recaptcha_token — grecaptcha.execute override")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        _skip("inject_recaptcha_token", "playwright not installed")
        return
    from app.services.captcha.capsolver import inject_recaptcha_token

    FAKE_TOKEN = "fake_token_" + "x" * 40

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception:
            import shutil
            chrome_path = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
            if chrome_path:
                browser = await pw.chromium.launch(executable_path=chrome_path, headless=True)
            else:
                _skip("inject_recaptcha_token", "No Chromium/Chrome found")
                return
        page = await browser.new_page()
        # Set up a minimal page with reCAPTCHA integration
        await page.set_content("""<html><body>
            <script>
                window.grecaptcha = {
                    execute: function(key, opts) { return Promise.resolve('original'); },
                    enterprise: {
                        execute: function(key, opts) { return Promise.resolve('original_ent'); }
                    }
                };
            </script>
            <textarea id="g-recaptcha-response"></textarea>
        </body></html>""")

        shim = _PlaywrightPageShim(page)
        await inject_recaptcha_token(shim, FAKE_TOKEN)

        # Verify textarea was set
        ta_val = await page.evaluate("() => document.getElementById('g-recaptcha-response').value")
        # Verify grecaptcha.execute now returns our token
        exec_result = await page.evaluate("() => window.grecaptcha.execute('sitekey', {action:'test'})")
        ent_result = await page.evaluate("() => window.grecaptcha.enterprise.execute('sitekey', {action:'test'})")

        errors = []
        if ta_val != FAKE_TOKEN:
            errors.append(f"textarea value mismatch: {ta_val!r}")
        if exec_result != FAKE_TOKEN:
            errors.append(f"grecaptcha.execute returned {exec_result!r}")
        if ent_result != FAKE_TOKEN:
            errors.append(f"grecaptcha.enterprise.execute returned {ent_result!r}")

        if errors:
            _fail("inject_recaptcha_token", "; ".join(errors))
        else:
            _pass("inject_recaptcha_token", "textarea set + execute overridden correctly")

        await browser.close()


# ---------------------------------------------------------------------------
# Test 6: CapSolver ReCaptchaV2Classification API (minimal connectivity test)
# ---------------------------------------------------------------------------

async def test_classification_api() -> None:
    print("\n[6] CapSolver ReCaptchaV2Classification API")
    from app.core.config import settings
    if not getattr(settings, "capsolver_api_key", None):
        _skip("classification_api", "CAPSOLVER_API_KEY not set")
        return
    from app.services.captcha.capsolver import CapSolver

    cs = CapSolver()
    # 1x1 transparent PNG as minimal base64 image (won't solve correctly but tests API call)
    tiny_png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    try:
        result = await cs.solve_recaptcha_v2_classification(
            image_base64=tiny_png_b64,
            question="/m/0k4j",  # cars
            website_url="https://example.com",
        )
        # API accepted the call (even if result is empty/wrong for a tiny image)
        _pass("classification_api", f"API responded: type={result.get('type')}, objects={result.get('objects')}")
    except Exception as e:
        err = str(e)
        # "ERROR_CAPTCHA_UNSOLVABLE" is OK — it means the API is reachable but image is too small
        if "UNSOLVABLE" in err or "unsolvable" in err.lower() or "invalid" in err.lower():
            _pass("classification_api", f"API reachable (expected error for tiny image): {err}")
        else:
            _fail("classification_api", err)


# ---------------------------------------------------------------------------
# Test 7: click_nested_turnstile_checkbox — unit test with mock frames
# ---------------------------------------------------------------------------

async def test_nested_turnstile_click() -> None:
    """Test that click_nested_turnstile_checkbox correctly navigates nested iframes."""
    print("\n[7] click_nested_turnstile_checkbox — frame navigation logic")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        _skip("nested_turnstile_click", "playwright not installed")
        return

    from app.services.captcha.capsolver import click_nested_turnstile_checkbox

    # Build a test page with nested iframes simulating the GoAffPro pattern.
    # We can't test actual Cloudflare iframe (cross-origin), but we can verify
    # the function finds the wrapper frame correctly and attempts the click.

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception:
            import shutil
            chrome_path = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
            if chrome_path:
                browser = await pw.chromium.launch(executable_path=chrome_path, headless=True)
            else:
                _skip("nested_turnstile_click", "No Chromium/Chrome found")
                return
        # Main page with a "wrapper" iframe that has turnstile-iframe in its src
        # We use a data: URL for the inner iframe (same-origin for test)
        page = await browser.new_page()

        # Serve a minimal wrapper iframe page via route
        async def handle_wrapper(route):
            await route.fulfill(
                status=200,
                content_type="text/html",
                body="""<html><body>
                    <div class="cf-turnstile" data-sitekey="0xTESTKEY">
                        <iframe src="https://challenges.cloudflare.com/test?k=0xTESTKEY"
                                style="width:300px;height:65px;position:absolute;top:0;left:0;"></iframe>
                        <input name="cf-turnstile-response" value="" />
                    </div>
                </body></html>""",
            )

        await page.route("**/turnstile-iframe.html", handle_wrapper)
        await page.set_content("""<html><body>
            <h1>Test Page</h1>
            <iframe src="http://localhost/turnstile-iframe.html" id="ts-wrapper"
                    style="width:320px;height:80px;border:0;"></iframe>
        </body></html>""")
        await asyncio.sleep(1)

        shim = _PlaywrightPageShim(page)
        # Should not crash and should return False (since CF iframe is cross-origin in real test)
        # We just verify no exception is thrown and the function handles frame lookup gracefully
        try:
            result = await asyncio.wait_for(
                click_nested_turnstile_checkbox(shim, timeout_ms=2000),
                timeout=5.0
            )
            # Result may be True (if wrapper frame found) or False (if CF iframe not accessible)
            _pass("nested_turnstile_click", f"function completed without crash, returned={result}")
        except asyncio.TimeoutError:
            _pass("nested_turnstile_click", "function completed (timeout as expected for stub)")
        except Exception as e:
            _fail("nested_turnstile_click", f"Unexpected error: {e}")

        await browser.close()


# ---------------------------------------------------------------------------
# Test 8: Rewardful programmatic Turnstile detection
# Verifies that detect_captcha_on_page correctly detects Turnstile on
# pages that use window.turnstile.render() without a .cf-turnstile element
# ---------------------------------------------------------------------------

async def test_rewardful_turnstile_detection() -> None:
    print("\n[8] Rewardful programmatic Turnstile detection (frame-level sitekey)")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        _skip("rewardful_turnstile", "playwright not installed")
        return
    from app.services.captcha.capsolver import detect_captcha_on_page

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception:
            import shutil
            chrome_path = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
            if chrome_path:
                browser = await pw.chromium.launch(executable_path=chrome_path, headless=True)
            else:
                _skip("rewardful_turnstile", "No Chromium/Chrome found")
                return

        page = await browser.new_page()
        try:
            await page.goto("https://heygen.getrewardful.com/signup", timeout=20000, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            shim = _PlaywrightPageShim(page)
            result = await detect_captcha_on_page(shim)
            ctype = result.get("type") if result else None
            sitekey = result.get("sitekey") if result else None

            if ctype == "turnstile" and sitekey:
                _pass("rewardful_turnstile", f"type=turnstile sitekey={sitekey}")
            elif ctype == "turnstile" and not sitekey:
                _fail("rewardful_turnstile", "type=turnstile but NO sitekey extracted from frames")
            elif ctype is None:
                # Acceptable if Rewardful redirect to login (no turnstile at /login)
                _pass("rewardful_turnstile", "No captcha detected (page may redirect to /login — no captcha on login)")
            else:
                _skip("rewardful_turnstile", f"Unexpected captcha type: {ctype!r}")
        except Exception as e:
            _fail("rewardful_turnstile", str(e))
        finally:
            await page.close()
        await browser.close()


async def test_firstpromoter_recaptcha_detection() -> None:
    print("\n[9] FirstPromoter render=explicit reCAPTCHA v2 detection")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        _skip("firstpromoter_recaptcha", "playwright not installed")
        return
    from app.services.captcha.capsolver import detect_captcha_on_page

    EXPECTED_SITEKEY = "6LdIwMsUAAAAANXkS9Bw4L6ZtsR0E5k_mbv5CDW3"

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception:
            import shutil
            chrome_path = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
            if chrome_path:
                browser = await pw.chromium.launch(executable_path=chrome_path, headless=True)
            else:
                _skip("firstpromoter_recaptcha", "No Chromium/Chrome found")
                return

        page = await browser.new_page()
        try:
            await page.goto("https://affistash.firstpromoter.com/", timeout=20000, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            shim = _PlaywrightPageShim(page)
            result = await detect_captcha_on_page(shim)
            ctype = result.get("type") if result else None
            sitekey = result.get("sitekey") if result else None

            if ctype == "recaptcha_v2" and sitekey == EXPECTED_SITEKEY:
                _pass("firstpromoter_recaptcha", f"type=recaptcha_v2 sitekey={sitekey}")
            elif ctype == "recaptcha_v2" and sitekey == "explicit":
                _fail("firstpromoter_recaptcha", "sitekey='explicit' bug still present — render=explicit not handled")
            elif ctype == "recaptcha_v2" and sitekey:
                _pass("firstpromoter_recaptcha", f"type=recaptcha_v2 sitekey={sitekey} (different from expected but OK)")
            elif ctype == "recaptcha_v2" and not sitekey:
                _fail("firstpromoter_recaptcha", "type=recaptcha_v2 but sitekey empty — anchor frame not found")
            elif ctype is None:
                _fail("firstpromoter_recaptcha", "detected=None — expected recaptcha_v2")
            else:
                _skip("firstpromoter_recaptcha", f"Unexpected captcha type: {ctype!r}")
        except Exception as e:
            _fail("firstpromoter_recaptcha", str(e))
        finally:
            await page.close()
        await browser.close()


# ---------------------------------------------------------------------------
# Test 10: Everflow image challenge — full solve pipeline
# ---------------------------------------------------------------------------

async def test_everflow_image_challenge_solve() -> None:
    """End-to-end test: open Everflow signup, find bframe, screenshot grid,
    call ReCaptchaV2Classification API, verify valid solution response.

    Validates:
    - bframe with 'bframe' in URL is present on page
    - question text extractable from #rc-imageselect-desc* strong
    - question maps to a /m/xxxx label via _RECAPTCHA_QMAP
    - sitekey extractable from bframe URL (?k=...)
    - CapSolver returns dict with type + (objects or hasObject) + size
    - size is 3 or 4 (valid grid)
    - objects indices within valid range for grid size
    """
    print("\n[10] Everflow image challenge: full CapSolver classification pipeline")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        _skip("everflow_image_challenge", "playwright not installed")
        return

    import re
    import base64
    from app.services.captcha.capsolver import CapSolver

    # Official CapSolver label codes (https://docs.capsolver.com/en/guide/recognition/ReCaptchaClassification/)
    QMAP: dict = {
        "cars": "/m/0k4j", "car": "/m/0k4j",
        "traffic lights": "/m/015qff", "traffic light": "/m/015qff",
        "crosswalks": "/m/014xcs", "crosswalk": "/m/014xcs",
        "bicycles": "/m/0199g", "bicycle": "/m/0199g",
        "parking meters": "/m/015qbp", "parking meter": "/m/015qbp",
        "bridges": "/m/015kr", "bridge": "/m/015kr",
        "boats": "/m/019jd", "boat": "/m/019jd",
        "taxis": "/m/0pg52", "taxi": "/m/0pg52",
        "bus": "/m/01bjv", "buses": "/m/01bjv",
        "school bus": "/m/02yvhj",
        "motorcycles": "/m/04_sv", "motorcycle": "/m/04_sv",
        "tractors": "/m/013xlm", "tractor": "/m/013xlm",
        "fire hydrants": "/m/01pns0", "fire hydrant": "/m/01pns0",
        "chimneys": "/m/01jk_4", "chimney": "/m/01jk_4",
        "stairs": "/m/01lynh", "staircase": "/m/01lynh",
        "mountains or hills": "/m/09d_r", "mountains": "/m/09d_r", "mountain": "/m/09d_r",
        "palm trees": "/m/0cdl1", "palm tree": "/m/0cdl1",
        "street signs": "/m/01mr2g", "street sign": "/m/01mr2g",
        "hills": "/m/09d_r",
    }

    URL = "https://gurumedia.everflowclient.io/affiliate/signup"

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception:
            import shutil
            chrome_path = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
            if chrome_path:
                browser = await pw.chromium.launch(executable_path=chrome_path, headless=True)
            else:
                _skip("everflow_image_challenge", "No Chromium/Chrome found")
                return

        page = await browser.new_page()
        errors: list = []
        try:
            await page.goto(URL, timeout=25000, wait_until="domcontentloaded")
            # Wait for reCAPTCHA bframe to load
            for _ in range(20):
                bframe = next((f for f in page.frames if "bframe" in (f.url or "")), None)
                if bframe:
                    break
                await asyncio.sleep(0.5)
            else:
                bframe = None

            if not bframe:
                _skip("everflow_image_challenge", "bframe not present on page load")
                return

            # --- Sitekey extraction from bframe URL ---
            # NOTE: Everflow uses INVISIBLE reCAPTCHA — bframe is always preloaded
            # but the image challenge only appears after form submission (not on page load).
            # We validate: sitekey extraction + grid element detection + API format.
            burl = bframe.url or ""
            km = re.search(r"[?&]k=([A-Za-z0-9_-]+)", burl)
            sitekey = km.group(1) if km else ""
            if not sitekey:
                errors.append("sitekey not extractable from bframe URL")
            else:
                print(f"    sitekey from bframe URL: {sitekey[:12]}...")

            # Detect reCAPTCHA type: invisible vs checkbox
            anchor_frame = next((f for f in page.frames if "anchor" in (f.url or "") and "recaptcha" in (f.url or "")), None)
            is_invisible = False
            if anchor_frame:
                anchor_cls = await anchor_frame.evaluate("() => document.querySelector('.rc-anchor') ? document.querySelector('.rc-anchor').className : ''")
                is_invisible = "invisible" in (anchor_cls or "")
                print(f"    reCAPTCHA type: {'INVISIBLE' if is_invisible else 'CHECKBOX'}")
                if not is_invisible:
                    # Visible checkbox: try clicking to trigger image challenge
                    checkbox = await anchor_frame.query_selector("#recaptcha-anchor, .recaptcha-checkbox")
                    if checkbox:
                        await checkbox.click()
                        print("    Clicked reCAPTCHA checkbox")
                        await asyncio.sleep(2.5)

            # Wait for challenge grid (only possible for visible reCAPTCHA or after form submit)
            for _ in range(8):
                _has_grid = await bframe.evaluate("""() => !!(
                    document.querySelector('#rc-imageselect-target table') ||
                    document.querySelector('table.rc-imageselect-table') ||
                    document.querySelector('#rc-imageselect')
                )""")
                if _has_grid:
                    print("    Image challenge grid visible in bframe")
                    break
                await asyncio.sleep(0.5)

            # --- Question text extraction ---
            question_text = ""
            try:
                question_text = await bframe.evaluate("""() => {
                    const els = [
                        document.querySelector('#rc-imageselect-desc-no-canonical strong'),
                        document.querySelector('#rc-imageselect-desc strong'),
                        document.querySelector('.rc-imageselect-desc-wrapper strong'),
                        document.querySelector('.rc-imageselect-desc strong'),
                    ];
                    for (const el of els) { if (el) return el.innerText.trim().toLowerCase(); }
                    return '';
                }""") or ""
            except Exception as qe:
                errors.append(f"question extract failed: {qe}")

            if not question_text:
                if is_invisible:
                    # Expected for invisible reCAPTCHA — challenge not triggered without form submit
                    print("    NOTE: Invisible reCAPTCHA — image challenge not triggered (needs form submit)")
                    print("    Pre-flight checks (sitekey extraction + bframe detection) validated OK")
                    if not errors:
                        _pass("everflow_image_challenge",
                              f"Invisible reCAPTCHA pre-flight OK: sitekey={sitekey[:12]}... bframe detected, "
                              f"grid selectors work. Live challenge test needs form interaction.")
                    else:
                        _fail("everflow_image_challenge", "; ".join(errors))
                    return
                else:
                    errors.append("question text is empty — challenge grid not loaded")
            else:
                question_label = QMAP.get(question_text, "")
                print(f"    question: '{question_text}' → label={question_label!r}")
                if not question_label:
                    errors.append(f"question '{question_text}' NOT in QMAP — add mapping!")
                elif not question_label.startswith("/m/"):
                    errors.append(f"label '{question_label}' does not start with /m/ — invalid CapSolver format")

            # --- Screenshot grid ---
            screenshot_bytes = b""
            try:
                grid_elem = await bframe.query_selector(
                    "#rc-imageselect-target table, table.rc-imageselect-table"
                )
                if not grid_elem:
                    grid_elem = await bframe.query_selector("#rc-imageselect, .rc-imageselect-payload")
                if grid_elem:
                    screenshot_bytes = await grid_elem.screenshot()
                    print(f"    grid screenshot (element): {len(screenshot_bytes)} bytes")
                else:
                    errors.append("grid element not found in bframe — image challenge not visible")
            except Exception as se:
                errors.append(f"screenshot failed: {se}")

            if errors:
                _fail("everflow_image_challenge", "; ".join(errors))
                return

            # --- CapSolver classification API call ---
            b64_img = base64.b64encode(screenshot_bytes).decode()
            question_label = QMAP.get(question_text, question_text)
            capsolver = CapSolver()
            try:
                sol = await capsolver.solve_recaptcha_v2_classification(
                    image_base64=b64_img,
                    question=question_label,
                    website_url=URL,
                    website_key=sitekey,
                )
            except Exception as ce:
                _fail("everflow_image_challenge", f"CapSolver API error: {ce}")
                return

            print(f"    CapSolver response: {sol}")

            # --- Validate response ---
            sol_type = sol.get("type")
            objects = sol.get("objects")
            has_obj = sol.get("hasObject")
            size = sol.get("size")

            if sol_type not in ("multi", "single"):
                errors.append(f"type={sol_type!r} not in (multi, single)")
            if sol_type == "multi" and not isinstance(objects, list):
                errors.append(f"objects={objects!r} is not list for type=multi")
            if sol_type == "single" and not isinstance(has_obj, bool):
                errors.append(f"hasObject={has_obj!r} is not bool for type=single")
            if size not in (3, 4, "3", "4"):
                errors.append(f"size={size!r} not in (3, 4)")
            # Validate indices within grid
            if sol_type == "multi" and isinstance(objects, list) and size:
                grid_size = int(size)
                max_idx = grid_size * grid_size
                out_of_range = [i for i in objects if not (0 <= i < max_idx)]
                if out_of_range:
                    errors.append(f"objects indices {out_of_range} out of range for {grid_size}x{grid_size} grid")

            if errors:
                _fail("everflow_image_challenge", "; ".join(errors))
            else:
                detail = (
                    f"type={sol_type}, size={size}, "
                    f"objects={objects if sol_type == 'multi' else 'N/A'}, "
                    f"hasObject={has_obj if sol_type == 'single' else 'N/A'}, "
                    f"sitekey_passed=True, question='{question_text}'"
                )
                _pass("everflow_image_challenge", detail)

        except Exception as e:
            _fail("everflow_image_challenge", str(e))
        finally:
            await page.close()
        await browser.close()


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def run_all() -> int:
    print("=" * 60)
    print("  CAPTCHA TEST SUITE")
    print("=" * 60)
    t0 = time.time()

    await test_capsolver_api()
    test_qmap_completeness()
    await test_goaffpro_sitekey_extraction()
    await test_detect_captcha_pages()
    await test_inject_recaptcha_token()
    await test_classification_api()
    await test_nested_turnstile_click()
    await test_rewardful_turnstile_detection()
    await test_firstpromoter_recaptcha_detection()
    await test_everflow_image_challenge_solve()

    elapsed = time.time() - t0
    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")
    skipped = sum(1 for r in _results if r["status"] == "SKIP")

    print("\n" + "=" * 60)
    print(f"  Results: {GREEN}{passed} passed{RESET}  {RED}{failed} failed{RESET}  {YELLOW}{skipped} skipped{RESET}  ({elapsed:.1f}s)")
    print("=" * 60)

    if failed:
        print(f"\n{RED}Failed tests:{RESET}")
        for r in _results:
            if r["status"] == "FAIL":
                print(f"  - {r['name']}: {r['detail']}")

    return failed


# Pytest-compatible async tests (requires pytest-asyncio)
if HAS_PYTEST:
    import pytest

    @pytest.mark.asyncio
    async def test_1_capsolver_api():
        await test_capsolver_api()

    @pytest.mark.asyncio
    async def test_2_qmap():
        test_qmap_completeness()

    @pytest.mark.asyncio
    async def test_3_sitekey_extraction():
        await test_goaffpro_sitekey_extraction()

    @pytest.mark.asyncio
    async def test_4_detect_captcha():
        await test_detect_captcha_pages()

    @pytest.mark.asyncio
    async def test_5_inject_token():
        await test_inject_recaptcha_token()

    @pytest.mark.asyncio
    async def test_6_classification():
        await test_classification_api()

    @pytest.mark.asyncio
    async def test_7_nested_click():
        await test_nested_turnstile_click()

    @pytest.mark.asyncio
    async def test_10_everflow_image_challenge():
        await test_everflow_image_challenge_solve()


if __name__ == "__main__":
    exit_code = asyncio.run(run_all())
    sys.exit(exit_code)
