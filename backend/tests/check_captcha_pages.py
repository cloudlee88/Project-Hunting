#!/usr/bin/env python3
"""
Test script to check captcha types on various affiliate signup pages.
Run: cd backend && .venv/bin/python3 tests/check_captcha_pages.py
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from playwright.async_api import async_playwright

CHROME = "/usr/bin/google-chrome"

PAGES = [
    ("GoAffPro Forever-Inject", "https://forever-inject.goaffpro.com/create-account"),
    ("Rewardful HeyGen signup", "https://heygen.getrewardful.com/signup"),
    ("Tolt Synthflow signup", "https://synthflow.tolt.io/signup"),
    ("Everflow Gurumedia", "https://gurumedia.everflowclient.io/affiliate/signup"),
]


async def detect_captcha(browser, name, url):
    print(f"\n{'='*60}")
    print(f"Checking: {name}")
    print(f"URL: {url}")
    page = await browser.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)  # wait for dynamic content

        # Check all frames
        frames = page.frames
        print(f"Frames ({len(frames)}):")
        for i, frame in enumerate(frames):
            print(f"  [{i}] {frame.url[:120]}")

        # Check for Turnstile
        result = await page.evaluate("""
            () => {
                const result = {};

                // Check iframes in main page
                const iframes = Array.from(document.querySelectorAll('iframe'));
                result.iframes = iframes.map(f => ({src: f.src, name: f.name, id: f.id, class: f.className}));

                // Turnstile element
                const tsEl = document.querySelector('.cf-turnstile, [data-sitekey]');
                result.turnstile_el = tsEl ? {tag: tsEl.tagName, sitekey: tsEl.dataset.sitekey} : null;

                // Scripts mentioning captcha
                result.captcha_scripts = Array.from(document.scripts)
                    .filter(s => s.src && (s.src.includes('recaptcha') || s.src.includes('hcaptcha') || 
                                           s.src.includes('turnstile') || s.src.includes('cloudflare')))
                    .map(s => s.src.split('?')[0]);

                // reCAPTCHA v3 detection
                const cfg = window.___grecaptcha_cfg;
                if (cfg && cfg.clients) {
                    const clients = Object.values(cfg.clients);
                    for (const client of clients) {
                        for (const val of Object.values(client)) {
                            if (val && typeof val === 'object' && val.sitekey) {
                                result.recaptcha_sitekey = val.sitekey;
                                result.recaptcha_type = val.sitekey.startsWith('6L') ? 'v3_or_v2' : 'enterprise';
                                break;
                            }
                        }
                    }
                }

                return result;
            }
        """)

        print(f"Iframes: {result.get('iframes', [])}")
        if result.get('turnstile_el'):
            print(f"Turnstile: {result['turnstile_el']}")
        if result.get('captcha_scripts'):
            print(f"Captcha scripts: {result['captcha_scripts']}")
        if result.get('recaptcha_sitekey'):
            print(f"reCAPTCHA sitekey: {result['recaptcha_sitekey']} type={result.get('recaptcha_type')}")

        # Check for hCaptcha
        hcaptcha = await page.evaluate("""
            () => {
                const el = document.querySelector('.h-captcha, [data-hcaptcha-sitekey]');
                return el ? {sitekey: el.dataset.sitekey || el.dataset.hcaptchaSitekey} : null;
            }
        """)
        if hcaptcha:
            print(f"hCaptcha: {hcaptcha}")

        # Check Turnstile in sub-frames
        for frame in frames[1:]:
            if any(x in frame.url for x in ['turnstile', 'cloudflare', 'challenges']):
                print(f"Turnstile sub-frame: {frame.url[:120]}")
                try:
                    sk = await frame.evaluate("() => document.querySelector('[data-sitekey]')?.dataset.sitekey")
                    if sk:
                        print(f"  sitekey in frame: {sk}")
                except Exception:
                    pass

        if not result.get('iframes') and not result.get('captcha_scripts') and not result.get('recaptcha_sitekey'):
            print("=> NO CAPTCHA DETECTED")
        
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await page.close()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path=CHROME,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        for name, url in PAGES:
            await detect_captcha(browser, name, url)
        await browser.close()
    print("\n\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
