"""
Chạy signup bằng Playwright script (không LLM) từ playbook đã ghi lại.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

# Số lần thử lại trong 1 script run (bao gồm lần đầu)
_MAX_RETRIES_PER_STEP = 2
_STEP_TIMEOUT = 15_000  # ms


# ── Variable substitution ───────────────────────────────────────────────────

def _build_variables(program: dict, profile: dict) -> dict:
    v: dict = {
        "signup_url": program.get("signup_url") or program.get("url") or "",
        "program_name": program.get("name", ""),
        "program_url": program.get("url", ""),
    }
    for field in (
        "email", "password", "first_name", "last_name", "full_name", "company",
        "website", "phone", "address", "city", "country", "zip", "state",
    ):
        v[f"profile.{field}"] = profile.get(field, "")
    # Vietnamese field aliases: ho = last name, ten = first name
    if not v.get("profile.first_name"):
        v["profile.first_name"] = profile.get("ten", "")
    if not v.get("profile.last_name"):
        v["profile.last_name"] = profile.get("ho", "")
    if not v.get("profile.full_name"):
        ho = profile.get("ho", "")
        ten = profile.get("ten", "")
        if ho or ten:
            v["profile.full_name"] = f"{ho} {ten}".strip()
    # email có thể đến từ _picked_email
    picked = profile.get("_picked_email") or {}
    if picked.get("address"):
        v["profile.email"] = picked["address"]
    return v


def _resolve(value: str, variables: dict) -> str:
    for k, v in variables.items():
        value = value.replace(f"{{{{{k}}}}}", str(v or ""))
    return value


def _resolve_step(step: dict, variables: dict) -> dict:
    resolved = dict(step)
    for key in ("url", "value", "selector"):
        if key in resolved and isinstance(resolved[key], str):
            resolved[key] = _resolve(resolved[key], variables)
    return resolved


# ── Smart element interaction ───────────────────────────────────────────────

def _not_found_err(action: str, selector: str, selectors: list[str]) -> RuntimeError:
    tried = "; ".join(selectors[:4])
    return RuntimeError(f"{action} failed [{selector}]: element not found (tried: {tried})")


async def _find_in_frames(page: Any, selectors: list[str]) -> Any | None:
    """Tìm locator trong iframes khi main page không có element (Typeform, PAP iframe…)."""
    try:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            for sel in selectors:
                try:
                    loc = frame.locator(sel).first
                    if await loc.count() > 0:
                        return loc
                except Exception:
                    pass
    except Exception:
        pass
    return None


async def _smart_fill(page: Any, selector: str, value: str) -> None:
    """Điền giá trị vào input. Thử nhiều chiến lược selector, rồi iframe fallback."""
    selectors = _expand_selectors(selector)
    last_err = None

    for attempt in range(2):
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=3000)
                await loc.fill(value, timeout=5000)
                return
            except Exception as e:
                last_err = e
                continue
        if attempt == 0 and last_err is None:
            await asyncio.sleep(1.5)  # đợi page render thêm rồi thử lại

    # Iframe fallback
    if last_err is None:
        frame_loc = await _find_in_frames(page, selectors)
        if frame_loc:
            try:
                await frame_loc.scroll_into_view_if_needed(timeout=3000)
                await frame_loc.click(timeout=3000)
                await frame_loc.fill(value, timeout=5000)
                return
            except Exception as e:
                last_err = e

    if last_err is None:
        raise _not_found_err("fill", selector, selectors)
    raise RuntimeError(f"fill failed [{selector}]: {last_err}")


async def _smart_click(page: Any, selector: str) -> None:
    """Click element với fallback selectors, wait-retry và iframe fallback."""
    selectors = _expand_selectors(selector)
    last_err = None

    for attempt in range(2):
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                await loc.scroll_into_view_if_needed(timeout=3000)
                await loc.click(timeout=5000)
                return
            except Exception as e:
                last_err = e
                continue
        if attempt == 0 and last_err is None:
            await asyncio.sleep(1.5)  # đợi button được enable/render

    # Iframe fallback
    if last_err is None:
        frame_loc = await _find_in_frames(page, selectors)
        if frame_loc:
            try:
                await frame_loc.scroll_into_view_if_needed(timeout=3000)
                await frame_loc.click(timeout=5000)
                return
            except Exception as e:
                last_err = e

    if last_err is None:
        raise _not_found_err("click", selector, selectors)
    raise RuntimeError(f"click failed [{selector}]: {last_err}")


async def _smart_select(page: Any, selector: str, value: str) -> None:
    selectors = _expand_selectors(selector)
    last_err = None

    for attempt in range(2):
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                await loc.select_option(value, timeout=5000)
                return
            except Exception as e:
                last_err = e
        if attempt == 0 and last_err is None:
            await asyncio.sleep(1.5)

    if last_err is None:
        frame_loc = await _find_in_frames(page, selectors)
        if frame_loc:
            try:
                await frame_loc.select_option(value, timeout=5000)
                return
            except Exception as e:
                last_err = e

    if last_err is None:
        raise _not_found_err("select", selector, selectors)
    raise RuntimeError(f"select failed [{selector}]: {last_err}")


async def _smart_check(page: Any, selector: str) -> None:
    selectors = _expand_selectors(selector)
    last_err = None

    for attempt in range(2):
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() == 0:
                    continue
                await loc.check(timeout=5000)
                return
            except Exception as e:
                last_err = e
        if attempt == 0 and last_err is None:
            await asyncio.sleep(1.5)

    if last_err is None:
        frame_loc = await _find_in_frames(page, selectors)
        if frame_loc:
            try:
                await frame_loc.check(timeout=5000)
                return
            except Exception as e:
                last_err = e

    if last_err is None:
        raise _not_found_err("check", selector, selectors)
    raise RuntimeError(f"check failed [{selector}]: {last_err}")


def _expand_selectors(selector: str) -> list[str]:
    """Sinh thêm selector fallback từ primary selector."""
    selectors = [selector]
    if selector.startswith(("xpath=", "text=")):
        return selectors

    selectors.append(f"{selector}:visible")

    # [name=X] fallbacks — thử input[name=X] và type-based variants
    import re as _re
    m = _re.match(r'^\[name=["\']?(\w+)["\']?\]$', selector)
    if m:
        n = m.group(1)
        selectors.append(f"input[name={n}]")
        selectors.append(f"input[name={n}]:visible")
        if n == "email":
            selectors += ["input[type=email]", "input[type=email]:visible"]
        elif n == "password":
            selectors += ["input[type=password]", "input[type=password]:visible"]
        elif n in ("first_name", "firstName", "fname"):
            selectors += [
                "input[name=first_name]", "input[name=firstName]",
                "input[placeholder*='First' i]:visible",
            ]
        elif n in ("last_name", "lastName", "lname"):
            selectors += [
                "input[name=last_name]", "input[name=lastName]",
                "input[placeholder*='Last' i]:visible",
            ]

    return selectors


# ── Step executor ───────────────────────────────────────────────────────────

async def _execute_step(page: Any, step: dict, step_idx: int, **ctx) -> None:
    action = step.get("action", "")
    label = step.get("label", action)

    if action == "navigate":
        url = step.get("url", "")
        if not url:
            return
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass  # networkidle có thể timeout nếu trang có long-polling, bỏ qua
        # Phát hiện IP block sớm — tránh lãng phí các bước tiếp theo + T3 cost
        try:
            page_text = (await page.inner_text("body", timeout=3_000)).lower()
            _BLOCK_SIGNALS = [
                "access denied", "ip address has been blocked", "your ip",
                "too many requests", "rate limit exceeded",
                "error 1020",  # Cloudflare ray block
                "cloudflare ray id",
                "403 forbidden",
            ]
            if any(s in page_text for s in _BLOCK_SIGNALS):
                raise RuntimeError("IP_BLOCKED")
        except RuntimeError:
            raise  # re-raise IP_BLOCKED
        except Exception:
            pass

    elif action == "fill":
        await _smart_fill(page, step["selector"], step.get("value", ""))

    elif action == "click":
        await _smart_click(page, step["selector"])

    elif action == "select":
        await _smart_select(page, step["selector"], step.get("value", ""))

    elif action == "check":
        await _smart_check(page, step["selector"])

    elif action == "wait":
        await asyncio.sleep(step.get("ms", 1000) / 1000)

    elif action == "wait_element":
        sel = step["selector"]
        await page.wait_for_selector(sel, timeout=step.get("timeout", _STEP_TIMEOUT))

    elif action == "wait_url_change":
        # Đợi URL thay đổi so với URL hiện tại
        current = page.url
        try:
            await page.wait_for_url(
                lambda url: url != current,
                timeout=step.get("timeout", _STEP_TIMEOUT),
            )
        except Exception:
            pass  # Không lỗi nếu URL không đổi

    elif action == "assert_success":
        # Sẽ được xử lý ở cấp cao hơn
        pass

    elif action == "key":
        # Gửi phím bàn phím (Enter, Tab, Escape…) — cần cho Typeform/multi-step forms
        key = step.get("key", "Enter")
        await page.keyboard.press(key)

    elif action == "scroll_down":
        pixels = step.get("pixels", 400)
        await page.evaluate(f"window.scrollBy(0, {pixels})")

    elif action == "solve_captcha":
        await _solve_captcha_step(
            page,
            job_id=ctx.get("job_id", 0),
            proxy_url=ctx.get("proxy_url", ""),
        )

    else:
        log.debug("Unknown step action=%s, skipping", action)


async def _solve_captcha_step(page: Any, job_id: int = 0, proxy_url: str = "") -> None:
    """
    Tự động phát hiện và giải CAPTCHA trên trang hiện tại.
    Đợi tối đa 6s cho CAPTCHA xuất hiện (hay xảy ra sau khi click Submit).
    Raise RuntimeError nếu CAPTCHA tìm thấy nhưng không giải được.
    """
    from app.services.captcha.capsolver import (
        CapSolver,
        detect_captcha_on_page,
        inject_turnstile_token,
        inject_recaptcha_token,
        fetch_turnstile_sitekey_from_iframe,
    )

    capsolver = CapSolver(proxy_url=proxy_url)
    if not capsolver.enabled:
        log.debug("[job=%s] CapSolver không được cấu hình, bỏ qua captcha step", job_id)
        return

    # Đợi CAPTCHA xuất hiện (tối đa 6 giây, kiểm tra mỗi 1.5s)
    info = None
    for _ in range(4):
        info = await detect_captcha_on_page(page)
        if info:
            break
        await asyncio.sleep(1.5)

    if not info:
        log.debug("[job=%s] Không phát hiện CAPTCHA trên trang", job_id)
        return

    ctype = info.get("type") or ""
    log.info("[job=%s] CAPTCHA phát hiện: type=%s info=%s", job_id, ctype, info)
    page_url = page.url

    if ctype in ("turnstile", "turnstile_iframe"):
        sk = info.get("sitekey") or ""

        # Turnstile iframe: lấy sitekey từ iframe HTML
        if not sk and ctype == "turnstile_iframe":
            sk = await fetch_turnstile_sitekey_from_iframe(info.get("iframe_src") or "")

        if not sk:
            raise RuntimeError(f"Turnstile detected nhưng không lấy được sitekey (info={info})")

        # Reset widget nếu đang ở trạng thái lỗi
        try:
            await page.evaluate(
                "() => { try { if (window.turnstile && window.turnstile.reset) window.turnstile.reset(); } catch(e){} }"
            )
        except Exception:
            pass

        res = await capsolver.solve_turnstile(
            page_url, sk, info.get("action") or "", info.get("cdata") or ""
        )
        await inject_turnstile_token(page, res["token"])
        log.info("[job=%s] Turnstile solved (token len=%d)", job_id, len(res["token"]))

    elif ctype in ("recaptcha_v2", "recaptcha_v3"):
        sk = info.get("sitekey") or ""
        if not sk:
            raise RuntimeError(f"reCAPTCHA detected nhưng không lấy được sitekey (info={info})")
        invisible = info.get("invisible", False) or (ctype == "recaptcha_v3")
        res = await capsolver.solve_recaptcha_v2(page_url, sk) if not invisible \
            else await capsolver.solve_recaptcha_v3(page_url, sk)
        await inject_recaptcha_token(page, res["token"])
        log.info("[job=%s] reCAPTCHA solved (type=%s)", job_id, ctype)

    elif ctype == "hcaptcha":
        sk = info.get("sitekey") or ""
        if not sk:
            raise RuntimeError(f"hCaptcha detected nhưng không lấy được sitekey (info={info})")
        res = await capsolver.solve_hcaptcha(page_url, sk)
        await inject_recaptcha_token(page, res["token"])
        log.info("[job=%s] hCaptcha solved", job_id)

    else:
        raise RuntimeError(f"CAPTCHA không hỗ trợ tự động: type={ctype}")


async def _llm_recover_step(
    page: Any,
    step: dict,
    error: Exception,
    api_key: str,
    model: str = "gemini-2.0-flash",
    job_id: int = 0,
) -> bool:
    """
    Tier 2 recovery: 1 Gemini Flash call để tìm selector đúng khi script step thất bại.
    Rẻ hơn ~50x so với full LLM agent. Trả về True nếu step đã được xử lý.
    """
    if not api_key:
        return False
    action = step.get("action", "")
    if action not in ("fill", "click", "check", "select"):
        return False
    try:
        import base64
        import re as _re
        import google.genai as genai
        from google.genai import types

        # Chụp màn hình + lấy text visible
        screenshot_bytes = await page.screenshot(type="png")
        page_text = ""
        try:
            page_text = (await page.inner_text("body", timeout=3_000))[:1500]
        except Exception:
            pass

        label = step.get("label", action)
        selector = step.get("selector", "")
        value = step.get("value", "")
        prompt = (
            f"You are automating a web signup form. A Playwright step failed.\n\n"
            f"Failed step:\n"
            f"- Action: {action}\n"
            f"- Intended selector: {selector}\n"
            f"- Value: {value}\n"
            f"- Label: {label}\n"
            f"- Error: {error}\n\n"
            f"Visible page text:\n{page_text}\n\n"
            f"Based on the screenshot and page text, find the correct CSS selector or XPath "
            f"to perform this action.\n"
            f"Respond with JSON only, no other text:\n"
            f'{{\"action\": \"{action}\", \"selector\": \"css_or_xpath\"}}\n\n'
            f"Use xpath= prefix for XPath selectors. "
            f"Set action to \"skip\" if the element does not exist on this page."
        )

        client = genai.Client(api_key=api_key)
        response = await client.aio.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png"),
                types.Part.from_text(text=prompt),
            ],
        )
        response_text = (response.text or "").strip()
        m = _re.search(r'\{[^{}]+\}', response_text, _re.DOTALL)
        if not m:
            log.warning("[job=%s] LLM recovery: no JSON for step '%s'", job_id, label)
            return False

        data = json.loads(m.group())
        llm_action = data.get("action", "")
        new_selector = data.get("selector", "")

        if llm_action == "skip":
            log.info("[job=%s] LLM recovery: '%s' skipped (element absent per LLM)", job_id, label)
            return True
        if not new_selector:
            return False

        log.info("[job=%s] LLM recovery: '%s' → selector='%s'", job_id, label, new_selector)
        if action == "fill":
            await _smart_fill(page, new_selector, value)
        elif action == "click":
            await _smart_click(page, new_selector)
        elif action == "check":
            await _smart_check(page, new_selector)
        elif action == "select":
            await _smart_select(page, new_selector, value)
        return True
    except Exception as e:
        log.warning("[job=%s] LLM recovery error for step '%s': %s", job_id, step.get("label", action), e)
        return False


async def _check_success(page: Any, patterns: list[str]) -> bool:
    try:
        # Dùng inner_text thay vì content() để chỉ quét text hiển thị.
        # content() trả về toàn bộ HTML + JS source — các từ như "success"/"welcome"
        # có thể xuất hiện trong code JS của trang dù form chưa submit thành công.
        text = (await page.inner_text("body", timeout=5_000)).lower()
        return any(p.lower() in text for p in patterns)
    except Exception:
        return False


# ── Main entry point ────────────────────────────────────────────────────────

async def _pw_screenshot(page: Any, job_id: int, program_id: int, profile_id: str, email: str = "") -> str | None:
    """Chụp screenshot trang Playwright và lưu vào data/signup_screenshots/."""
    try:
        import time as _time
        from pathlib import Path
        from app.core.config import settings

        out_dir = settings.data_path("signup_screenshots")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = int(_time.time())
        fname = f"job{job_id}_prog{program_id}_{profile_id}_{ts}.png"
        path = out_dir / fname
        await page.screenshot(path=str(path), full_page=False)
        log.info("[job=%s] Screenshot saved: %s", job_id, fname)
        return fname
    except Exception as e:
        log.warning("[job=%s] Screenshot failed: %s", job_id, e)
        return None


def _parse_proxy(proxy_url: str) -> dict | None:
    """Parse proxy_url → Playwright proxy dict."""
    import re as _re
    if not proxy_url:
        return None
    m = _re.match(r'(https?://)(([^:@]+):([^@]+)@)?(.+)', proxy_url)
    if not m:
        return {"server": proxy_url}
    scheme, _, user, passwd, hostport = m.groups()
    d: dict = {"server": scheme + hostport}
    if user:
        d["username"] = user
        d["password"] = passwd
    return d


async def run_with_playbook(
    playbook_id: int,
    program: dict,
    profile: dict,
    headless: bool,
    user_id: int,
    job_id: int,
    proxy_url: str | None = None,
    llm_api_key: str = "",
    llm_model: str = "gemini-2.0-flash",
) -> dict:
    """
    Chạy signup bằng playbook đã ghi.
    Khi llm_api_key được cung cấp (hybrid mode), mỗi step thất bại sẽ được thử
    recover bằng 1 Gemini Flash call (Tier 2) trước khi dừng hẳn.
    Returns dict: {status, message, final_url, steps, duration_sec}
    """
    from sqlalchemy import select
    from app.core.db import SessionLocal
    from app.models.signup_playbook import SignupPlaybook
    from playwright.async_api import async_playwright

    started = time.time()

    # Load playbook
    async with SessionLocal() as session:
        pb = (
            await session.execute(select(SignupPlaybook).where(SignupPlaybook.id == playbook_id))
        ).scalar_one_or_none()
        if not pb:
            return {"status": "error", "message": f"Playbook #{playbook_id} không tồn tại", "steps": 0, "duration_sec": 0}
        steps = json.loads(pb.steps_json or "[]")

    if not steps:
        return {"status": "error", "message": "Playbook không có bước nào", "steps": 0, "duration_sec": 0}

    variables = _build_variables(program, profile)
    # Job proxy (truyền vào) ưu tiên hơn profile proxy
    proxy_url = proxy_url or (profile.get("proxy_url") or "").strip() or None
    proxy_config = _parse_proxy(proxy_url) if proxy_url else None

    # Tìm CloakBrowser binary để tránh bot-detection
    exec_path = None
    launch_args = [
        "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
    ]
    try:
        from cloakbrowser.browser import ensure_binary, build_args
        exec_path = ensure_binary()
        launch_args = build_args(True, launch_args, headless=headless)
        log.debug("[job=%s] CloakBrowser binary: %s", job_id, exec_path)
    except Exception:
        pass  # Fallback to default Chromium

    page = None
    browser = None
    executed_steps = 0
    final_url = ""

    try:
        async with async_playwright() as pw:
            launch_kwargs: dict = {
                "headless": headless,
                "args": launch_args,
            }
            if exec_path:
                launch_kwargs["executable_path"] = exec_path
            if proxy_config:
                launch_kwargs["proxy"] = proxy_config

            browser = await pw.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            success_patterns: list[str] = []
            error_step: str | None = None

            for step_idx, step in enumerate(steps):
                step = _resolve_step(step, variables)
                action = step.get("action", "")

                # Tích lũy success patterns
                if action == "assert_success":
                    success_patterns = step.get("patterns", [])
                    continue

                try:
                    await _execute_step(page, step, step_idx, job_id=job_id, proxy_url=proxy_url or "")
                    executed_steps += 1
                except Exception as e:
                    if step.get("optional"):
                        log.debug(
                            "[job=%s playbook=%s] Optional step %d (%s) skipped: %s",
                            job_id, playbook_id, step_idx + 1, step.get("label", action), e,
                        )
                        continue
                    # Tier 2: thử recover bằng LLM trước khi dừng hẳn
                    if llm_api_key and action in ("fill", "click", "check", "select"):
                        log.info(
                            "[job=%s playbook=%s] Step %d ('%s') failed, trying LLM recovery…",
                            job_id, playbook_id, step_idx + 1, step.get("label", action),
                        )
                        recovered = await _llm_recover_step(
                            page, step, e, llm_api_key, llm_model, job_id
                        )
                        if recovered:
                            executed_steps += 1
                            continue
                    error_step = f"Bước {step_idx + 1} ({step.get('label', action)}): {type(e).__name__}: {e}"
                    log.warning("[job=%s playbook=%s] %s", job_id, playbook_id, error_step)
                    break

            final_url = page.url if page else ""

            if error_step:
                return {
                    "status": "failed",
                    "message": error_step,
                    "final_url": final_url,
                    "screenshot": None,
                    "steps": executed_steps,
                    "duration_sec": round(time.time() - started, 2),
                }

            # Chụp screenshot sau khi steps hoàn thành (bằng chứng kết quả)
            screenshot_name = await _pw_screenshot(
                page, job_id, program.get("id", 0), profile.get("id", ""), profile.get("_picked_email", {}).get("address", "")
            )

            # Kiểm tra thành công dựa trên nội dung trang
            signup_url = variables.get("signup_url", "")
            if success_patterns and await _check_success(page, success_patterns):
                return {
                    "status": "success",
                    "message": "Playbook hoàn thành thành công.",
                    "final_url": final_url,
                    "screenshot": screenshot_name,
                    "steps": executed_steps,
                    "duration_sec": round(time.time() - started, 2),
                }

            # URL đã đổi (path thay đổi, không chỉ query param) → chờ verify email
            from urllib.parse import urlparse
            final_parsed = urlparse(final_url)
            signup_parsed = urlparse(signup_url)
            url_path_changed = (
                final_parsed.netloc != signup_parsed.netloc
                or final_parsed.path.rstrip("/") != signup_parsed.path.rstrip("/")
            )
            if url_path_changed:
                return {
                    "status": "pending_verify",
                    "message": "Form đã gửi, chờ xác nhận email.",
                    "final_url": final_url,
                    "screenshot": screenshot_name,
                    "steps": executed_steps,
                    "duration_sec": round(time.time() - started, 2),
                }

            return {
                "status": "failed",
                "message": "Script hoàn thành nhưng không xác nhận được thành công.",
                "final_url": final_url,
                "screenshot": screenshot_name,
                "steps": executed_steps,
                "duration_sec": round(time.time() - started, 2),
            }

    except Exception as e:
        log.exception("[job=%s playbook=%s] run_with_playbook crash", job_id, playbook_id)
        return {
            "status": "failed",
            "message": f"{type(e).__name__}: {e}"[:400],
            "final_url": final_url,
            "steps": executed_steps,
            "duration_sec": round(time.time() - started, 2),
        }


# ── Playbook stat update ─────────────────────────────────────────────────────

async def update_playbook_stats(
    playbook_id: int,
    success: bool,
    max_consecutive_before_llm: int = 2,
) -> bool:
    """
    Cập nhật stats và consecutive_fails.
    Returns True nếu cần trigger LLM fallback (consecutive_fails đạt ngưỡng).
    """
    from sqlalchemy import select
    from datetime import datetime
    from app.core.db import SessionLocal
    from app.models.signup_playbook import SignupPlaybook

    async with SessionLocal() as session:
        pb = (
            await session.execute(select(SignupPlaybook).where(SignupPlaybook.id == playbook_id))
        ).scalar_one_or_none()
        if not pb:
            return False

        pb.total_runs += 1
        pb.last_used_at = datetime.utcnow()

        if success:
            pb.success_runs += 1
            pb.consecutive_fails = 0
        else:
            pb.fail_runs += 1
            pb.consecutive_fails += 1

        needs_llm = pb.consecutive_fails >= max_consecutive_before_llm

        await session.commit()
        return needs_llm


# ── Affiliate platform detection (mirrors logic in api/playbooks.py) ──────────

def _detect_affiliate_platform(signup_url: str) -> str:
    """Detect affiliate network platform từ signup URL."""
    if not signup_url:
        return ""
    u = signup_url.lower()
    if "firstpromoter.com" in u:
        return "firstpromoter"
    if "everflowclient.io" in u or "everflow.com" in u:
        return "everflow"
    if "partnerstack.com" in u or "growsumo.com" in u:
        return "partnerstack"
    if "impact.com" in u or "impactradius.com" in u:
        return "impact"
    if "shareasale.com" in u:
        return "shareasale"
    if "cj.com" in u or "cjaffiliate.com" in u:
        return "cj"
    if "rakuten" in u or "linkshare.com" in u:
        return "rakuten"
    if "awin.com" in u:
        return "awin"
    if "refersion.com" in u:
        return "refersion"
    if "tapfiliate.com" in u:
        return "tapfiliate"
    if "rewardful.com" in u:
        return "rewardful"
    if "getambassador.com" in u:
        return "ambassador"
    if "post.affiliate" in u:
        return "postaffiliatepro"
    return ""


# ── Playbook matching ─────────────────────────────────────────────────────────

async def find_best_playbook(
    user_id: int,
    program: dict,
) -> "SignupPlaybook | None":
    """
    Tìm playbook phù hợp nhất cho một program theo 3 tầng:
      1. Khớp chính xác theo program_id
      2. Cùng affiliate platform + category (kể cả playbook gắn program_id khác)
      3. Cùng affiliate platform only

    Affiliate platform được detect từ signup_url — không dùng source field.
    """
    from sqlalchemy import select
    from app.core.db import SessionLocal
    from app.models.signup_playbook import SignupPlaybook

    program_id = program.get("id")
    signup_url = program.get("signup_url") or program.get("url") or ""
    aff_platform = _detect_affiliate_platform(signup_url)
    category = (program.get("category") or "").lower()

    async with SessionLocal() as session:
        # 1. Khớp chính xác program_id
        if program_id:
            pb = (
                await session.execute(
                    select(SignupPlaybook).where(
                        SignupPlaybook.user_id == user_id,
                        SignupPlaybook.program_id == program_id,
                        SignupPlaybook.status == "active",
                    )
                )
            ).scalar_one_or_none()
            if pb:
                return pb

        if not aff_platform:
            return None

        # Load tất cả active playbooks cho platform này
        all_pbs = (
            await session.execute(
                select(SignupPlaybook).where(
                    SignupPlaybook.user_id == user_id,
                    SignupPlaybook.platform == aff_platform,
                    SignupPlaybook.status == "active",
                )
            )
        ).scalars().all()

        if not all_pbs:
            return None

        # Ưu tiên: generic (không gắn program_id) trước cụ thể
        generic = [pb for pb in all_pbs if pb.program_id is None]
        specific = [pb for pb in all_pbs if pb.program_id is not None]
        ordered = generic + specific

        # 2. Platform + category
        for pb in ordered:
            if (pb.category or "").lower() == category:
                return pb

        # 3. Platform only — lấy cái đầu tiên
        return ordered[0]
