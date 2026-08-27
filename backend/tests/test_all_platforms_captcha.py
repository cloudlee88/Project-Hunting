#!/usr/bin/env python3
"""
Comprehensive captcha detection test for ALL platforms in the system.
Opens real browsers, fills fake data, submits forms, detects captcha types.

Platforms tested:
- GoAffPro       (22821 programs)  → Turnstile iframe
- Rewardful      (Lovable)         → Programmatic Turnstile
- Tolt           (Lovable)         → No captcha / email OTP
- Everflow       (Lovable)         → reCAPTCHA v2 image challenge
- PromoteKit     (Lovable)         → ?
- FirstPromoter  (Lovable)         → ?
- Tapfiliate     (Lovable)         → ?
- PostAffiliatePro (Lovable)       → ?
- LeadDyno       (Lovable)         → ?
- PartnerStack   (OpenAffiliate)   → ?

Run:
    cd browser_automation/backend
    .venv/bin/python3 tests/test_all_platforms_captcha.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DB_URL", "sqlite:///data/app.db")

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"

CHROME = "/usr/bin/google-chrome"

# ---------------------------------------------------------------------------
# Platform test cases: (platform_name, signup_url, fill_form_fn or None)
# ---------------------------------------------------------------------------
PLATFORMS = [
    {
        "name": "GoAffPro",
        "url": "https://forever-inject.goaffpro.com/create-account",
        "expected": "turnstile_iframe",
    },
    {
        "name": "Rewardful (HeyGen)",
        "url": "https://heygen.getrewardful.com/signup",
        # Turnstile loads programmatically AFTER form interaction (not at page load)
        # detect_captcha_on_page returns None at initial load; returns 'turnstile' after interaction
        # test_captcha.py [8] verifies Turnstile detection works correctly in agent flow
        "expected": None,
    },
    {
        "name": "Tolt (Synthflow)",
        "url": "https://synthflow.tolt.io/signup",
        "expected": None,  # email OTP, no captcha
    },
    {
        "name": "Everflow",
        "url": "https://gurumedia.everflowclient.io/affiliate/signup",
        "expected": "recaptcha_v2_image_challenge",
    },
    {
        "name": "PromoteKit (adigen)",
        "url": "https://adigen.promotekit.com/",
        "expected": None,  # no captcha on signup form
    },
    {
        "name": "FirstPromoter (affistash)",
        "url": "https://affistash.firstpromoter.com/",
        # render=explicit reCAPTCHA v2; sitekey extracted from /recaptcha/api2/anchor frame
        "expected": "recaptcha_v2",
    },
    {
        "name": "FirstPromoter (apify)",
        "url": "https://apify.firstpromoter.com/signup/28997",
        "expected": "recaptcha_v2",
    },
    {
        "name": "Tapfiliate (adalysis)",
        "url": "https://adalysis.tapfiliate.com/",
        "expected": "recaptcha_v2_image_challenge",
    },
    {
        "name": "Tapfiliate (affiliates)",
        "url": "https://affiliates.tapfiliate.com/",
        "expected": "recaptcha_v2_image_challenge",
    },
    {
        "name": "PostAffiliatePro (1stphorm)",
        "url": "https://1stphorm.postaffiliatepro.com/",
        "expected": None,  # no captcha on public signup page
    },
    {
        "name": "LeadDyno",
        "url": "https://affiliates.leaddyno.com/",
        "expected": "recaptcha_v2_image_challenge",
    },
    {
        "name": "PartnerStack (monday)",
        "url": "https://partnerstack.com/monday",
        # api.js loaded but reCAPTCHA widget renders after form submit; detect returns None initially
        "expected": None,
    },
    {
        "name": "PartnerStack (freshworks)",
        "url": "https://partnerstack.com/freshworks",
        "expected": None,
    },
    {
        "name": "Rewardful (Synthesia)",
        "url": "https://synthesia.getrewardful.com/signup",
        # Different Rewardful merchants use different captcha types:
        # HeyGen → Turnstile, Synthesia → reCAPTCHA v2 image challenge
        "expected": "recaptcha_v2_image_challenge",
    },
    {
        "name": "PromoteKit (affiliates)",
        "url": "https://affiliates.promotekit.com/",
        "expected": "unknown",
    },
]


class _Shim:
    """Minimal shim to adapt Playwright page to detect_captcha_on_page."""
    def __init__(self, pw_page):
        self._pw = pw_page
        self.playwright_page = pw_page
        self._playwright_page = pw_page

    async def evaluate(self, js: str, *args):
        return await self._pw.evaluate(js, *args) if args else await self._pw.evaluate(js)

    @property
    def frames(self):
        return self._pw.frames


async def check_platform(browser, case: dict) -> dict:
    name = case["name"]
    url = case["url"]
    expected = case["expected"]

    page = await browser.new_page(
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    result = {"name": name, "url": url, "detected": None, "sitekey": None, "error": None,
              "extra_info": [], "expected": expected}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(3)

        # Collect page info
        title = await page.title()
        result["title"] = title

        # Frame inventory
        frames = page.frames
        cf_frames = [f.url for f in frames if "challenges.cloudflare.com" in f.url or "hcaptcha.com" in f.url or "google.com/recaptcha" in f.url or "recaptcha.net" in f.url]
        result["cf_frames"] = cf_frames

        # Raw DOM detection
        dom_info = await page.evaluate("""() => {
            const info = {};
            // iframes
            info.iframes = Array.from(document.querySelectorAll('iframe[src]')).map(f => f.src.substring(0,100));
            // scripts related to captcha
            info.captcha_scripts = Array.from(document.scripts)
                .map(s => s.src)
                .filter(s => s && (s.includes('captcha') || s.includes('recaptcha') || s.includes('turnstile') || s.includes('hcaptcha') || s.includes('funcaptcha') || s.includes('arkoselabs') || s.includes('mtcaptcha') || s.includes('geetest')));
            // captcha elements
            info.turnstile_el = !!document.querySelector('.cf-turnstile, [data-sitekey]');
            info.recaptcha_el = !!document.querySelector('.g-recaptcha, .grecaptcha-badge');
            info.hcaptcha_el = !!document.querySelector('.h-captcha, iframe[src*="hcaptcha"]');
            info.chl_widget = document.querySelector('[id*="cf-chl-widget"]') ? document.querySelector('[id*="cf-chl-widget"]').id : null;
            info.body_text_sample = (document.body?.innerText || '').slice(0,200).replace(/\\s+/g, ' ');
            return info;
        }""")
        result["dom_info"] = dom_info

        # Use our detect function
        from app.services.captcha.capsolver import detect_captcha_on_page
        shim = _Shim(page)
        detected = await detect_captcha_on_page(shim)
        if detected:
            result["detected"] = detected.get("type")
            result["sitekey"] = detected.get("sitekey") or detected.get("pkey") or detected.get("captchaId")
            result["full_detection"] = detected
        else:
            result["detected"] = None

        # Additional: try submitting if it's a signup form to trigger captcha
        # Only for "unknown" platforms
        if expected == "unknown" and result["detected"] is None:
            has_form = await page.evaluate("() => !!document.querySelector('form input[type=email], form input[type=text]')")
            if has_form:
                try:
                    # Fill fake data
                    await page.fill("input[type=email]", "testuser_captcha_check@gmail.com")
                    # Try name/first name fields
                    for sel in ["input[name*='name' i]:not([type=hidden])", "input[placeholder*='name' i]", "input[name='first_name']", "input[name='firstname']"]:
                        try:
                            await page.fill(sel, "Test User", timeout=1000)
                            break
                        except Exception:
                            pass
                    # Try password
                    for sel in ["input[type=password]"]:
                        try:
                            await page.fill(sel, "TestPass123!", timeout=1000)
                        except Exception:
                            pass
                    # Click submit
                    for submit_sel in ["button[type=submit]", "input[type=submit]", "button:has-text('Sign up')", "button:has-text('Register')", "button:has-text('Join')", "button:has-text('Create')"]:
                        try:
                            await page.click(submit_sel, timeout=2000)
                            break
                        except Exception:
                            pass
                    await asyncio.sleep(2)
                    # Re-detect after submit
                    detected2 = await detect_captcha_on_page(shim)
                    if detected2:
                        result["detected"] = detected2.get("type")
                        result["sitekey"] = detected2.get("sitekey") or ""
                        result["full_detection"] = detected2
                        result["extra_info"].append("captcha appeared after form submit")
                except Exception as e:
                    result["extra_info"].append(f"form fill error: {e}")

    except Exception as e:
        result["error"] = str(e)
    finally:
        await page.close()

    return result


def print_result(r: dict) -> None:
    name = r["name"]
    detected = r["detected"]
    sitekey = r["sitekey"]
    expected = r["expected"]
    error = r.get("error")

    if error:
        status = f"{RED}ERROR{RESET}"
        detail = error[:80]
    elif expected == "unknown":
        status = f"{CYAN}INFO{RESET}"
        detail = f"detected={detected!r}" + (f" sitekey={sitekey}" if sitekey else "")
    elif expected is None and detected is None:
        status = f"{GREEN}OK{RESET}"
        detail = "No captcha (expected)"
    elif detected == expected:
        status = f"{GREEN}OK{RESET}"
        detail = f"detected={detected!r} sitekey={sitekey}"
    elif detected is not None and expected == "unknown":
        status = f"{CYAN}FOUND{RESET}"
        detail = f"detected={detected!r} sitekey={sitekey}"
    else:
        status = f"{YELLOW}DIFF{RESET}"
        detail = f"detected={detected!r}, expected={expected!r}"

    print(f"  {status}  {name:<30}  {detail}")

    # Print DOM info for unknown platforms
    dom = r.get("dom_info") or {}
    if dom.get("captcha_scripts"):
        for s in dom["captcha_scripts"]:
            print(f"         script: {s[:100]}")
    if dom.get("chl_widget"):
        print(f"         chl-widget: {dom['chl_widget']}")
    if r.get("cf_frames"):
        for f in r["cf_frames"]:
            print(f"         frame: {f[:100]}")
    if r.get("extra_info"):
        for ei in r["extra_info"]:
            print(f"         note: {ei}")


async def main():
    print("=" * 70)
    print("  MULTI-PLATFORM CAPTCHA DETECTION TEST")
    print("=" * 70)
    t0 = time.time()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(f"{RED}playwright not installed{RESET}")
        return

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(
                executable_path=CHROME,
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as e:
            print(f"{RED}Cannot launch Chrome: {e}{RESET}")
            return

        results = []
        for case in PLATFORMS:
            print(f"\n  Checking {case['name']} ({case['url'][:60]})...")
            r = await check_platform(browser, case)
            print_result(r)
            results.append(r)

        await browser.close()

    # Summary
    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)

    # Group by detected type
    by_type: dict = {}
    for r in results:
        t = r["detected"] or "none"
        by_type.setdefault(t, []).append(r["name"])

    for ctype, names in sorted(by_type.items()):
        handled = _is_handled(ctype)
        status = f"{GREEN}handled{RESET}" if handled else f"{RED}NOT HANDLED{RESET}"
        print(f"  {ctype:<35} [{status}]")
        for n in names:
            print(f"    - {n}")

    print(f"\n  Done in {elapsed:.1f}s")

    unhandled = [r for r in results if r["detected"] and not _is_handled(r["detected"])]
    if unhandled:
        print(f"\n{RED}UNHANDLED captcha types found:{RESET}")
        for r in unhandled:
            print(f"  - {r['name']}: {r['detected']} (sitekey={r['sitekey']})")
        return 1
    else:
        print(f"\n{GREEN}All detected captcha types are handled!{RESET}")
        return 0


def _is_handled(ctype: str) -> bool:
    """Check if our agent_runner.py handles this captcha type."""
    handled = {
        "none", "turnstile", "turnstile_iframe", "recaptcha_v2", "recaptcha_v2_enterprise",
        "recaptcha_v3", "recaptcha_v3_enterprise", "recaptcha_v2_image_challenge",
        "hcaptcha", "hcaptcha_enterprise", "mtcaptcha", "funcaptcha", "geetest_v4",
        "image_to_text", "cloudflare_interstitial", "aws_waf", "datadome",
    }
    return ctype in handled


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc or 0)
