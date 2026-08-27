"""Experiment service — chạy browser-use Agent với bất kỳ task nào.

Lưu trữ jobs in-memory (xóa khi restart). Mỗi job chạy trong asyncio task riêng.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger
from app.services.captcha.capsolver import (
    CapSolver,
    detect_captcha_on_page,
    inject_cookies_and_reload,
    inject_recaptcha_token,
    inject_turnstile_token,
    click_turnstile_checkbox,
    fetch_turnstile_sitekey_from_iframe,
)
from app.services.llm_keyring import (
    gemini_key_count,
    get_gemini_key,
    is_quota_error,
    mark_gemini_quota_error,
    openai_key_count,
    get_openai_key,
    mark_openai_quota_error,
    deepseek_key_count,
    get_deepseek_key,
    mark_deepseek_quota_error,
)

log = get_logger("experiment_service")

# ── In-memory job store ───────────────────────────────────────────────────────
_JOBS: dict[int, dict] = {}
_NEXT_ID: list[int] = [1]


def _new_id() -> int:
    j = _NEXT_ID[0]
    _NEXT_ID[0] += 1
    return j


# ── Public API ────────────────────────────────────────────────────────────────

async def run_experiment(task: str, model: str = "gemini", max_steps: int = 30) -> int:
    """Tạo job mới và chạy agent trong background. Trả về job_id."""
    job_id = _new_id()
    _JOBS[job_id] = {
        "id": job_id,
        "task": task,
        "model": model,
        "max_steps": max_steps,
        "status": "running",
        "steps": 0,
        "result": None,
        "screenshot": None,
        "duration_sec": None,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    asyncio.create_task(_run_job(job_id, task, model, max_steps))
    log.info("Experiment job=%d started: %r (model=%s)", job_id, task[:60], model)
    return job_id


def get_job(job_id: int) -> dict | None:
    return _JOBS.get(job_id)


def list_jobs(limit: int = 50) -> list[dict]:
    return sorted(_JOBS.values(), key=lambda j: j["id"], reverse=True)[:limit]


def cancel_job(job_id: int) -> bool:
    j = _JOBS.get(job_id)
    if j and j["status"] == "running":
        j["status"] = "cancelled"
        j["result"] = "Đã hủy bởi người dùng."
        return True
    return False


# ── Background runner ─────────────────────────────────────────────────────────

async def _run_job(job_id: int, task: str, model: str, max_steps: int) -> None:
    started = time.time()
    job = _JOBS[job_id]

    try:
        # Import nặng bên trong để tránh slow startup
        from browser_use import ActionResult, Agent, Tools  # type: ignore[import-not-found]
        from app.services.browser.session import create_signup_browser_session

        # Khởi tạo LLM
        def _make_llm():
            if model == "openai":
                from browser_use import ChatOpenAI  # type: ignore[import-not-found]
                key, index, total = get_openai_key()
                if not key:
                    raise RuntimeError("Chưa cấu hình OPENAI_API_KEY trong .env")
                log.info("Experiment job=%d using OpenAI key %s/%s", job_id, index, total)
                return ChatOpenAI(model="gpt-4o", api_key=key), "openai", key
            if model == "deepseek":
                from browser_use import ChatOpenAI  # type: ignore[import-not-found]
                key, index, total = get_deepseek_key()
                if not key:
                    raise RuntimeError("Chưa cấu hình DEEPSEEK_API_KEY trong .env")
                log.info("Experiment job=%d using DeepSeek key %s/%s", job_id, index, total)
                return (
                    ChatOpenAI(
                        model="deepseek-chat",
                        api_key=key,
                        base_url="https://api.deepseek.com/v1",
                    ),
                    "deepseek",
                    key,
                )
            # Default: Gemini
            if not gemini_key_count():
                raise RuntimeError("Chưa cấu hình GEMINI_API_KEY trong .env")
            from browser_use import ChatGoogle  # type: ignore[import-not-found]
            key, index, total = get_gemini_key()
            log.info("Experiment job=%d using Gemini key %s/%s", job_id, index, total)
            return (
                ChatGoogle(
                    model=settings.signup_llm_model or "gemini-2.0-flash",
                    api_key=key,
                ),
                "gemini",
                key,
            )

        browser_session = create_signup_browser_session(headless=False)
        _capsolver = CapSolver(api_key=settings.capsolver_api_key or "")
        tools = Tools()

        # ── Captcha tools ──────────────────────────────────────────────────────
        if _capsolver.enabled:
            @tools.action(
                description=(
                    "Auto-detect and solve ANY captcha on the current page via CapSolver. "
                    "Call this FIRST when you see any captcha (Turnstile, reCAPTCHA, hCaptcha, "
                    "AWS WAF, Cloudflare interstitial, DataDome, GeeTest). No args needed."
                )
            )
            async def solve_captcha_auto(browser_session) -> ActionResult:
                try:
                    page = await browser_session.get_current_page()
                    page_url = await page.get_url()
                    info = await detect_captcha_on_page(page)
                    if not info:
                        return ActionResult(extracted_content="No captcha detected on current page.", include_in_memory=True)
                    ctype = info.get("type", "")
                    log.info("Experiment job=%d: captcha=%s info=%s", job_id, ctype, info)

                    if ctype == "turnstile":
                        sk = info.get("sitekey") or ""
                        if not sk:
                            return ActionResult(extracted_content="Turnstile detected but sitekey not found.", include_in_memory=True)
                        try:
                            await page.evaluate("() => { try { if (window.turnstile && typeof window.turnstile.reset === 'function') window.turnstile.reset(); } catch(e) {} return true; }")
                        except Exception:
                            pass
                        res = await _capsolver.solve_turnstile(page_url, sk, info.get("action") or "", info.get("cdata") or "")
                        token = res["token"]
                        ua = (res.get("user_agent") or "").strip()
                        if ua:
                            try:
                                cdp = await browser_session.get_or_create_cdp_session(target_id=None)
                                await cdp.cdp_client.send.Network.setUserAgentOverride(params={"userAgent": ua}, session_id=cdp.session_id)
                            except Exception as ue:
                                log.warning("Experiment job=%d: Turnstile UA override failed: %s", job_id, ue)
                        await inject_turnstile_token(page, token)
                        return ActionResult(extracted_content=f"Turnstile solved (token len={len(token)}, UA pinned). Click submit now.", include_in_memory=True)

                    if ctype == "turnstile_iframe":
                        iframe_src = info.get("iframe_src") or ""
                        sk = await fetch_turnstile_sitekey_from_iframe(iframe_src)
                        if not sk:
                            return ActionResult(extracted_content=f"Turnstile iframe: sitekey not extractable from {iframe_src}.", include_in_memory=True)
                        try:
                            res = await _capsolver.solve_turnstile(page_url, sk)
                            token = res["token"]
                            ua = (res.get("user_agent") or "").strip()
                            if ua:
                                try:
                                    cdp = await browser_session.get_or_create_cdp_session(target_id=None)
                                    await cdp.cdp_client.send.Network.setUserAgentOverride(params={"userAgent": ua}, session_id=cdp.session_id)
                                except Exception:
                                    pass
                            await inject_turnstile_token(page, token)
                            return ActionResult(extracted_content=f"Turnstile iframe solved (token len={len(token)}). Click submit now.", include_in_memory=True)
                        except Exception as ts_err:
                            ok = await click_turnstile_checkbox(page, timeout_ms=15000)
                            if ok:
                                return ActionResult(extracted_content="Turnstile passed via direct click. Click submit now.", include_in_memory=True)
                            return ActionResult(extracted_content=f"Turnstile failed: {ts_err}. Try reload.", include_in_memory=True)

                    if ctype in ("recaptcha_v2", "recaptcha_v2_enterprise"):
                        sk = info.get("sitekey") or ""
                        if ctype == "recaptcha_v2_enterprise":
                            token = await _capsolver.solve_recaptcha_v2_enterprise(page_url, sk)
                        else:
                            token = await _capsolver.solve_recaptcha_v2(page_url, sk, invisible=bool(info.get("invisible")))
                        await inject_recaptcha_token(page, token)
                        return ActionResult(extracted_content=f"reCAPTCHA v2 solved (token len={len(token)}). Click submit.", include_in_memory=True)

                    if ctype in ("recaptcha_v3", "recaptcha_v3_enterprise"):
                        sk = info.get("sitekey") or ""
                        if ctype == "recaptcha_v3_enterprise":
                            token = await _capsolver.solve_recaptcha_v3_enterprise(page_url, sk, action="submit")
                        else:
                            token = await _capsolver.solve_recaptcha_v3(page_url, sk, action="submit")
                        await inject_recaptcha_token(page, token)
                        return ActionResult(extracted_content="reCAPTCHA v3 solved. Click submit.", include_in_memory=True)

                    if ctype == "hcaptcha":
                        sk = info.get("sitekey") or ""
                        ua_raw = await page.evaluate("() => navigator.userAgent")
                        token = await _capsolver.solve_hcaptcha(page_url, sk, user_agent=(ua_raw or "").strip('"'))
                        await page.evaluate(
                            """(t) => {
                                const setVal = (el) => { el.value = t; el.innerHTML = t; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); };
                                document.querySelectorAll('textarea[name="h-captcha-response"],textarea[name="g-recaptcha-response"],input[name="h-captcha-response"]').forEach(setVal);
                                document.querySelectorAll('.h-captcha[data-callback]').forEach(el => { const cb=el.getAttribute('data-callback'); if(cb&&typeof window[cb]==='function') try{window[cb](t);}catch(e){} });
                                if(window.hcaptcha&&typeof window.hcaptcha.close==='function') try{window.hcaptcha.close();}catch(e){}
                            }""", token)
                        return ActionResult(extracted_content=f"hCaptcha solved (token len={len(token)}). Click submit.", include_in_memory=True)

                    if ctype == "hcaptcha_enterprise":
                        sk = info.get("sitekey") or ""
                        token = await _capsolver.solve_hcaptcha_enterprise(page_url, sk)
                        await page.evaluate(
                            """(t) => {
                                const setVal = (el) => { el.value = t; el.innerHTML = t; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); };
                                document.querySelectorAll('textarea[name="h-captcha-response"],textarea[name="g-recaptcha-response"],input[name="h-captcha-response"]').forEach(setVal);
                                document.querySelectorAll('.h-captcha[data-callback]').forEach(el => { const cb=el.getAttribute('data-callback'); if(cb&&typeof window[cb]==='function') try{window[cb](t);}catch(e){} });
                            }""", token)
                        return ActionResult(extracted_content="hCaptcha Enterprise solved. Click submit.", include_in_memory=True)

                    if ctype == "cloudflare_interstitial":
                        res = await _capsolver.solve_cloudflare_challenge(page_url)
                        await inject_cookies_and_reload(browser_session, page, res.get("cookies") or {}, page_url)
                        return ActionResult(extracted_content="Cloudflare interstitial bypassed; page reloaded.", include_in_memory=True)

                    if ctype == "aws_waf":
                        cookies = await _capsolver.solve_aws_waf(page_url)
                        await inject_cookies_and_reload(browser_session, page, cookies, page_url)
                        return ActionResult(extracted_content="AWS WAF bypassed; page reloaded.", include_in_memory=True)

                    if ctype == "geetest_v4":
                        cid = info.get("captchaId") or ""
                        sol = await _capsolver.solve_geetest(page_url, captcha_id=cid)
                        await page.evaluate("(s) => { window.__capsolver_geetest = s; }", sol)
                        return ActionResult(extracted_content="GeeTest v4 solved (saved to window.__capsolver_geetest).", include_in_memory=True)

                    if ctype == "datadome":
                        captcha_url = info.get("captchaUrl") or ""
                        ua_raw = await page.evaluate("() => navigator.userAgent")
                        cookie = await _capsolver.solve_datadome(captcha_url, (ua_raw or "").strip('"'))
                        name_value = cookie.split(";")[0]
                        name, _, value = name_value.partition("=")
                        host = page_url.split("/")[2] if "/" in page_url else ""
                        try:
                            await browser_session._cdp_set_cookies([{"name": name.strip(), "value": value.strip(), "domain": "." + host, "path": "/", "secure": True, "sameSite": "Lax"}])
                        except Exception:
                            pass
                        await page.reload()
                        return ActionResult(extracted_content="DataDome solved; page reloaded.", include_in_memory=True)

                    return ActionResult(extracted_content=f"Detected captcha '{ctype}' but no solver matched.", include_in_memory=True)
                except Exception as e:
                    log.exception("Experiment job=%d: solve_captcha_auto failed", job_id)
                    return ActionResult(extracted_content=f"Captcha solve failed: {type(e).__name__}: {e}", include_in_memory=True)

        try:
            if model == "openai":
                max_llm_attempts = max(1, openai_key_count())
            elif model == "deepseek":
                max_llm_attempts = max(1, deepseek_key_count())
            else:
                max_llm_attempts = max(1, gemini_key_count())
            history = None
            for llm_attempt in range(max_llm_attempts):
                llm, provider, api_key = _make_llm()
                try:
                    agent = Agent(
                        task=task,
                        llm=llm,
                        browser_session=browser_session,
                        tools=tools,
                        use_vision=True,
                        max_actions_per_step=3,
                    )
                    history = await agent.run(max_steps=max_steps)
                    break
                except Exception as e:
                    if is_quota_error(e) and llm_attempt < max_llm_attempts - 1:
                        if provider == "gemini":
                            mark_gemini_quota_error(api_key)
                        elif provider == "openai":
                            mark_openai_quota_error(api_key)
                        elif provider == "deepseek":
                            mark_deepseek_quota_error(api_key)
                        log.warning(
                            "Experiment job=%d %s key quota/rate-limit; retrying (%s/%s)",
                            job_id, provider, llm_attempt + 1, max_llm_attempts,
                        )
                        continue
                    raise
            if history is None:
                raise RuntimeError("Agent did not return history")

            # Trích kết quả
            steps = len(getattr(history, "history", []) or [])
            final_result: Any = None
            try:
                final_result = history.final_result()
            except Exception:
                pass
            if not final_result:
                try:
                    for h in reversed(getattr(history, "history", []) or []):
                        for r in getattr(h, "result", []) or []:
                            if getattr(r, "is_done", False):
                                final_result = getattr(r, "extracted_content", "") or ""
                                break
                        if final_result:
                            break
                except Exception:
                    pass

            # Chụp screenshot cuối
            screenshot_b64: str | None = None
            try:
                page = await browser_session.get_current_page()
                png = await page.screenshot(type="png")
                import base64
                screenshot_b64 = "data:image/png;base64," + base64.b64encode(png).decode()
            except Exception:
                pass

            # Cập nhật job — kiểm tra chưa bị cancel
            if job["status"] != "cancelled":
                job["status"] = "done"
                job["steps"] = steps
                job["result"] = (str(final_result)[:4000] if final_result
                                 else "Agent hoàn thành nhưng không trả kết quả.")
                job["screenshot"] = screenshot_b64

        finally:
            try:
                await browser_session.close()
            except Exception:
                pass
            try:
                await browser_session.kill()
            except Exception:
                pass

    except Exception as e:
        import traceback
        if job["status"] != "cancelled":
            job["status"] = "error"
            job["result"] = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()[-800:]}"
        log.error("Experiment job=%d error: %s", job_id, e)

    finally:
        job["duration_sec"] = round(time.time() - started, 2)
        log.info("Experiment job=%d finished: status=%s steps=%s duration=%.1fs",
                 job_id, job["status"], job.get("steps", 0), job["duration_sec"])
