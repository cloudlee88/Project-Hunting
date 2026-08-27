"""
Browser-use Agent driver — chạy 1 attempt đăng ký:
  (program_signup_url, profile, instruction_text) → kết quả (success/fail + log).

KHÔNG có queue/celery — đơn giản async function, gọi từ signup_runner.
"""

from __future__ import annotations

import asyncio
import base64
import json
import json as _json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.browser.session import create_signup_browser_session
from app.services.captcha.capsolver import (
    CapSolver,
    click_turnstile_checkbox,
    click_nested_turnstile_checkbox,
    detect_captcha_on_page,
    fetch_turnstile_sitekey_from_iframe,
    inject_cookies_and_reload,
    inject_recaptcha_token,
    inject_turnstile_token,
)
from app.services.captcha.memory_service import (
    add_captcha_success,
    format_memory_for_prompt,
)
from app.services.llm_keyring import (
    gemini_key_count,
    get_gemini_key,
    get_gemini_key_by_index,
    mark_gemini_quota_error,
    openai_key_count,
    get_openai_key,
    get_openai_key_by_index,
    mark_openai_quota_error,
    deepseek_key_count,
    get_deepseek_key,
    get_deepseek_key_by_index,
    mark_deepseek_quota_error,
    is_quota_error,
)

from .email_reader import wait_for_verification
from .instruction_parser import build_field_rules_block, parse_instruction_text
from .sms_otp import SmsOtpService

logger = logging.getLogger(__name__)

# Sitekeys where ReCaptchaV2TaskProxyLess always fails (ERROR_INVALID_TASK_DATA).
# Populated at runtime — skips BG token solve for those sitekeys to save time.
_bg_token_skip_sitekeys: set[str] = set()


def _is_cdp_startup_error(exc: Exception) -> bool:
    text = str(exc)
    return (
        "connect() timed out after 15s" in text
        or "CDP connection" in text
        or "Failed to establish CDP connection" in text
    )


async def _close_browser_session(browser_session: Any, job_id: int) -> None:
    try:
        await browser_session.close()
    except Exception as e:
        logger.warning(f"[job={job_id}] browser_session.close() failed: {e}")
    try:
        await browser_session.kill()
    except Exception:
        pass


def _build_task_prompt(program: dict, profile: dict, instruction_block: str, extra: str, signup_email: str = "", captcha_memory_block: str = "") -> str:
    """Soạn task prompt cho browser-use Agent."""
    name = profile.get("full_name") or ""
    ho = profile.get("ho") or ""
    ten = profile.get("ten") or ""
    if not name and (ho or ten):
        name = f"{ho} {ten}".strip()
    password = profile.get("password") or ""
    country = profile.get("country") or ""
    website = profile.get("website") or ""
    niche = ", ".join(profile.get("niche") or [])
    payment = profile.get("payment") or {}
    notes = profile.get("notes") or ""

    program_name = program.get("name") or ""
    signup_url = program.get("signup_url") or program.get("url") or ""

    parts = [
        f"Nhiệm vụ: ĐĂNG KÝ tài khoản affiliate trên trang `{program_name}` tại URL: {signup_url}",
        "",
        "### Email đăng ký (DUY NHẤT — bắt buộc dùng email này):",
        f"- Signup email: {signup_email}",
        "  ⇒ Đây là email DUY NHẤT được cấp phép điền vào field Email / Username.",
        "  ⇒ TUYỆT ĐỐI KHÔNG dùng email khác (không suy ra từ payment, không tự đặt).",
        "",
        "### Thông tin profile (dùng để điền các field khác, KHÔNG bao gồm email):",
        f"- Full name: {name}",
        f"- Họ (Last name): {ho}",
        f"- Tên (First name): {ten}",
        f"- Password: {password}",
        f"- Country: {country}",
        f"- Website / blog: {website}",
        f"- Niche / lĩnh vực: {niche}",
        f"- Payment info: {json.dumps(payment, ensure_ascii=False)}",
        f"- Ghi chú thêm: {notes}",
    ]
    if instruction_block:
        parts.append("")
        parts.append(instruction_block)
    if extra:
        parts.append("")
        parts.append("### Yêu cầu bổ sung từ user:")
        parts.append(extra)

    parts.extend([
        "",
        "### Quy trình mong muốn:",
        "1. Mở URL signup ở trên.",
        "2. Nếu thấy nút 'Sign up' / 'Register' / 'Join' / 'Become an affiliate' → click để vào form.",
        "3. Điền form theo dữ liệu profile, tuân thủ quy tắc field rules (nếu có).",
        "   - Field bắt buộc PHẢI điền. Field optional có thể bỏ trống nếu không có dữ liệu.",
        "   - Nếu thiếu dữ liệu thực, dùng giá trị hợp lý (ví dụ phone tạo số US ngẫu nhiên hợp lệ).",
        "   - Combobox / autocomplete dropdown (HeadlessUI, React Select, Vue Select): ĐỪNG dùng 'input' action vì có thể không trigger dropdown. Thay vào đó: dùng `select_combobox_option(query='Vietnam')` — tool này tự CDP-click focus → Input.insertText → CDP-click option trong 1 lần gọi.",
        "4. Nếu gặp CAPTCHA → CHIẾN LƯỢC CAPSOLVER-FIRST (mạnh nhất, đã verify):",
        "   a. GỌI `solve_captcha_auto()` NGAY (KHÔNG cần arg). Tool tự detect → solve qua CapSolver → override UA → inject token. ĐỪNG click checkbox 'Verify you are human' bằng tay (Cloudflare detect bot → 'Verification failed' không reset được).",
        "   b. Nếu auto trả về 'Click submit ngay' → đợi 1 giây rồi click nút submit form. KHÔNG click vào widget captcha nữa.",
        "   c. Nếu auto fail (lý do trong message) → fallback tool riêng:",
        "   - reCAPTCHA IMAGE GRID (popup chọn xe/xe đạp/đèn giao thông — 'Select all images with X') → `solve_recaptcha_image_challenge()` NGAY. TUYỆT ĐỐI KHÔNG tự click tile bằng tay — LLM không thể nhận dạng ảnh chính xác.",
        "   - Cloudflare Turnstile widget → `solve_cloudflare_turnstile(sitekey, action, cdata)`. action/cdata = data-action/data-cdata nếu có.",
        "   - Cloudflare full-page interstitial (cả trang là 'Verify you are human, just a moment...') → `solve_cloudflare_interstitial()`.",
        "   - reCAPTCHA v2 visible (div.g-recaptcha có checkbox 'I am not a robot') → `solve_recaptcha_v2(sitekey)`.",
        "   - reCAPTCHA v2 invisible (không có checkbox visible, data-size='invisible', hoặc captcha chạy tự động khi submit) → `solve_recaptcha_v2(sitekey, invisible=True)`.",
        "   - reCAPTCHA v3 (invisible, badge góc phải dưới, gọi grecaptcha.execute) → `solve_recaptcha_v3(sitekey, action)`. Action lấy từ JS hoặc thử 'submit'/'signup'.",
        "   - hCaptcha (div.h-captcha / iframe hcaptcha.com) → `solve_hcaptcha(sitekey)`.",
        "   - FunCaptcha / Arkose (iframe arkoselabs.com, xoay ảnh) → `solve_funcaptcha(public_key)` (data-pkey).",
        "   - AWS WAF challenge (cả trang 'Verify you are human' từ AWS) → `solve_aws_waf_challenge()`.",
        "   d. CHỈ DÙNG `click_cloudflare_checkbox()` khi CapSolver lỗi credit / network — đây là last resort.",
        "   e. NẾU đã solve_captcha_auto thành công (token injected) mà vẫn thấy popup chọn ảnh → gọi `solve_recaptcha_image_challenge()` NGAY (đây là v2 image challenge secondary verification).",
        "   - Sau khi tool trả về 'OK' / 'injected' / 'solved', chờ 1-2 giây rồi click submit form (KHÔNG click vào widget).",
        "5. Nếu form yêu cầu SMS / phone OTP verification:",
        "   a. Gọi tool `request_sms_phone_number` (không cần arg) → nhận lại {phone, rental_id}.",
        "   b. Điền số phone đó vào field phone của form. Submit để site gửi SMS.",
        "   c. Gọi tool `read_sms_otp_code` (không cần arg) → nhận code (digits).",
        "   d. Điền code vào ô OTP, submit verify.",
        "6. Nếu form yêu cầu EMAIL verification (gửi link/code vào email):",
        "   a. Submit form trước (site sẽ gửi email).",
        "   b. Gọi tool `read_email_verification` với sender_contains='' (hoặc tên brand như 'webflow').",
        "   c. Nếu trả về 'code' → điền code vào ô verify trên trang.",
        "   d. Nếu trả về 'link' → gọi tool `navigate` với url=link đó để mở verification page.",
        "7. Submit form cuối — tìm nút 'Apply' / 'Submit' / 'Register' / 'Join' / 'Sign up' → click.",
        "   - Nếu form có Terms & Conditions TEXT AREA có scroll: scroll nó xuống đáy (JavaScript scrollTop=999999 hoặc click vào nó rồi Ctrl+End) TRƯỚC khi click Apply.",
        "   - Sau khi tool captcha trả về 'token set' hoặc 'click Apply/Submit ngay' → đợi 1-2 giây rồi click nút submit.",
        "   - KHÔNG đợi quá 3 giây sau captcha — token có hạn ~2 phút.",
        "   - Khi thấy success/thank-you/confirmation/dashboard → coi như success.",
        "8. Khi xong, gọi `done` với JSON: {\"status\": \"success\"|\"failed\"|\"captcha\"|\"pending_verify\", \"message\": \"...\", \"final_url\": \"...\"}.",
        "",
        "QUAN TRỌNG:",
        "- KHÔNG đăng ký nhiều account. Mỗi lần chỉ 1 lần submit.",
        "- Nếu trang yêu cầu login (đã có account) → báo failed 'ALREADY_REGISTERED'.",
        "- Nếu signup_url không hợp lệ / 404 → báo failed 'INVALID_URL'.",
        "- Nếu IP bị ban / 'Access Denied' / 'Too many requests' → báo failed 'IP_BLOCKED' (system sẽ retry với proxy/profile khác).",
        "- Nếu tool OTP/Email báo lỗi không thể vượt qua → báo failed với message lỗi cụ thể.",
    ])
    if captcha_memory_block:
        parts.append("")
        parts.append("### Captcha Memory (kinh nghiệm cá nhân hóa — học từ lần trước):")
        parts.append(captcha_memory_block)
    return "\n".join(parts)


def _parse_agent_result(history: Any) -> dict:
    """Trích status/message từ AgentHistoryList của browser-use."""
    out = {"status": "failed", "message": "", "final_url": "", "steps": 0}
    try:
        out["steps"] = len(getattr(history, "history", []) or [])
    except Exception:
        pass
    final_result = None
    try:
        final_result = history.final_result()  # browser-use API
    except Exception:
        pass
    if not final_result:
        try:
            for h in reversed(getattr(history, "history", []) or []):
                if getattr(h, "result", None):
                    for r in h.result:
                        if getattr(r, "is_done", False):
                            final_result = r.extracted_content or ""
                            break
                if final_result:
                    break
        except Exception:
            pass

    if final_result:
        text = final_result if isinstance(final_result, str) else str(final_result)
        out["message"] = text[:2000]
        try:
            data = json.loads(_extract_json(text))
            if isinstance(data, dict):
                out["status"] = (data.get("status") or "").lower() or out["status"]
                if data.get("message"):
                    out["message"] = data["message"][:2000]
                if data.get("final_url"):
                    out["final_url"] = data["final_url"]
        except Exception:
            # Không phải JSON → heuristic
            low = text.lower()
            if "success" in low or "successfully" in low or "đăng ký thành công" in low:
                out["status"] = "success"
            elif "captcha" in low:
                out["status"] = "captcha"
            elif "verify" in low or "verification" in low:
                out["status"] = "pending_verify"
    return out


def _extract_json(text: str) -> str:
    """Tìm JSON object đầu tiên trong text."""
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text


async def _capture_screenshot(browser_session, job_id: int, program_id: int, profile_id: str) -> str | None:
    """Chụp screenshot trang hiện tại, save vào data/signup_screenshots/."""
    try:
        out_dir = settings.data_path("signup_screenshots")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        path = out_dir / f"job{job_id}_prog{program_id}_{profile_id}_{ts}.png"
        # browser-use BrowserSession có method screenshot
        png_bytes = await browser_session.take_screenshot(full_page=False)
        if isinstance(png_bytes, bytes):
            path.write_bytes(png_bytes)
        elif isinstance(png_bytes, str):
            # base64
            import base64
            path.write_bytes(base64.b64decode(png_bytes))
        else:
            return None
        return str(path.relative_to(settings.data_dir.resolve() if settings.data_dir.is_absolute() else Path.cwd()))
    except Exception as e:
        logger.warning(f"Screenshot failed: {e}")
        return None


def _extract_last_screenshot_from_history(history, job_id: int, program_id: int, profile_id: str) -> str | None:
    """Lấy screenshot cuối cùng từ agent history (đã được lưu sẵn trên đĩa bởi browser-use)."""
    try:
        import base64
        import shutil
        out_dir = settings.data_path("signup_screenshots")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        target = out_dir / f"job{job_id}_prog{program_id}_{profile_id}_{ts}.png"

        items = list(getattr(history, "history", []) or [])
        for h in reversed(items):
            state = getattr(h, "state", None)
            if not state:
                continue
            src_path = getattr(state, "screenshot_path", None)
            if src_path and Path(src_path).exists():
                shutil.copyfile(src_path, target)
                return target.name
            # fallback: base64 screenshot in memory
            b64 = getattr(state, "screenshot", None)
            if b64:
                target.write_bytes(base64.b64decode(b64))
                return target.name
        return None
    except Exception as e:
        logger.warning(f"Extract screenshot from history failed: {e}")
        return None


async def run_signup_attempt(
    *,
    job_id: int,
    program: dict,
    profile: dict,
    instruction_content: str = "",
    instruction_filename: str = "",
    extra_prompt: str = "",
    headless: bool = False,
    user_id: int = 0,
    gemini_key_index: int = 0,
    llm_provider: str = "",
    llm_key_index: int = 0,
) -> dict:
    """
    Chạy 1 lần đăng ký. Return dict:
      {status, message, steps, final_url, screenshot, duration_sec}
    status ∈ success|failed|captcha|pending_verify|error
    """
    # `browser_use` import ở trong hàm — thư viện nặng, tránh slow startup FastAPI.
    from browser_use import ActionResult, Agent, ChatGoogle, ChatOpenAI, Tools

    started = time.time()
    program_id = program.get("id")
    profile_id = profile.get("id") or "unknown"

    # 1. Chọn LLM — ưu tiên: llm_provider được chỉ định → Gemini → OpenAI → DeepSeek
    # Xác định provider thực sự sẽ dùng
    _effective_provider = llm_provider or ""
    if not _effective_provider:
        if gemini_key_count():
            _effective_provider = "gemini"
        elif openai_key_count():
            _effective_provider = "openai"
        elif deepseek_key_count():
            _effective_provider = "deepseek"

    if not _effective_provider:
        return {
            "status": "error",
            "message": "Chưa cấu hình API key LLM. Thêm GEMINI_API_KEY, OPENAI_API_KEY hoặc DEEPSEEK_API_KEY tại Thư viện → API KEY.",
            "steps": 0,
            "duration_sec": 0,
        }

    def _make_llm(preferred_key_index: int = 0):
        if _effective_provider == "openai":
            key, index, total = (
                get_openai_key_by_index(preferred_key_index)
                if preferred_key_index
                else get_openai_key()
            )
            logger.info("[job=%s] Using OpenAI key %s/%s", job_id, index, total)
            return ChatOpenAI(model="gpt-4o", api_key=key), "openai", key
        if _effective_provider == "deepseek":
            key, index, total = (
                get_deepseek_key_by_index(preferred_key_index)
                if preferred_key_index
                else get_deepseek_key()
            )
            logger.info("[job=%s] Using DeepSeek key %s/%s", job_id, index, total)
            return (
                ChatOpenAI(
                    model="deepseek-chat",
                    api_key=key,
                    base_url="https://api.deepseek.com/v1",
                ),
                "deepseek",
                key,
            )
        # default: gemini
        key, index, total = (
            get_gemini_key_by_index(preferred_key_index)
            if preferred_key_index
            else get_gemini_key()
        )
        logger.info("[job=%s] Using Gemini key %s/%s", job_id, index, total)
        return (
            ChatGoogle(
                model=settings.signup_llm_model or "gemini-2.0-flash",
                api_key=key,
            ),
            "gemini",
            key,
        )

    # 2. Build prompt
    parsed = parse_instruction_text(instruction_content, instruction_filename)
    rules_block = build_field_rules_block(parsed) if parsed else ""
    # Email đến từ job selection (bơm bởi _enrich) — KHÔNG fallback sang bất kỳ nguồn nào khác
    _signup_email = (
        (profile.get("imap") or {}).get("user")
        or profile.get("email")
        or ""
    )
    # Captcha memory — inject kinh nghiệm cá nhân hóa theo user_id
    from urllib.parse import urlparse as _urlparse
    _signup_url = program.get("signup_url") or program.get("url") or ""
    _site_host = _urlparse(_signup_url).hostname or ""
    _captcha_mem_block = format_memory_for_prompt(user_id, _site_host) if user_id else ""
    task = _build_task_prompt(program, profile, rules_block, extra_prompt, signup_email=_signup_email, captcha_memory_block=_captcha_mem_block)

    # 3. Browser session — đi qua factory tập trung (CloakBrowser binary + stealth args + proxy)
    proxy_override = (profile.get("proxy_url") or "").strip() or None
    browser_session = None

    # 4. Tools — đăng ký custom action cho CapSolver
    tools = Tools()
    capsolver = CapSolver(proxy_url=proxy_override)

    if capsolver.enabled:

        # ─── internal helper: reCAPTCHA v2 image grid (bframe) classification ─────
        # Label codes từ CapSolver docs chính thức: https://docs.capsolver.com/en/guide/recognition/ReCaptchaClassification/
        _RECAPTCHA_QMAP: dict = {
            "cars": "/m/0k4j", "car": "/m/0k4j",
            "traffic lights": "/m/015qff", "traffic light": "/m/015qff",
            "crosswalks": "/m/014xcs", "crosswalk": "/m/014xcs",
            "bicycles": "/m/0199g", "bicycle": "/m/0199g",
            "parking meters": "/m/015qbp", "parking meter": "/m/015qbp",
            "bridges": "/m/015kr", "bridge": "/m/015kr",
            "boats": "/m/019jd", "boat": "/m/019jd",
            "taxis": "/m/0pg52", "taxi": "/m/0pg52",
            "bus": "/m/01bjv", "buses": "/m/01bjv",
            "school bus": "/m/02yvhj", "school buses": "/m/02yvhj",
            "motorcycles": "/m/04_sv", "motorcycle": "/m/04_sv",
            "tractors": "/m/013xlm", "tractor": "/m/013xlm",
            "fire hydrants": "/m/01pns0", "fire hydrant": "/m/01pns0",
            "chimneys": "/m/01jk_4", "chimney": "/m/01jk_4",
            "stairs": "/m/01lynh", "staircase": "/m/01lynh",
            "mountains": "/m/09d_r", "mountain": "/m/09d_r", "hills": "/m/09d_r",
            "mountains or hills": "/m/09d_r",
            "palm trees": "/m/0cdl1", "palm tree": "/m/0cdl1",
            "street signs": "/m/01mr2g", "street sign": "/m/01mr2g",
            "vehicles": "/m/07yv9", "vehicle": "/m/07yv9",
            "trucks": "/m/07r04", "truck": "/m/07r04",
            "planes": "/m/0k5j", "airplane": "/m/0k5j", "airplanes": "/m/0k5j",
            "trains": "/m/07jdr", "train": "/m/07jdr",
        }

        def _normalize_qtext(q: str) -> str:
            """Normalize reCAPTCHA question text to look up in QMAP.
            Strips leading 'a ', 'an ', 'all ', common prefixes, and whitespace."""
            q = q.strip().lower()
            for prefix in ("select all images with ", "click on all images with ",
                           "click all images with ", "click on all ", "click all ",
                           "select all ", "all images with ", "images with "):
                if q.startswith(prefix):
                    q = q[len(prefix):]
            for article in ("a ", "an ", "the "):
                if q.startswith(article):
                    q = q[len(article):]
            return q.strip()

        async def _solve_image_challenge(page, page_url: str, sitekey: str = "") -> "ActionResult":
            """Giải reCAPTCHA v2 image grid qua bframe + ReCaptchaV2Classification.

            Dùng CDP createIsolatedWorld để access cross-origin bframe (PageActor không có .frames).
            Screenshot grid qua Page.captureScreenshot + clip, click tiles qua DOM .click() in bframe context.
            Hỗ trợ dynamic challenge — tối đa 8 rounds.
            """
            import json as _json
            try:
                cdp = page._client  # CDPClient from browser-use PageActor
                session_id = await page._ensure_session()

                # 1. Get frame tree to find bframe frame ID
                frame_tree = await cdp.send.Page.getFrameTree(params={}, session_id=session_id)

                def _find_frame(node, match_fn):
                    frame = node.get("frame", {})
                    if match_fn(frame):
                        return frame
                    for child in node.get("childFrames", []):
                        r = _find_frame(child, match_fn)
                        if r:
                            return r
                    return None

                bframe_info = _find_frame(
                    frame_tree.get("frameTree", {}),
                    lambda f: "bframe" in (f.get("url") or ""),
                )
                if not bframe_info:
                    return ActionResult(
                        extracted_content="reCAPTCHA bframe không tìm thấy — challenge chưa mở hoặc đã giải xong.",
                        include_in_memory=True,
                    )

                bframe_frame_id = bframe_info["id"]
                bframe_url = bframe_info.get("url", "")
                logger.info(f"[job={job_id}] Found bframe: {bframe_url[:80]}")

                # Auto-extract sitekey from bframe URL (e.g. ?k=6Le...) if not provided
                if not sitekey:
                    _km = re.search(r"[?&]k=([A-Za-z0-9_-]+)", bframe_url)
                    if _km:
                        sitekey = _km.group(1)
                        logger.debug(f"[job={job_id}] Extracted sitekey from bframe URL: {sitekey[:12]}...")

                # Helper: inject g-recaptcha-response token into main page + fire callbacks
                def _build_inject_js(token: str) -> str:
                    return (
                        f"(function(tk){{\n"
                        f"  var ta=document.querySelector('textarea[name=\"g-recaptcha-response\"],#g-recaptcha-response');\n"
                        f"  if(ta){{try{{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,tk);}}catch(e){{ta.value=tk;}}\n"
                        f"    ta.dispatchEvent(new Event('input',{{bubbles:true}}));\n"
                        f"    ta.dispatchEvent(new Event('change',{{bubbles:true}}));}}\n"
                        f"  try{{var cfg=window.___grecaptcha_cfg;\n"
                        f"    if(cfg&&cfg.clients){{Object.values(cfg.clients).forEach(function(c){{\n"
                        f"      Object.values(c||{{}}).forEach(function(v){{\n"
                        f"        if(v&&v.callback)v.callback(tk);\n"
                        f"        else if(v&&typeof v==='object')Object.values(v).forEach(function(v2){{if(v2&&v2.callback)v2.callback(tk);}});\n"
                        f"      }});}});}}}}\n"
                        f"  catch(e){{}}\n"
                        f"  return (ta&&ta.value===tk)||false;\n"
                        f"}})({token!r})"
                    )

                # 2. Create isolated world in bframe → get executionContextId for JS eval
                isolated = await cdp.send.Page.createIsolatedWorld(
                    params={"frameId": bframe_frame_id, "worldName": "bframe_eval", "grantUniversalAccess": True},
                    session_id=session_id,
                )
                # Use list so nested functions can mutate it (nonlocal alternative)
                _ctx = [isolated["executionContextId"]]

                async def beval(js: str):
                    """Evaluate JS expression in bframe context via CDP Runtime.evaluate."""
                    try:
                        result = await cdp.send.Runtime.evaluate(
                            params={"expression": js, "contextId": _ctx[0], "returnByValue": True, "awaitPromise": False},
                            session_id=session_id,
                        )
                        return result.get("result", {}).get("value")
                    except Exception as _e:
                        logger.warning(f"[job={job_id}] beval error: {_e}")
                        return None

                async def refresh_bframe_ctx():
                    """Re-detect bframe frameId + recreate isolated world (handles bframe navigation)."""
                    try:
                        ft = await cdp.send.Page.getFrameTree(params={}, session_id=session_id)
                        bf = _find_frame(ft.get("frameTree", {}), lambda f: "bframe" in (f.get("url") or ""))
                        if bf:
                            if bf["id"] != bframe_frame_id:
                                logger.info(f"[job={job_id}] bframe navigated: new frameId={bf['id']!r}")
                            iso = await cdp.send.Page.createIsolatedWorld(
                                params={"frameId": bf["id"], "worldName": "bframe_eval", "grantUniversalAccess": True},
                                session_id=session_id,
                            )
                            _ctx[0] = iso["executionContextId"]
                            logger.debug(f"[job={job_id}] bframe ctx refreshed: ctxId={_ctx[0]}")
                            return True
                    except Exception as _re:
                        logger.warning(f"[job={job_id}] refresh_bframe_ctx error: {_re}")
                    return False

                # Helper: click anchor checkbox via CDP isolated world (JS inside anchor frame)
                async def _trigger_anchor_click():
                    """Click reCAPTCHA anchor checkbox via CDP mouse events (coordinate-based).
                    
                    JS isolated world approach fails because anchor element may not be
                    found via querySelector in cross-origin OOPIF context.
                    CDP mouse dispatch at viewport coordinates routes correctly to any iframe.
                    """
                    try:
                        # Scroll anchor iframe into viewport center first (handles post-wait layout shifts)
                        try:
                            await cdp.send.Runtime.evaluate(
                                params={
                                    "expression": (
                                        "(function(){"
                                        "var f=document.querySelector('iframe[src*=\"anchor\"]');"
                                        "if(f){f.scrollIntoView({block:'center',inline:'center',behavior:'instant'});return true;}"
                                        "return false;})()"
                                    ),
                                    "returnByValue": True,
                                },
                                session_id=session_id,
                            )
                            await asyncio.sleep(0.3)
                        except Exception:
                            pass
                        # Get anchor iframe rect from main page DOM (no contextId = main page)
                        rect_r = await cdp.send.Runtime.evaluate(
                            params={
                                "expression": (
                                    "(function(){"
                                    "var f=document.querySelector('iframe[src*=\"anchor\"]');"
                                    "if(!f)return null;"
                                    "var r=f.getBoundingClientRect();"
                                    "var vw=window.innerWidth,vh=window.innerHeight;"
                                    "return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height,vw:vw,vh:vh,scrollX:window.scrollX,scrollY:window.scrollY});"
                                    "})()"
                                ),
                                "returnByValue": True,
                            },
                            session_id=session_id,
                        )
                        rect_str = rect_r.get("result", {}).get("value")
                        if not rect_str:
                            logger.warning(f"[job={job_id}] anchor iframe not found on main page")
                            return False
                        rect = _json.loads(rect_str)
                        vw = float(rect.get("vw", 1920))
                        vh = float(rect.get("vh", 900))
                        ix = float(rect.get("x", 0))
                        iy = float(rect.get("y", 0))
                        iw = float(rect.get("w", 300))
                        ih = float(rect.get("h", 74))
                        # reCAPTCHA checkbox is ~28px from left of anchor iframe, vertically centered
                        cx = ix + 28.0
                        cy = iy + ih / 2.0
                        logger.info(f"[job={job_id}] Anchor rect=({ix:.0f},{iy:.0f},{iw:.0f}x{ih:.0f}) viewport=({vw:.0f}x{vh:.0f}) click=({cx:.0f},{cy:.0f})")
                        # Warn if click position is outside viewport (click won't register)
                        if cx < 0 or cx > vw or cy < 0 or cy > vh:
                            logger.warning(f"[job={job_id}] Anchor click position ({cx:.0f},{cy:.0f}) is OUTSIDE viewport ({vw:.0f}x{vh:.0f})!")
                        await cdp.send.Input.dispatchMouseEvent(
                            params={"type": "mouseMoved", "x": cx, "y": cy, "button": "none", "clickCount": 0},
                            session_id=session_id,
                        )
                        await asyncio.sleep(0.1)
                        await cdp.send.Input.dispatchMouseEvent(
                            params={"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1},
                            session_id=session_id,
                        )
                        await asyncio.sleep(0.1)
                        await cdp.send.Input.dispatchMouseEvent(
                            params={"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1},
                            session_id=session_id,
                        )
                        return True
                    except Exception as _ae:
                        logger.warning(f"[job={job_id}] _trigger_anchor_click error: {_ae}")
                        return False

                async def _check_silent_pass() -> str:
                    """Check if reCAPTCHA passed silently (score-based, no image challenge shown).
                    Returns g-recaptcha-response token if present, else empty string."""
                    try:
                        _r = await cdp.send.Runtime.evaluate(
                            params={
                                "expression": "(function(){var ta=document.querySelector('textarea[name=\"g-recaptcha-response\"],#g-recaptcha-response');return ta&&ta.value&&ta.value.length>20?ta.value:'';})()",
                                "returnByValue": True,
                            },
                            session_id=session_id,
                        )
                        return _r.get("result", {}).get("value") or ""
                    except Exception:
                        return ""

                # 3. Start background token solve IMMEDIATELY (runs in parallel with anchor click loop)
                # No apiDomain — field unsupported for ReCaptchaV2TaskProxyLess, causes ERROR_INVALID_TASK_DATA
                _GRID_SEL = "#rc-imageselect-target table, table.rc-imageselect-table, #rc-imageselect, .rc-imageselect"
                _token_bg_task = None
                if sitekey:
                    if sitekey in _bg_token_skip_sitekeys:
                        logger.info(f"[job={job_id}] BG token solve skipped (sitekey previously failed with INVALID_TASK_DATA)")
                    else:
                        async def _bg_token_solve():
                            try:
                                _cs = capsolver  # use job's proxy (if any) for better score
                                logger.info(f"[job={job_id}] BG token solve: pageUrl={page_url!r} sitekey={sitekey[:12]}...")
                                return await _cs.solve_recaptcha_v2(page_url, sitekey)
                            except Exception as _bte:
                                if "ERROR_INVALID_TASK_DATA" in str(_bte):
                                    _bg_token_skip_sitekeys.add(sitekey)
                                    logger.warning(f"[job={job_id}] BG token solve INVALID_TASK_DATA — sitekey {sitekey[:12]}... added to skip list")
                                else:
                                    logger.warning(f"[job={job_id}] BG token solve failed: {_bte}")
                                return ""
                        _token_bg_task = asyncio.ensure_future(_bg_token_solve())

                _recaptcha_reset_count = 0  # allow up to 3 resets per attempt
                _grid_appeared = False

                # If challenge already visible from previous attempt, skip anchor click entirely
                _pre_grid = await beval(f"!!document.querySelector('{_GRID_SEL}')")
                if _pre_grid:
                    logger.info(f"[job={job_id}] Challenge grid already visible — skipping anchor click, proceeding to classification")
                    if _token_bg_task and not _token_bg_task.done():
                        _token_bg_task.cancel()
                    _grid_appeared = True
                else:
                    await _trigger_anchor_click()
                    await asyncio.sleep(2)

                    # Check for silent pass (reCAPTCHA may pass without showing image challenge)
                    _silent_token = await _check_silent_pass()
                    if _silent_token:
                        logger.info(f"[job={job_id}] reCAPTCHA SILENT PASS detected (token {len(_silent_token)} chars)")
                        if _token_bg_task and not _token_bg_task.done():
                            _token_bg_task.cancel()
                        if user_id and _site_host:
                            add_captcha_success(user_id, _site_host, "recaptcha_v2_image_challenge", sitekey=sitekey or "", tips=["Silent pass (no challenge needed)"])
                        return ActionResult(
                            extracted_content=(
                                "reCAPTCHA v2 solved silently (score-based, no image challenge). "
                                "g-recaptcha-response is set. NOW click the Apply/Submit button immediately."
                            ),
                            include_in_memory=True,
                        )

                for _gw in range(150):  # 30s max
                    # Check if background token solve completed
                    if _token_bg_task is not None and _token_bg_task.done():
                        try:
                            _early_token = _token_bg_task.result()
                            if _early_token and len(_early_token) > 20:
                                logger.info(f"[job={job_id}] BG token solve SUCCESS ({len(_early_token)} chars), injecting early")
                                _inj_ok = await page.evaluate(f"() => {{ {_build_inject_js(_early_token)} }}")
                                logger.info(f"[job={job_id}] Early token injected, textarea match={_inj_ok}")
                                if user_id and _site_host:
                                    add_captcha_success(user_id, _site_host, "recaptcha_v2_image_challenge", sitekey=sitekey, tips=["Token-based solve (parallel)"])
                                return ActionResult(
                                    extracted_content=(
                                        f"reCAPTCHA v2 token obtained ({len(_early_token)} chars) and injected. "
                                        "NOW click the Apply/Submit button immediately (token expires in 2 minutes)."
                                    ),
                                    include_in_memory=True,
                                )
                        except Exception:
                            pass
                        _token_bg_task = None  # Task done but failed — clear it

                    grid_exists = await beval(f"!!document.querySelector('{_GRID_SEL}')")
                    if grid_exists:
                        _grid_appeared = True
                        break
                    # Every 5s: refresh ctx + re-check bframe state
                    if _gw > 0 and _gw % 25 == 0:
                        # Check silent pass again (might take a few seconds after click)
                        _sp = await _check_silent_pass()
                        if _sp:
                            logger.info(f"[job={job_id}] reCAPTCHA SILENT PASS (delayed) at {_gw*0.2:.0f}s")
                            if _token_bg_task and not _token_bg_task.done():
                                _token_bg_task.cancel()
                            if user_id and _site_host:
                                add_captcha_success(user_id, _site_host, "recaptcha_v2_image_challenge", sitekey=sitekey or "", tips=["Silent pass (delayed)"])
                            return ActionResult(
                                extracted_content=(
                                    "reCAPTCHA v2 solved silently (score-based, no image challenge). "
                                    "g-recaptcha-response is set. NOW click the Apply/Submit button immediately."
                                ),
                                include_in_memory=True,
                            )
                        await refresh_bframe_ctx()
                        _bstate = await beval(
                            "(function(){var h=document.body?document.body.innerHTML:'';return h.indexOf('rc-imageselect')>=0?'challenge':h.indexOf('finput')>=0?'finput':'other';})()"
                        )
                        logger.info(f"[job={job_id}] Grid wait {_gw*0.2:.0f}s: bframe_state={_bstate}")
                        if _bstate == "finput":
                            # Reset up to 3 times per attempt (at ~5s, 15s, 25s)
                            if _recaptcha_reset_count < 3:
                                logger.info(f"[job={job_id}] Attempting grecaptcha.reset() to refresh session")
                                try:
                                    _reset_js = (
                                        "(function(){"
                                        "try{"
                                        "  var cfg=window.___grecaptcha_cfg;"
                                        "  if(cfg&&cfg.clients){"
                                        "    var ids=Object.keys(cfg.clients);"
                                        "    ids.forEach(function(k){try{grecaptcha.reset(parseInt(k));}catch(e){}});"
                                        "    return 'reset:'+ids.length+' widgets';"
                                        "  } else if(window.grecaptcha&&grecaptcha.reset){"
                                        "    grecaptcha.reset();"
                                        "    return 'reset:default';"
                                        "  }"
                                        "}catch(e){return 'reset_error:'+e.message;}"
                                        "return 'no_grecaptcha';})()"
                                    )
                                    _reset_result = await cdp.send.Runtime.evaluate(
                                        params={"expression": _reset_js, "returnByValue": True},
                                        session_id=session_id,
                                    )
                                    logger.info(f"[job={job_id}] grecaptcha.reset() #{_recaptcha_reset_count+1} result: {_reset_result.get('result',{}).get('value')}")
                                    _recaptcha_reset_count += 1
                                    await asyncio.sleep(3)  # give bframe time to reinitialize
                                    await refresh_bframe_ctx()
                                except Exception as _re:
                                    logger.warning(f"[job={job_id}] grecaptcha.reset() error: {_re}")
                            await _trigger_anchor_click()
                            await asyncio.sleep(1.5)
                    await asyncio.sleep(0.2)

                if not _grid_appeared:
                    # Grid never appeared after 30s — wait for background token solve (up to 90s)
                    if _token_bg_task is not None and not _token_bg_task.done():
                        logger.info(f"[job={job_id}] No challenge after 30s — awaiting background token solve (up to 90s)")
                        try:
                            _rc_token = await asyncio.wait_for(_token_bg_task, timeout=90.0)
                            if _rc_token and len(_rc_token) > 20:
                                logger.info(f"[job={job_id}] Got reCAPTCHA v2 token ({len(_rc_token)} chars), injecting...")
                                _inj_ok = await page.evaluate(f"() => {{ {_build_inject_js(_rc_token)} }}")
                                logger.info(f"[job={job_id}] Token injected, textarea match={_inj_ok}")
                                if user_id and _site_host:
                                    add_captcha_success(user_id, _site_host, "recaptcha_v2_image_challenge", sitekey=sitekey, tips=["Token-based solve (ReCaptchaV2TaskProxyLess)"])
                                return ActionResult(
                                    extracted_content=(
                                        f"reCAPTCHA v2 token obtained ({len(_rc_token)} chars) and injected. "
                                        "NOW click the Apply/Submit button immediately (token expires in 2 minutes)."
                                    ),
                                    include_in_memory=True,
                                )
                        except asyncio.TimeoutError:
                            logger.warning(f"[job={job_id}] Background token solve timed out (90s)")
                        except Exception as _tok_e:
                            logger.warning(f"[job={job_id}] Token-based solve failed ({_tok_e})")
                    elif _token_bg_task is None:
                        if sitekey and sitekey in _bg_token_skip_sitekeys:
                            logger.info(f"[job={job_id}] No challenge after 30s — BG token skipped (INVALID_TASK_DATA sitekey), returning partial immediately")
                        else:
                            logger.warning(f"[job={job_id}] No sitekey available for token solve")
                    # Last resort: check silent pass one more time
                    _sp = await _check_silent_pass()
                    if _sp:
                        logger.info(f"[job={job_id}] reCAPTCHA SILENT PASS (final check) token={len(_sp)} chars")
                        return ActionResult(
                            extracted_content="reCAPTCHA v2 solved silently. g-recaptcha-response is set. Click Submit now.",
                            include_in_memory=True,
                        )
                    logger.warning(f"[job={job_id}] No challenge grid and token solve failed — returning partial result")
                    return ActionResult(
                        extracted_content=(
                            "reCAPTCHA v2 challenge did not appear after 30s and token solve failed. "
                            "Try scrolling to the reCAPTCHA widget and clicking 'I'm not a robot' manually, then call solve_captcha_auto again."
                        ),
                        include_in_memory=True,
                    )
                else:
                    # Grid appeared — cancel background token solve to save credits
                    if _token_bg_task and not _token_bg_task.done():
                        _token_bg_task.cancel()

                _Q_JS = (
                    "(function(){"
                    # Try multiple selectors — reCAPTCHA UI varies across versions
                    "var els=["
                    "document.querySelector('#rc-imageselect-desc-no-canonical strong'),"
                    "document.querySelector('#rc-imageselect-desc strong'),"
                    "document.querySelector('.rc-imageselect-desc-wrapper strong'),"
                    "document.querySelector('.rc-imageselect-desc strong'),"
                    "document.querySelector('.rc-imageselect-instructions strong'),"
                    "document.querySelector('#rc-imageselect-desc-no-canonical'),"
                    "document.querySelector('#rc-imageselect-desc'),"
                    "document.querySelector('.rc-imageselect-desc-wrapper'),"
                    "document.querySelector('.rc-imageselect-instructions')];"
                    "for(var i=0;i<els.length;i++){"
                    "  if(els[i]){"
                    "    var t=(els[i].innerText||els[i].textContent||'').trim().toLowerCase();"
                    "    if(t)return t;"
                    "  }"
                    "}"
                    "return '';}"
                    ")()"
                )

                # 4. Wait for question text to be non-empty (up to 8s)
                question_text = ""
                for _ in range(40):
                    question_text = await beval(_Q_JS) or ""
                    if question_text:
                        break
                    await asyncio.sleep(0.2)

                # Debug: if still empty, log bframe HTML to understand structure
                if not question_text:
                    _debug_html = await beval(
                        "(function(){var b=document.body;return b?(b.innerHTML||b.textContent||'').substring(0,800):'no-body';})()"
                    )
                    logger.info(f"[job={job_id}] bframe HTML dump: {_debug_html!r}")
                    # Also try title/lang to confirm we're inside bframe
                    _debug_title = await beval("document.title||document.documentElement.lang||'no-title'")
                    logger.info(f"[job={job_id}] bframe title/lang: {_debug_title!r}")

                _q_normalized = _normalize_qtext(question_text)
                question_label = _RECAPTCHA_QMAP.get(_q_normalized, _q_normalized) or question_text
                logger.info(f"[job={job_id}] bframe question: '{question_text}' → norm='{_q_normalized}' label={question_label!r}")

                async def capture_grid_b64() -> str:
                    """Capture captcha grid by extracting tile image URLs from bframe DOM.
                    
                    CDP captureScreenshot cannot capture cross-origin iframe content (OOPIF).
                    Instead, get tile image URLs from bframe DOM and fetch+stitch via Python.
                    """
                    import aiohttp as _aiohttp
                    from PIL import Image as _PIL_Image
                    import io as _io

                    # Get tile info from bframe DOM
                    _TILE_JS = (
                        "(function(){"
                        "var result={tiles:[],cols:3,rows:3};"
                        "if(document.querySelector('.rc-imageselect-table-44'))result.cols=result.rows=4;"
                        "else if(document.querySelector('.rc-imageselect-table-34'))result.cols=4;"
                        "var tiles=document.querySelectorAll('.rc-imageselect-tile,td.rc-imageselect-tile');"
                        "if(!tiles.length)tiles=document.querySelectorAll('#rc-imageselect-target td');"
                        "for(var i=0;i<tiles.length;i++){"
                        "  var img=tiles[i].querySelector('img');"
                        "  if(img)result.tiles.push({src:img.src||img.currentSrc||'',w:img.naturalWidth,h:img.naturalHeight});"
                        "}"
                        "if(!result.tiles.length){"
                        "  var imgs=document.querySelectorAll('.rc-image-tile-33,.rc-image-tile-44,.rc-image-tile-22,.rc-image-tile-wrapper img,#rc-imageselect-target img');"
                        "  for(var j=0;j<imgs.length;j++)result.tiles.push({src:imgs[j].src||imgs[j].currentSrc||'',w:imgs[j].naturalWidth,h:imgs[j].naturalHeight});"
                        "}"
                        "return JSON.stringify(result);"
                        "})()"
                    )
                    tile_info_str = await beval(_TILE_JS)
                    try:
                        tile_info = _json.loads(tile_info_str) if tile_info_str else {}
                    except Exception:
                        tile_info = {}

                    tiles = tile_info.get("tiles", [])
                    cols = tile_info.get("cols", 3)
                    rows = tile_info.get("rows", 3)

                    valid_tiles = [t for t in tiles if t.get("src") and t["src"].startswith("http")]
                    logger.info(f"[job={job_id}] bframe tiles found={len(valid_tiles)}, cols={cols}, rows={rows}")

                    if not valid_tiles:
                        logger.warning(f"[job={job_id}] No tile image URLs in bframe DOM")
                        return ""

                    # Check if single large image (all tiles same src) or multiple
                    unique_srcs = list(dict.fromkeys(t["src"] for t in valid_tiles))
                    logger.info(f"[job={job_id}] unique tile srcs={len(unique_srcs)} first={unique_srcs[0][:80]!r}")

                    try:
                        _headers = {
                            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                            "Referer": "https://recaptcha.net/",
                        }
                        async with _aiohttp.ClientSession(headers=_headers) as _sess:
                            if len(unique_srcs) == 1:
                                # Single full-grid image
                                async with _sess.get(unique_srcs[0], timeout=_aiohttp.ClientTimeout(total=12)) as _resp:
                                    if _resp.status == 200:
                                        _img_bytes = await _resp.read()
                                        logger.info(f"[job={job_id}] Downloaded single grid image: {len(_img_bytes)} bytes")
                                        return base64.b64encode(_img_bytes).decode()
                                    logger.warning(f"[job={job_id}] Grid image download status={_resp.status}")
                            else:
                                # Multiple tile images — download and stitch
                                _tile_imgs = []
                                for _t in valid_tiles[:cols * rows]:
                                    try:
                                        async with _sess.get(_t["src"], timeout=_aiohttp.ClientTimeout(total=8)) as _resp:
                                            if _resp.status == 200:
                                                _tile_imgs.append(_PIL_Image.open(_io.BytesIO(await _resp.read())).convert("RGB"))
                                            else:
                                                _tile_imgs.append(None)
                                    except Exception as _te:
                                        logger.warning(f"[job={job_id}] Tile download failed: {_te}")
                                        _tile_imgs.append(None)

                                _valid_tile = next((t for t in _tile_imgs if t is not None), None)
                                if not _valid_tile:
                                    return ""
                                _tw, _th = _valid_tile.size
                                _grid = _PIL_Image.new("RGB", (_tw * cols, _th * rows), (255, 255, 255))
                                for _idx, _ti in enumerate(_tile_imgs):
                                    if _ti:
                                        _grid.paste(_ti, ((_idx % cols) * _tw, (_idx // cols) * _th))
                                _buf = _io.BytesIO()
                                _grid.save(_buf, format="PNG", optimize=True)
                                _result = base64.b64encode(_buf.getvalue()).decode()
                                logger.info(f"[job={job_id}] Stitched {len(_tile_imgs)} tiles into {_tw*cols}x{_th*rows} grid ({len(base64.b64decode(_result))} bytes)")
                                return _result
                    except Exception as _fe:
                        logger.warning(f"[job={job_id}] capture_grid_b64 fetch error: {_fe}")
                    return ""


                all_clicked: list = []
                MAX_ROUNDS = 8

                for _round in range(MAX_ROUNDS):
                    if _round > 0:
                        # Refresh bframe context (handles navigation after reload/Verify)
                        await refresh_bframe_ctx()
                        # Re-read question after reload (wait up to 6s for new question)
                        for _qw in range(30):
                            _qt = await beval(_Q_JS) or ""
                            if _qt:
                                question_text = _qt
                                break
                            await asyncio.sleep(0.2)
                        _q_normalized = _normalize_qtext(question_text)
                        question_label = _RECAPTCHA_QMAP.get(_q_normalized, _q_normalized) or question_text

                    # Screenshot grid
                    try:
                        b64_img = await capture_grid_b64()
                    except Exception as _se:
                        logger.warning(f"[job={job_id}] Round {_round} screenshot failed: {_se}")
                        break

                    # Debug: log image size to confirm not blank
                    img_bytes_len = len(base64.b64decode(b64_img)) if b64_img else 0
                    logger.info(f"[job={job_id}] Round {_round}: screenshot size={img_bytes_len} bytes, question={question_label!r}")
                    # Save screenshot for visual inspection (first round only)
                    if _round == 0 and b64_img:
                        _ss_path = Path("/tmp") / f"captcha_job{job_id}_r{_round}.jpg"
                        _ss_path.write_bytes(base64.b64decode(b64_img))
                        logger.info(f"[job={job_id}] Saved screenshot: {_ss_path}")

                    if not question_label:
                        logger.info(f"[job={job_id}] Round {_round}: empty question → reload")
                        await beval("(function(){var b=document.querySelector('#recaptcha-reload-button');if(b)b.click();})()")
                        await asyncio.sleep(1.5)
                        continue

                    # ReCaptchaV2Classification — sync ~1-2s (retry up to 3x on transient errors)
                    sol = None
                    for _cap_try in range(3):
                        try:
                            sol = await capsolver.solve_recaptcha_v2_classification(
                                image_base64=b64_img,
                                question=question_label,
                                website_url=page_url,
                                website_key=sitekey,
                            )
                            break
                        except Exception as _ce:
                            logger.warning(f"[job={job_id}] Round {_round} classification attempt {_cap_try+1}/3 failed: {_ce}")
                            if _cap_try < 2:
                                await asyncio.sleep(2)
                            else:
                                logger.warning(f"[job={job_id}] Round {_round} classification failed after 3 tries, continuing")
                    if sol is None:
                        continue

                    objects = sol.get("objects") or []
                    sol_type = sol.get("type") or "multi"
                    has_obj = sol.get("hasObject")
                    logger.info(f"[job={job_id}] Round {_round}: type={sol_type} objects={objects} hasObject={has_obj}")

                    # ── single image (yes/no) ─────────────────────────────────
                    if sol_type == "single":
                        if has_obj:
                            await beval("(function(){var img=document.querySelector('.rc-image-tile-wrapper img,#rc-imageselect img');if(img)img.click();})()")
                            all_clicked.append("single-img")
                            await asyncio.sleep(0.5)
                        await beval("(function(){var b=document.querySelector('#recaptcha-verify-button');if(b)b.click();})()")
                        return ActionResult(
                            extracted_content=f"reCAPTCHA single-image: hasObject={has_obj} → Verify clicked.",
                            include_in_memory=True,
                        )

                    # ── multi-tile grid ───────────────────────────────────────
                    if not objects:
                        logger.info(f"[job={job_id}] Round {_round}: no objects → done")
                        break

                    tile_count_raw = await beval("document.querySelectorAll('td.rc-imageselect-tile').length")
                    tile_count = int(tile_count_raw) if tile_count_raw is not None else 0

                    # Get bframe iframe viewport position for CDP mouse events (isTrusted clicks)
                    _bfr_r = await cdp.send.Runtime.evaluate(
                        params={
                            "expression": (
                                "(function(){"
                                "var f=document.querySelector('iframe[src*=\"bframe\"]');"
                                "if(!f)return null;"
                                "var r=f.getBoundingClientRect();"
                                "return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height});"
                                "})()"
                            ),
                            "returnByValue": True,
                        },
                        session_id=session_id,
                    )
                    _bfr_vp = _json.loads(_bfr_r.get("result", {}).get("value") or "null")

                    # Snapshot current tile srcs BEFORE clicking (for dynamic tile detection)
                    _tile_src_js = "(function(){var t=document.querySelectorAll('td.rc-imageselect-tile img');var s=[];for(var i=0;i<t.length;i++)s.push(t[i].src||'');return JSON.stringify(s);})()"
                    _pre_srcs_raw = await beval(_tile_src_js)
                    _pre_srcs = set(_json.loads(_pre_srcs_raw)) if _pre_srcs_raw else set()

                    round_clicked = []
                    for idx in objects:
                        if 0 <= idx < tile_count:
                            # Get tile rect within bframe DOM
                            _tile_rect_str = await beval(
                                f"(function(){{var t=document.querySelectorAll('td.rc-imageselect-tile');"
                                f"if({idx}<t.length){{var r=t[{idx}].getBoundingClientRect();"
                                f"return JSON.stringify({{x:r.x,y:r.y,w:r.width,h:r.height}});}}return null;}})()"
                            )
                            _tile_rect = _json.loads(_tile_rect_str) if _tile_rect_str else None
                            _click_ok = False
                            if _bfr_vp and _tile_rect:
                                _tx = float(_bfr_vp["x"]) + float(_tile_rect["x"]) + float(_tile_rect["w"]) / 2
                                _ty = float(_bfr_vp["y"]) + float(_tile_rect["y"]) + float(_tile_rect["h"]) / 2
                                logger.info(f"[job={job_id}] CDP click tile {idx} at ({_tx:.0f},{_ty:.0f})")
                                try:
                                    await cdp.send.Input.dispatchMouseEvent(
                                        params={"type": "mouseMoved", "x": _tx, "y": _ty, "button": "none", "clickCount": 0},
                                        session_id=session_id,
                                    )
                                    await asyncio.sleep(0.05)
                                    await cdp.send.Input.dispatchMouseEvent(
                                        params={"type": "mousePressed", "x": _tx, "y": _ty, "button": "left", "clickCount": 1},
                                        session_id=session_id,
                                    )
                                    await asyncio.sleep(0.05)
                                    await cdp.send.Input.dispatchMouseEvent(
                                        params={"type": "mouseReleased", "x": _tx, "y": _ty, "button": "left", "clickCount": 1},
                                        session_id=session_id,
                                    )
                                    _click_ok = True
                                except Exception as _ce2:
                                    logger.warning(f"[job={job_id}] CDP tile click error: {_ce2}")
                            if not _click_ok:
                                # Fallback: JS click in bframe isolated world
                                _click_ok = bool(await beval(
                                    f"(function(){{var t=document.querySelectorAll('td.rc-imageselect-tile');"
                                    f"if({idx}<t.length){{t[{idx}].click();return true;}}return false;}})()"
                                ))
                            if _click_ok:
                                round_clicked.append(idx)
                                all_clicked.append(idx)
                                await asyncio.sleep(0.4)

                    logger.info(f"[job={job_id}] Round {_round}: clicked {round_clicked} / {tile_count} tiles")

                    if not round_clicked:
                        break

                    # Single-image grid (all tiles share 1 src): no dynamic tiles will appear.
                    # Click Verify immediately after selecting all matching tiles.
                    _is_single_image_grid = len(_pre_srcs) <= 1
                    if _is_single_image_grid:
                        logger.info(f"[job={job_id}] Round {_round}: single-image grid → clicking Verify immediately")
                        break

                    # Wait up to 4s for dynamic tiles to replace clicked ones
                    for _dw in range(20):
                        await asyncio.sleep(0.2)
                        _cur_srcs_raw = await beval(_tile_src_js)
                        if _cur_srcs_raw:
                            try:
                                _cur_srcs = set(_json.loads(_cur_srcs_raw))
                                if _cur_srcs - _pre_srcs:
                                    logger.info(f"[job={job_id}] Dynamic tiles appeared after {(_dw+1)*0.2:.1f}s")
                                    break
                            except Exception:
                                pass
                    else:
                        await asyncio.sleep(0.4)

                    still_open = await beval("!!document.querySelector('#recaptcha-verify-button')")
                    if not still_open:
                        logger.info(f"[job={job_id}] Challenge auto-closed after round {_round}")
                        return ActionResult(
                            extracted_content=f"reCAPTCHA image grid solved automatically after round {_round+1}. Submit form now.",
                            include_in_memory=True,
                        )

                # Click Verify
                await beval("(function(){var b=document.querySelector('#recaptcha-verify-button');if(b)b.click();})()")
                logger.info(f"[job={job_id}] Verify clicked after {len(all_clicked)} total tile clicks")

                # Poll main page for g-recaptcha-response token (up to 12s)
                token_len = 0
                for _pt in range(24):
                    await asyncio.sleep(0.5)
                    try:
                        _tr = await page.evaluate(
                            "() => { var r=document.querySelector('textarea#g-recaptcha-response,textarea[name=\"g-recaptcha-response\"]'); return r?r.value:''; }"
                        )
                        if _tr and len(_tr) > 20:
                            token_len = len(_tr)
                            break
                    except Exception:
                        pass

                if token_len:
                    logger.info(f"[job={job_id}] reCAPTCHA token confirmed ({token_len} chars)")
                    # Save success to memory
                    if user_id and _site_host:
                        tips = [
                            f"Dùng CDP createIsolatedWorld để access bframe: '{question_text}' → {len(all_clicked)} tiles clicked",
                            "Sau Verify, đợi 0.5-2s rồi click nút Apply/Submit ngay",
                        ]
                        add_captcha_success(user_id, _site_host, "recaptcha_v2_image_challenge", sitekey=sitekey, tips=tips)
                    return ActionResult(
                        extracted_content=(
                            f"reCAPTCHA image solved! Token confirmed ({token_len} chars). "
                            f"'{question_text}' → {len(all_clicked)} tiles clicked. "
                            "NOW click the Apply/Submit button immediately (ĐỪNG chờ lâu — token hết hạn trong 2 phút)."
                        ),
                        include_in_memory=True,
                    )

                # Token not seen — still return success message
                return ActionResult(
                    extracted_content=(
                        f"reCAPTCHA image challenge: '{question_text}' → {len(all_clicked)} tiles clicked → Verify clicked. "
                        "Wait 1-2s then click Apply/Submit button."
                    ),
                    include_in_memory=True,
                )

            except Exception as _top_e:
                logger.error(f"[job={job_id}] _solve_image_challenge error: {_top_e}", exc_info=True)
                return ActionResult(
                    extracted_content=f"reCAPTCHA solve error: {_top_e}. Try solve_captcha_auto or reload page.",
                    include_in_memory=True,
                )

        # ─────────────────────────────────────────────────────────────────────

        @tools.action(
            description=(
                "AUTO-SOLVE bất kỳ CAPTCHA nào trên trang hiện tại — KHÔNG cần tham số. "
                "Tool tự scan DOM tìm Turnstile / reCAPTCHA v2/v3 / hCaptcha / FunCaptcha / "
                "GeeTest v4 / DataDome / Cloudflare interstitial / AWS WAF, lấy sitekey, gọi "
                "CapSolver giải, inject token/cookie và trigger callback. "
                "DÙNG TOOL NÀY TRƯỚC — chỉ fallback các tool solve_* riêng nếu auto thất bại."
            )
        )
        async def solve_captcha_auto(browser_session) -> ActionResult:
            try:
                page = await browser_session.get_current_page()
                page_url = await page.get_url()
                info = await detect_captcha_on_page(page)
                if not info:
                    return ActionResult(
                        extracted_content="No captcha detected on current page.",
                        include_in_memory=True,
                    )
                ctype = info.get("type") or ""
                logger.info(f"[job={job_id}] auto-detect captcha={ctype} info={info}")

                if ctype == "turnstile":
                    sk = info.get("sitekey") or ""
                    if not sk:
                        return ActionResult(extracted_content="Turnstile detected nhưng không tìm được sitekey.", include_in_memory=True)
                    logger.info(f"[job={job_id}] Turnstile → CapSolver-first strategy, sitekey={sk}")
                    # Step 1: reset widget nếu nó đang ở trạng thái 'Verification failed'.
                    try:
                        await page.evaluate(
                            "() => { try { if (window.turnstile && typeof window.turnstile.reset === 'function') window.turnstile.reset(); } catch(e) {} return true; }"
                        )
                    except Exception as e:
                        logger.warning(f"[job={job_id}] turnstile.reset() failed: {e}")
                    # Step 2: solve qua CapSolver → token + userAgent.
                    res = await capsolver.solve_turnstile(page_url, sk, info.get("action") or "", info.get("cdata") or "")
                    token = res["token"]
                    ua = (res.get("user_agent") or "").strip()
                    # Step 3: override UA qua CDP để token match server-side validation.
                    if ua:
                        try:
                            cdp_session = await browser_session.get_or_create_cdp_session(target_id=None)
                            await cdp_session.cdp_client.send.Network.setUserAgentOverride(
                                params={"userAgent": ua},
                                session_id=cdp_session.session_id,
                            )
                            logger.info(f"[job={job_id}] Turnstile UA override OK: {ua[:60]}...")
                        except Exception as ue:
                            logger.warning(f"[job={job_id}] Turnstile UA override failed: {ue}")
                    # Step 4: inject token + fire data-callback.
                    await inject_turnstile_token(page, token)
                    return ActionResult(
                        extracted_content=f"Turnstile solved via CapSolver (token len={len(token)}, UA pinned). Click submit ngay.",
                        include_in_memory=True,
                    )

                if ctype == "turnstile_iframe":
                    # Site nhúng Turnstile qua iframe bên thứ 3 (vd: goaffpro creatives,
                    # Webflow embed). Sitekey nằm trong HTML iframe → fetch để lấy.
                    iframe_src = info.get("iframe_src") or ""
                    logger.info(f"[job={job_id}] Turnstile-iframe wrapper detected: {iframe_src}")
                    sk = await fetch_turnstile_sitekey_from_iframe(iframe_src)
                    if not sk:
                        return ActionResult(
                            extracted_content=f"Turnstile iframe detected ({iframe_src}) nhưng không extract được sitekey.",
                            include_in_memory=True,
                        )
                    logger.info(f"[job={job_id}] iframe sitekey={sk} → CapSolver solve")
                    try:
                        res = await capsolver.solve_turnstile(page_url, sk)
                        token = res["token"]
                        ua = (res.get("user_agent") or "").strip()
                        if ua:
                            try:
                                cdp_session = await browser_session.get_or_create_cdp_session(target_id=None)
                                await cdp_session.cdp_client.send.Network.setUserAgentOverride(
                                    params={"userAgent": ua}, session_id=cdp_session.session_id,
                                )
                            except Exception as ue:
                                logger.warning(f"[job={job_id}] iframe UA override failed: {ue}")
                        # inject_turnstile_token đã bao gồm postMessage + global + invoke onTurnstileSuccess
                        await inject_turnstile_token(page, token)
                        return ActionResult(
                            extracted_content=f"Turnstile iframe solved (sitekey={sk}, token len={len(token)}). Click submit now — button should be enabled.",
                            include_in_memory=True,
                        )
                    except Exception as ts_err:
                        err_str = str(ts_err)
                        logger.warning(f"[job={job_id}] CapSolver Turnstile failed: {err_str} — trying direct checkbox click (CloakBrowser stealth)")
                        ok = await click_nested_turnstile_checkbox(page, timeout_ms=15000)
                        if ok:
                            return ActionResult(
                                extracted_content="Turnstile passed via direct checkbox click (CloakBrowser stealth). Click submit now.",
                                include_in_memory=True,
                            )
                        return ActionResult(
                            extracted_content=f"Turnstile failed: CapSolver error ({err_str}) + direct click also failed. Try reload page and retry.",
                            include_in_memory=True,
                        )

                if ctype == "recaptcha_v2_invisible_pending":
                    # Invisible reCAPTCHA: widget not yet triggered.
                    # Strategy A: extract sitekey from page HTML/scripts → CapSolver before submit
                    # Strategy B: if anchor frame visible (after submit click) → extract from frame URL
                    import json as _json

                    def _ev2(r):
                        if isinstance(r, str):
                            try:
                                return _json.loads(r)
                            except Exception:
                                return None
                        return r

                    def _find_frame_url(node, match_fn):
                        frame = node.get("frame", {})
                        if match_fn(frame):
                            return frame.get("url", "")
                        for child in node.get("childFrames", []):
                            r = _find_frame_url(child, match_fn)
                            if r:
                                return r
                        return ""

                    sk = ""
                    _strategy_a_found = False  # track if Strategy A found the sitekey (v2 indicator)

                    # --- Strategy A: search page HTML/scripts/grecaptcha_cfg ---
                    try:
                        sk_raw = _ev2(await page.evaluate(
                            """(dummy) => {
                                try {
                                    var cfg = window.___grecaptcha_cfg;
                                    if (cfg && cfg.clients) {
                                        var ks = Object.keys(cfg.clients);
                                        for (var i = 0; i < ks.length; i++) {
                                            var cl = cfg.clients[ks[i]];
                                            var cks = Object.keys(cl || {});
                                            for (var j = 0; j < cks.length; j++) {
                                                var e = cl[cks[j]];
                                                if (e && e.sitekey && typeof e.sitekey === 'string' &&
                                                        e.sitekey.charAt(0) === '6' && e.sitekey.length > 25) {
                                                    return e.sitekey;
                                                }
                                            }
                                        }
                                    }
                                } catch(ex) {}
                                var scripts = Array.from(document.scripts).filter(function(s) { return !s.src; });
                                for (var si = 0; si < scripts.length; si++) {
                                    var text = scripts[si].textContent || '';
                                    var m = text.match(/['"](6[A-Za-z0-9_-]{29,})['"]/);
                                    if (m) return m[1];
                                }
                                var html = document.documentElement.innerHTML;
                                var m2 = html.match(/(?:sitekey|site_key)['"\s]*[:=]['"\s]*(6[A-Za-z0-9_-]{29,})/);
                                if (m2) return m2[1];
                                return null;
                            }""",
                            True,
                        ))
                        if isinstance(sk_raw, str) and len(sk_raw) > 25 and sk_raw.startswith("6"):
                            sk = sk_raw
                            _strategy_a_found = True
                            logger.info(f"[job={job_id}] invisible reCAPTCHA sitekey found in page source: {sk[:20]}...")
                    except Exception as _ske:
                        logger.warning(f"[job={job_id}] recaptcha invisible sitekey page-search failed: {_ske}")

                    # --- Strategy B: check CDP frame tree for anchor iframe (populated after submit click) ---
                    _anchor_url = ""
                    if not sk:
                        try:
                            _cdp = page._client
                            _sid = await page._ensure_session()
                            _ft = await _cdp.send.Page.getFrameTree(params={}, session_id=_sid)
                            _anchor_url = _find_frame_url(
                                _ft.get("frameTree", {}),
                                lambda f: ("/recaptcha/api2/anchor" in (f.get("url") or "")
                                           or "/recaptcha/enterprise/anchor" in (f.get("url") or "")),
                            )
                            if _anchor_url:
                                _km = re.search(r"[?&]k=([6][A-Za-z0-9_-]{29,})", _anchor_url)
                                if _km:
                                    sk = _km.group(1)
                                    _is_ent = "/recaptcha/enterprise/" in _anchor_url
                                    logger.info(f"[job={job_id}] invisible reCAPTCHA sitekey from anchor frame: {sk[:20]}... enterprise={_is_ent}")
                        except Exception as _fe:
                            logger.warning(f"[job={job_id}] frame tree sitekey search failed: {_fe}")

                    if sk:
                        try:
                            # Determine actual size from anchor URL to pass correct invisible flag
                            _is_invisible = True
                            _is_enterprise = "/recaptcha/enterprise/" in _anchor_url if _anchor_url else False
                            if _anchor_url:
                                _size_m = re.search(r"[?&]size=([^&]+)", _anchor_url)
                                if _size_m:
                                    _size_val = _size_m.group(1)
                                    _is_invisible = _size_val == "invisible"
                                    logger.info(f"[job={job_id}] anchor size={_size_val} → invisible={_is_invisible} enterprise={_is_enterprise}")

                            # Check if this is actually reCAPTCHA v3 (script loaded with render=sitekey OR vue-recaptcha-v3)
                            _is_v3 = False
                            _v3_action = "submit"
                            try:
                                _v3_info = _ev2(await page.evaluate(
                                    """(sk) => {
                                        function _getAction() {
                                            // URL-based detection for known platforms
                                            var href = window.location.href;
                                            if (href.indexOf('firstpromoter.com/signup') >= 0 ||
                                                    href.indexOf('.fprom.co/signup') >= 0 ||
                                                    href.indexOf('fprom.io/signup') >= 0) {
                                                return 'affiliate_signup';
                                            }
                                            // Search inline HTML for executeRecaptcha call
                                            var html = document.documentElement.innerHTML;
                                            var am = html.match(/executeRecaptcha\s*\(\s*['"]([^'"]+)['"]/);
                                            return am ? am[1] : 'submit';
                                        }
                                        var dbg = {rc_scripts: [], v3_attr: false, cfg_clients: -1, nuxt_v3: false};
                                        // Gather all recaptcha script URLs for logging
                                        var allScripts = Array.from(document.scripts);
                                        var allSrc = allScripts.map(function(s) { return s.src || ''; }).join(' ');
                                        dbg.rc_scripts = allScripts
                                            .map(function(s) { return s.src || ''; })
                                            .filter(function(s) { return s.indexOf('recaptcha') >= 0 || s.indexOf('gstatic') >= 0; });
                                        // Check 1: script URL has render=SITEKEY
                                        var m = allSrc.match(/[?&]render=([a-zA-Z0-9_-]{20,})/);
                                        if (m && m[1] !== 'explicit' && m[1] === sk) {
                                            return {is_v3: true, reason: 'render_param', action: _getAction(), dbg: dbg};
                                        }
                                        // Check 2: vue-recaptcha-v3 sets recaptcha-v3-script attribute
                                        var v3Script = document.querySelector('script[recaptcha-v3-script]');
                                        dbg.v3_attr = !!v3Script;
                                        if (v3Script) {
                                            return {is_v3: true, reason: 'v3_script_attr', action: _getAction(), dbg: dbg};
                                        }
                                        // Check 3: ___grecaptcha_cfg.clients length
                                        try {
                                            var cfg = window.___grecaptcha_cfg;
                                            dbg.cfg_clients = cfg && cfg.clients ? Object.keys(cfg.clients).length : 0;
                                        } catch(e3) {}
                                        // Check 4: Nuxt 3 exposes RECAPTCHA_SITE_KEY in window.__NUXT__.config.public
                                        // vue-recaptcha-v3 is standard for Nuxt → always v3
                                        try {
                                            var nuxt = window.__NUXT__;
                                            var nuxtPub = nuxt && nuxt.config && nuxt.config.public;
                                            if (nuxtPub && nuxtPub.RECAPTCHA_SITE_KEY && nuxtPub.RECAPTCHA_SITE_KEY === sk) {
                                                dbg.nuxt_v3 = true;
                                                var act = _getAction();
                                                if (act === 'submit') act = 'affiliate_signup';
                                                return {is_v3: true, reason: 'nuxt_recaptcha_site_key', action: act, dbg: dbg};
                                            }
                                        } catch(e4) {}
                                        return {is_v3: false, dbg: dbg};
                                    }""",
                                    sk,
                                ))
                                logger.info(f"[job={job_id}] v3 JS detect raw: {type(_v3_info).__name__}={str(_v3_info)[:200]}")
                                if isinstance(_v3_info, dict):
                                    _dbg = _v3_info.get("dbg") or {}
                                    logger.info(
                                        f"[job={job_id}] v3 JS detect: reason={_v3_info.get('reason') or 'none'} "
                                        f"| v3_attr={_dbg.get('v3_attr')} | cfg_clients={_dbg.get('cfg_clients')} "
                                        f"| nuxt_v3={_dbg.get('nuxt_v3')} | rc_scripts={_dbg.get('rc_scripts')}"
                                    )
                                    if _v3_info.get("is_v3"):
                                        _is_v3 = True
                                        _v3_action = _v3_info.get("action") or "submit"
                                        logger.info(f"[job={job_id}] detected reCAPTCHA v3 via JS (reason={_v3_info.get('reason')}), action={_v3_action!r}")
                                    elif not _strategy_a_found:
                                        # Heuristic: Strategy A (___grecaptcha_cfg.clients) failed to find sitekey
                                        # but Strategy B (anchor frame) succeeded → likely v3 with lazy loading
                                        _is_v3 = True
                                        _v3_action = _v3_info.get("action") or (_dbg.get("action")) or "submit"
                                        logger.info(
                                            f"[job={job_id}] v3 heuristic: Strategy A miss + anchor hit → v3, action={_v3_action!r}"
                                        )
                                    else:
                                        logger.info(f"[job={job_id}] v3 detect: is_v3=False, _strategy_a_found={_strategy_a_found} → using v2")
                                else:
                                    logger.warning(f"[job={job_id}] v3 detect: _v3_info not dict → fallback to heuristic")
                                    if not _strategy_a_found:
                                        _is_v3 = True
                                        logger.info(f"[job={job_id}] v3 heuristic (non-dict): Strategy A miss → v3")
                            except Exception as _v3_err:
                                logger.warning(f"[job={job_id}] v3 detection check failed: {_v3_err}")
                                if not _strategy_a_found:
                                    # On error, still apply heuristic
                                    _is_v3 = True
                                    logger.info(f"[job={job_id}] v3 heuristic (on error): Strategy A miss → treating as v3")

                            if _is_enterprise:
                                logger.info(f"[job={job_id}] using Enterprise reCAPTCHA solver")
                                token = await capsolver.solve_recaptcha_v2_enterprise(page_url, sk)
                            elif _is_v3:
                                logger.info(f"[job={job_id}] using reCAPTCHA v3 solver, action={_v3_action!r}")
                                token = await capsolver.solve_recaptcha_v3(page_url, sk, action=_v3_action)
                            else:
                                token = await capsolver.solve_recaptcha_v2(page_url, sk, invisible=_is_invisible)
                            # Install fetch/XHR interceptor BEFORE inject_recaptcha_token
                            # so the interceptor wraps window.fetch before callbacks fire
                            # Install fetch/XHR interceptor to lock our CapSolver token in outbound POST requests.
                            # Also captures server response in window.__signup_api_response for debugging.
                            _intercept_js = """(tok) => {
    try {
        // Always update the global token reference (mutable — used inside interceptors)
        window.__capsolver_intercept_token = tok;
        window.__signup_api_response = null;

        // If interceptor is already installed, just update the token — avoid stacking wrappers
        if (window.__capsolver_intercept_installed) {
            return 'updated';
        }
        window.__capsolver_intercept_installed = true;

        // Helper: replace recaptcha-like field in a JSON object recursively (uses global token)
        function _repTok(o) {
            if (!o || typeof o !== 'object') return;
            var _t = window.__capsolver_intercept_token;
            for (var k in o) {
                var kl = typeof k === 'string' ? k.toLowerCase().replace(/-/g,'_') : '';
                if ((typeof o[k] === 'string' || o[k] == null) &&
                        (kl.indexOf('recaptcha') >= 0 || kl.indexOf('captcha') >= 0 || kl === 'token' || kl === 'g_token')) {
                    o[k] = _t;
                } else if (o[k] && typeof o[k] === 'object') {
                    _repTok(o[k]);
                }
            }
        }

        var _oFetch = window.fetch;
        window.fetch = function(url, opts) {
            var _t = window.__capsolver_intercept_token;
            var _opts = opts ? Object.assign({}, opts) : {};
            if (_opts.body) {
                var b = _opts.body;
                if (typeof b === 'string') {
                    try {
                        var j = JSON.parse(b);
                        _repTok(j);
                        _opts.body = JSON.stringify(j);
                    } catch(e2) {
                        // URL-encoded
                        if (b.indexOf('captcha') >= 0 || b.indexOf('recaptcha') >= 0) {
                            _opts.body = b.replace(/([^=&]*(re)?captcha[^=&]*)=([^&]*)/gi,
                                '$1=' + encodeURIComponent(_t));
                        }
                    }
                } else if (b && typeof b.get === 'function') {
                    // URLSearchParams or FormData
                    try {
                        b.forEach(function(v, k) {
                            var kl = k.toLowerCase().replace(/-/g,'_');
                            if (kl.indexOf('captcha') >= 0 || kl.indexOf('recaptcha') >= 0) {
                                b.set(k, _t);
                            }
                        });
                    } catch(e3) {}
                }
            }
            return _oFetch.call(window, url, _opts).then(function(resp) {
                resp.clone().json().then(function(j) {
                    window.__signup_api_response = {url: url, status: resp.status, body: j};
                }).catch(function() {
                    resp.clone().text().then(function(t2) {
                        window.__signup_api_response = {url: url, status: resp.status, body: t2.substring(0, 500)};
                    }).catch(function(){});
                });
                return resp;
            });
        };

        // Override XHR open to capture URL
        var _oOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {
            this.__xhr_url = url || '';
            return _oOpen.apply(this, arguments);
        };

        // Override setRequestHeader to capture captcha-related headers
        var _oSetHeader = XMLHttpRequest.prototype.setRequestHeader;
        XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
            var nl = (name || '').toLowerCase();
            if (nl.indexOf('captcha') >= 0 || nl.indexOf('recaptcha') >= 0 || nl.indexOf('token') >= 0) {
                window.__xhr_debug = window.__xhr_debug || [];
                window.__xhr_debug.push({header: name, value_prefix: (value||'').substring(0,20)});
            }
            return _oSetHeader.apply(this, arguments);
        };

        var _oSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function(body) {
            var _t = window.__capsolver_intercept_token;
            var _xurl = this.__xhr_url || '';
            // DEBUG: log raw body before modification
            window.__xhr_debug = window.__xhr_debug || [];
            var _body_raw = body ? (typeof body === 'string' ? body.substring(0, 500) : '[non-string]') : null;
            if (body && typeof body === 'string' &&
                    (body.indexOf('captcha') >= 0 || body.indexOf('recaptcha') >= 0)) {
                // Try JSON first, then URL-encoded fallback
                try {
                    var _j = JSON.parse(body);
                    _repTok(_j);
                    body = JSON.stringify(_j);
                } catch(e) {
                    body = body.replace(/([^=&]*(re)?captcha[^=&]*)=([^&]*)/gi,
                        '$1=' + encodeURIComponent(_t));
                }
            } else if (body && typeof body === 'string' && _t) {
                // No captcha field found — force-inject token into JSON body for known signup APIs
                try {
                    var _fj = JSON.parse(body);
                    if (_fj && typeof _fj === 'object' && !Array.isArray(_fj)) {
                        // Inject under multiple possible field names (server ignores unknown ones)
                        if (!_fj.recaptchaToken) _fj.recaptchaToken = _t;
                        if (!_fj.recaptcha_token) _fj.recaptcha_token = _t;
                        if (!_fj.recaptcha_response) _fj.recaptcha_response = _t;
                        if (!_fj['g-recaptcha-response']) _fj['g-recaptcha-response'] = _t;
                        body = JSON.stringify(_fj);
                    }
                } catch(e2) {}
            }
            var _body_raw_len = body ? (typeof body === 'string' ? (_body_raw ? _body_raw.length : 0) : 0) : 0;
            var _body_after_full = body || '';
            var _body_after_len = typeof _body_after_full === 'string' ? _body_after_full.length : 0;
            var _has_tok = typeof _body_after_full === 'string' && _body_after_full.indexOf('recaptchaToken') >= 0;
            window.__xhr_debug.push({url: _xurl.substring(0,80), raw_len: _body_raw ? _body_raw.length : 0, sent_len: _body_after_len, has_tok: _has_tok, body_tail: typeof _body_after_full === 'string' ? _body_after_full.substring(_body_after_len - 150) : null, tok_prefix: _t ? _t.substring(0,20) : null});
            // Capture XHR response into window.__signup_api_response
            var _self = this;
            this.addEventListener('load', function() {
                try {
                    var _url = _self.responseURL || '';
                    var _st = _self.status;
                    var _rb;
                    try { _rb = JSON.parse(_self.responseText); }
                    catch(e) { _rb = _self.responseText.substring(0, 500); }
                    window.__signup_api_response = { url: _url, status: _st, body: _rb };
                } catch(e) {}
            }, { once: true });
            return _oSend.call(this, body);
        };
        return 'ok';
    } catch(e) { return 'err:' + e; }
}"""
                            try:
                                _int_res = await page.evaluate(_intercept_js, token)
                                logger.info(f"[job={job_id}] fetch/XHR interceptor installed: {_int_res}")
                            except Exception as _int_err:
                                logger.warning(f"[job={job_id}] fetch/XHR interceptor failed: {_int_err}")
                            # Inject token AFTER interceptor so callbacks that fire immediately are captured
                            await inject_recaptcha_token(page, token)
                            logger.info(f"[job={job_id}] reCAPTCHA solved via CapSolver sitekey={sk[:20]}... invisible={_is_invisible}")

                            # Wait briefly to see if auto-submit fires via fireCallbacks
                            await asyncio.sleep(3)
                            _auto_resp = await page.evaluate("(dummy) => JSON.stringify(window.__signup_api_response)", None)
                            logger.info(f"[job={job_id}] __signup_api_response 3s after inject: {_auto_resp}")

                            _auto_note = ""
                            if _auto_resp and _auto_resp != "null":
                                _auto_note = f" Auto-submit response: {str(_auto_resp)[:300]}."

                            return ActionResult(
                                extracted_content=(
                                    "reCAPTCHA v2 solved via CapSolver. Token injected + fetch/XHR interceptor active."
                                    + _auto_note
                                    + " Now evaluate: JSON.stringify(window.__signup_api_response). "
                                    "If null → Click Submit/Sign Up button ONCE, wait 5 seconds, then evaluate BOTH: "
                                    "(1) JSON.stringify(window.__signup_api_response) AND "
                                    "(2) JSON.stringify(window.__xhr_debug) — include BOTH in memory. "
                                    "Read result: status 200-299 or body has id/success → done(signed_up=True). "
                                    "409 or body says 'already'/'duplicate'/'exists'/'taken' → ALREADY_REGISTERED. "
                                    "403 → evaluate window.__signup_api_response.body FULL text + window.__xhr_debug FULL, include both in memory, then done(signed_up=False). "
                                    "SUCCESS page indicators (DO NOT reload): 'check your email', 'thank you', 'welcome', 'signed up', 'verify your email'. "
                                    "If you see any of these → done(signed_up=True) immediately."
                                ),
                                include_in_memory=True,
                            )
                        except Exception as _cap_err:
                            logger.warning(f"[job={job_id}] recaptcha invisible CapSolver failed: {_cap_err} — falling back to anchor-frame strategy")

                    # --- Fallback: anchor frame not yet visible (submit not clicked) ---
                    return ActionResult(
                        extracted_content=(
                            "Invisible reCAPTCHA v2 detected — sitekey not in page yet. "
                            "EXACT STEPS: "
                            "Step 1) Click the Sign Up / Submit button ONCE. "
                            "Step 2) Immediately (within 2 seconds) call solve_captcha_auto() AGAIN. "
                            "The anchor frame will now be visible and solve_captcha_auto() will extract the sitekey and solve with CapSolver automatically. "
                            "Step 3) After 'Token injected' message → click Submit again. "
                            "If you see 403 'invalid recaptcha' after clicking Submit → reload page, refill form, and restart from Step 1. "
                            "CRITICAL: Do NOT skip Step 2 after clicking Submit. The second call to solve_captcha_auto() is REQUIRED."
                        ),
                        include_in_memory=True,
                    )

                if ctype == "recaptcha_v2":
                    sk = info.get("sitekey") or ""
                    token = await capsolver.solve_recaptcha_v2(page_url, sk, invisible=bool(info.get("invisible")))
                    await inject_recaptcha_token(page, token)
                    return ActionResult(extracted_content=f"reCAPTCHA v2 auto-solved (token len={len(token)}); click submit.", include_in_memory=True)

                if ctype == "recaptcha_v2_enterprise":
                    sk = info.get("sitekey") or ""
                    token = await capsolver.solve_recaptcha_v2_enterprise(page_url, sk)
                    await inject_recaptcha_token(page, token)
                    return ActionResult(extracted_content=f"reCAPTCHA v2 Enterprise solved; click submit.", include_in_memory=True)

                if ctype == "recaptcha_v3":
                    sk = info.get("sitekey") or ""
                    token = await capsolver.solve_recaptcha_v3(page_url, sk, action="submit")
                    await inject_recaptcha_token(page, token)
                    return ActionResult(extracted_content=f"reCAPTCHA v3 auto-solved; click submit.", include_in_memory=True)

                if ctype == "recaptcha_v3_enterprise":
                    sk = info.get("sitekey") or ""
                    token = await capsolver.solve_recaptcha_v3_enterprise(page_url, sk, action="submit")
                    await inject_recaptcha_token(page, token)
                    return ActionResult(extracted_content=f"reCAPTCHA v3 Enterprise solved; click submit.", include_in_memory=True)

                if ctype == "recaptcha_v2_image_challenge":
                    # v2 image grid (chọn xe/xe đạp/đèn...) — dùng ReCaptchaV2Classification sync
                    return await _solve_image_challenge(page, page_url, sitekey=info.get("sitekey") or "")

                if ctype == "hcaptcha":
                    sk = info.get("sitekey") or ""
                    ua_raw = await page.evaluate("() => navigator.userAgent")
                    ua = (ua_raw or "").strip('"')
                    token = await capsolver.solve_hcaptcha(page_url, sk, user_agent=ua)
                    await page.evaluate(
                        """(t) => {
                            const setVal = (el) => {
                                el.value = t; el.innerHTML = t;
                                el.dispatchEvent(new Event('input', {bubbles: true}));
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                            };
                            document.querySelectorAll('textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"], input[name="h-captcha-response"]').forEach(setVal);
                            document.querySelectorAll('.h-captcha[data-callback]').forEach(el => {
                                const cb = el.getAttribute('data-callback');
                                if (cb && typeof window[cb] === 'function') try { window[cb](t); } catch(e) {}
                            });
                            ['onHCaptchaSuccess', 'onhCaptchaSuccess', 'hCaptchaCallback', 'onCaptchaSuccess'].forEach(n => {
                                if (typeof window[n] === 'function') try { window[n](t); } catch(e) {}
                            });
                            if (window.hcaptcha && typeof window.hcaptcha.close === 'function') try { window.hcaptcha.close(); } catch(e) {}
                        }""",
                        token,
                    )
                    return ActionResult(extracted_content=f"hCaptcha auto-solved (token len={len(token)}); click submit.", include_in_memory=True)

                if ctype == "hcaptcha_enterprise":
                    sk = info.get("sitekey") or ""
                    token = await capsolver.solve_hcaptcha_enterprise(page_url, sk)
                    await page.evaluate(
                        """(t) => {
                            const setVal = (el) => {
                                el.value = t; el.innerHTML = t;
                                el.dispatchEvent(new Event('input', {bubbles: true}));
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                            };
                            document.querySelectorAll('textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"], input[name="h-captcha-response"]').forEach(setVal);
                            document.querySelectorAll('.h-captcha[data-callback]').forEach(el => {
                                const cb = el.getAttribute('data-callback');
                                if (cb && typeof window[cb] === 'function') try { window[cb](t); } catch(e) {}
                            });
                            ['onHCaptchaSuccess', 'onhCaptchaSuccess', 'hCaptchaCallback'].forEach(n => {
                                if (typeof window[n] === 'function') try { window[n](t); } catch(e) {}
                            });
                        }""",
                        token,
                    )
                    return ActionResult(extracted_content=f"hCaptcha Enterprise solved (token len={len(token)}); click submit.", include_in_memory=True)

                if ctype == "mtcaptcha":
                    sk = info.get("sitekey") or ""
                    token = await capsolver.solve_mtcaptcha(page_url, sk)
                    await page.evaluate(
                        "(t) => { window.mtcaptchaConfig = window.mtcaptchaConfig || {}; document.querySelectorAll('input[name=\"mtcaptcha-verifiedtoken\"]').forEach(i => i.value = t); if (window.mtcaptcha && typeof window.mtcaptcha.getVerifiedToken === 'function') { /* stub */ } }",
                        token,
                    )
                    return ActionResult(extracted_content=f"MTCaptcha solved; click submit.", include_in_memory=True)

                if ctype == "funcaptcha":
                    pk = info.get("pkey") or ""
                    token = await capsolver.solve_funcaptcha(page_url, pk, surl="https://client-api.arkoselabs.com")
                    await page.evaluate(
                        "(t) => document.querySelectorAll('input[name=\"fc-token\"], input[name=\"verification-token\"]').forEach(i => i.value = t)",
                        token,
                    )
                    return ActionResult(extracted_content=f"FunCaptcha auto-solved; click submit.", include_in_memory=True)

                if ctype == "geetest_v4":
                    cid = info.get("captchaId") or ""
                    sol = await capsolver.solve_geetest(page_url, captcha_id=cid)
                    await page.evaluate("(s) => { window.__capsolver_geetest = s; }", sol)
                    return ActionResult(extracted_content=f"GeeTest v4 auto-solved (saved in window.__capsolver_geetest). Form submit may pick it up automatically.", include_in_memory=True)

                if ctype == "image_to_text":
                    image_src = info.get("image_src") or ""
                    # fetch ảnh qua page context → base64
                    b64 = await page.evaluate(
                        """async (src) => {
                            const r = await fetch(src, {credentials: 'include'});
                            const buf = await r.arrayBuffer();
                            const bytes = new Uint8Array(buf);
                            let bin = '';
                            for (let i = 0; i < bytes.byteLength; i++) bin += String.fromCharCode(bytes[i]);
                            return btoa(bin);
                        }""",
                        image_src,
                    )
                    if isinstance(b64, str):
                        b64 = b64.strip().strip('"')
                    text = await capsolver.solve_image_to_text(b64)
                    return ActionResult(extracted_content=f"Image captcha OCR result: '{text}'. Type vào input captcha tương ứng.", include_in_memory=True)

                if ctype == "datadome":
                    captcha_url = info.get("captchaUrl") or ""
                    ua_raw = await page.evaluate("() => navigator.userAgent")
                    ua = ua_raw.strip('"') if isinstance(ua_raw, str) else ""
                    cookie = await capsolver.solve_datadome(captcha_url, ua)
                    # Cookie format: "datadome=...; Max-Age=...; Domain=; Path=/; ..."
                    name_value = cookie.split(";")[0]
                    name, _, value = name_value.partition("=")
                    host = page_url.split("/")[2] if "/" in page_url else ""
                    try:
                        await browser_session._cdp_set_cookies([{
                            "name": name.strip(),
                            "value": value.strip(),
                            "domain": "." + host,
                            "path": "/",
                            "secure": True,
                            "sameSite": "Lax",
                        }])
                    except Exception as ce:
                        logger.warning(f"[job={job_id}] DataDome CDP cookie set failed: {ce}")
                    await page.reload()
                    return ActionResult(extracted_content="DataDome cookie injected and page reloaded.", include_in_memory=True)

                if ctype == "cloudflare_interstitial":
                    res = await capsolver.solve_cloudflare_challenge(page_url)
                    await inject_cookies_and_reload(browser_session, page, res.get("cookies") or {}, page_url)
                    return ActionResult(extracted_content="Cloudflare interstitial bypassed; page reloaded.", include_in_memory=True)

                if ctype == "aws_waf":
                    res = await capsolver.solve_aws_waf(page_url)
                    await inject_cookies_and_reload(browser_session, page, res.get("cookies") or {}, page_url)
                    return ActionResult(extracted_content="AWS WAF bypassed; page reloaded.", include_in_memory=True)

                return ActionResult(extracted_content=f"Detected captcha type '{ctype}' but no solver path matched.", include_in_memory=True)
            except Exception as e:
                logger.exception(f"[job={job_id}] solve_captcha_auto failed")
                return ActionResult(
                    extracted_content=f"Auto-solve failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        @tools.action(
            description=(
                "Solve Cloudflare Turnstile CAPTCHA. Call when you see a Cloudflare challenge "
                "iframe (challenges.cloudflare.com) or div.cf-turnstile on the page. "
                "Pass sitekey = value of data-sitekey attribute on the turnstile element. "
                "Optional action/cdata = value of data-action / data-cdata if present (some sites require)."
            )
        )
        async def solve_cloudflare_turnstile(sitekey: str, browser_session, action: str = "", cdata: str = "") -> ActionResult:
            try:
                page = await browser_session.get_current_page()
                page_url = await page.get_url()
                # Reset widget trước (phòng khi đang ở 'Verification failed').
                try:
                    await page.evaluate(
                        "() => { try { if (window.turnstile && typeof window.turnstile.reset === 'function') window.turnstile.reset(); } catch(e) {} return true; }"
                    )
                except Exception:
                    pass
                res = await capsolver.solve_turnstile(page_url, sitekey, action, cdata)
                token = res["token"]
                ua = (res.get("user_agent") or "").strip()
                if ua:
                    try:
                        cdp_session = await browser_session.get_or_create_cdp_session(target_id=None)
                        await cdp_session.cdp_client.send.Network.setUserAgentOverride(
                            params={"userAgent": ua},
                            session_id=cdp_session.session_id,
                        )
                    except Exception as ue:
                        logger.warning(f"[job={job_id}] Turnstile UA override failed: {ue}")
                await inject_turnstile_token(page, token)
                return ActionResult(
                    extracted_content=f"Turnstile solved via CapSolver (token len={len(token)}, UA pinned). Click submit now.",
                    include_in_memory=True,
                )
            except Exception as e:
                return ActionResult(
                    extracted_content=f"Turnstile solve failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        @tools.action(
            description=(
                "Click trực tiếp vào checkbox 'Verify you are human' của Cloudflare Turnstile widget. "
                "Dùng khi widget visible (KHÔNG phải full-page interstitial). "
                "Browser CloakBrowser stealth fingerprint thường pass ngay sau click — nhanh và "
                "reliable hơn CapSolver (tránh UA/IP mismatch). KHÔNG cần tham số."
            )
        )
        async def click_cloudflare_checkbox(browser_session) -> ActionResult:
            try:
                page = await browser_session.get_current_page()
                ok = await click_turnstile_checkbox(page, timeout_ms=12000)
                if ok:
                    return ActionResult(extracted_content="Turnstile checkbox passed — click submit now.", include_in_memory=True)
                return ActionResult(
                    extracted_content="Turnstile checkbox clicked but no token detected. Try `solve_cloudflare_turnstile(sitekey)` or `solve_captcha_auto()` next.",
                    include_in_memory=True,
                )
            except Exception as e:
                return ActionResult(extracted_content=f"Click checkbox failed: {type(e).__name__}: {e}", include_in_memory=True)

        @tools.action(
            description=(
                "Giải reCAPTCHA v2 IMAGE GRID challenge (popup chọn ảnh xe/xe đạp/đèn giao thông...). "
                "Dùng ngay khi thấy popup chọn ảnh xuất hiện — KHÔNG tự click tile bằng tay vì LLM "
                "không nhìn thấy nội dung ảnh chính xác. "
                "Tool tự: tìm bframe → screenshot grid → CapSolver classification (sync ~1-2s) → "
                "click đúng tile → click Verify. KHÔNG cần tham số."
            )
        )
        async def solve_recaptcha_image_challenge(browser_session) -> ActionResult:
            try:
                page = await browser_session.get_current_page()
                page_url = await page.get_url()
                return await _solve_image_challenge(page, page_url)
            except Exception as e:
                logger.exception(f"[job={job_id}] solve_recaptcha_image_challenge top-level error")
                return ActionResult(
                    extracted_content=f"Image challenge solve error: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        @tools.action(
            description=(
                "Open a combobox / autocomplete field and select an option — all via real CDP events. "
                "Use when browser 'input' action types text but the dropdown does NOT appear (text doesn't register). "
                "This tool: (1) CDP-clicks the input to focus/open the dropdown, (2) uses CDP Input.insertText to type "
                "the search query (trusted keyboard events), (3) CDP-clicks the matching option. "
                "selector = optional CSS selector to find the combobox input (default: auto-detect). "
                "query = text to type to filter (e.g. 'Vietnam'). "
                "option_text = visible text of option to click (defaults to query if same)."
            )
        )
        async def select_combobox_option(query: str, browser_session, option_text: str = "", selector: str = "") -> ActionResult:
            import asyncio as _asyncio
            import json as _json
            import traceback as _tb
            def _ev(r):
                """Parse page.evaluate() result — CloakBrowser may return JSON string."""
                if isinstance(r, str):
                    try:
                        return _json.loads(r)
                    except Exception:
                        return {}
                return r or {}
            try:
                page = await browser_session.get_current_page()
                cdp = page._client
                session_id = await page._ensure_session()
                if not option_text:
                    option_text = query
                detect_sel = selector or '[id^="headlessui-combobox-input"], input[role="combobox"], input[aria-autocomplete]'
                # Step 1: Find input position — arrow-function+arg pattern (confirmed working in CloakBrowser)
                input_pos = _ev(await page.evaluate(
                    """(sel) => {
                        var cands = sel.split(', ');
                        for (var i = 0; i < cands.length; i++) {
                            var el = document.querySelector(cands[i]);
                            if (el) {
                                var r = el.getBoundingClientRect();
                                if (r.height > 0) return {x: r.left + r.width/2, y: r.top + r.height/2, found: true};
                            }
                        }
                        return {found: false};
                    }""",
                    detect_sel,
                ))
                if not input_pos.get("found"):
                    return ActionResult(
                        extracted_content=f"Combobox input not found (tried: {detect_sel}). Provide a specific selector.",
                        include_in_memory=True,
                    )
                ix, iy = float(input_pos["x"]), float(input_pos["y"])
                # Step 2: CDP click to focus and open dropdown
                await cdp.send.Input.dispatchMouseEvent(
                    params={"type": "mousePressed", "x": ix, "y": iy, "button": "left", "clickCount": 1, "buttons": 1},
                    session_id=session_id,
                )
                await _asyncio.sleep(0.05)
                await cdp.send.Input.dispatchMouseEvent(
                    params={"type": "mouseReleased", "x": ix, "y": iy, "button": "left", "clickCount": 1, "buttons": 0},
                    session_id=session_id,
                )
                await _asyncio.sleep(0.25)
                # Step 3: Set value via native HTMLInputElement setter + dispatch input event
                # This triggers Vue/React onChange handlers (native setter bypasses framework proxy)
                await page.evaluate(
                    """(q) => {
                        var sels = ['[id^="headlessui-combobox-input"]', 'input[role="combobox"]', 'input[aria-autocomplete]'];
                        for (var i = 0; i < sels.length; i++) {
                            var el = document.querySelector(sels[i]);
                            if (el) {
                                var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                                setter.call(el, q);
                                el.dispatchEvent(new Event('input', {bubbles: true}));
                                el.dispatchEvent(new Event('change', {bubbles: true}));
                                return true;
                            }
                        }
                        return false;
                    }""",
                    query,
                )
                await _asyncio.sleep(0.8)
                # Step 4: Find matching option — arrow-function+arg pattern
                result = _ev(await page.evaluate(
                    """(text) => {
                        var opts = Array.from(document.querySelectorAll('[id^="headlessui-combobox-option"], [role="option"], [role="listitem"]'));
                        for (var i = 0; i < opts.length; i++) {
                            var el = opts[i];
                            if (el.textContent.trim() === text || el.textContent.includes(text)) {
                                var r = el.getBoundingClientRect();
                                if (r.height > 0) return {x: r.left + r.width/2, y: r.top + r.height/2, found: true, text: el.textContent.trim()};
                            }
                        }
                        return {found: false, available: opts.slice(0, 5).map(function(e) { return e.textContent.trim(); })};
                    }""",
                    option_text,
                ))
                if not result.get("found"):
                    avail = result.get("available", [])
                    return ActionResult(
                        extracted_content=(
                            f"Set '{query}' via native input setter but option '{option_text}' not found in dropdown. "
                            f"Available: {avail}. Try click_headlessui_option after dropdown appears."
                        ),
                        include_in_memory=True,
                    )
                ox, oy = float(result["x"]), float(result["y"])
                # Step 5: CDP real-click on the option
                await cdp.send.Input.dispatchMouseEvent(
                    params={"type": "mousePressed", "x": ox, "y": oy, "button": "left", "clickCount": 1, "buttons": 1},
                    session_id=session_id,
                )
                await _asyncio.sleep(0.05)
                await cdp.send.Input.dispatchMouseEvent(
                    params={"type": "mouseReleased", "x": ox, "y": oy, "button": "left", "clickCount": 1, "buttons": 0},
                    session_id=session_id,
                )
                return ActionResult(
                    extracted_content=(
                        f"select_combobox_option: set '{query}' via native input setter, "
                        f"CDP-clicked '{result.get('text', option_text)}' at ({ox:.0f},{oy:.0f}). "
                        "Framework state should now be updated — verify by checking the field value."
                    ),
                    include_in_memory=True,
                )
            except Exception as e:
                logger.error(f"select_combobox_option error:\n{_tb.format_exc()}")
                return ActionResult(
                    extracted_content=f"select_combobox_option failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        @tools.action(
            description=(
                "Click a HeadlessUI / Vue / React combobox dropdown option by its visible text using a real CDP mouse event. "
                "Use this when an autocomplete dropdown is open (options with id starting 'headlessui-combobox-option' are visible) "
                "but clicking via evaluate() does not register in the framework state. "
                "text = the exact or partial visible text of the option (e.g. 'Vietnam')."
            )
        )
        async def click_headlessui_option(text: str, browser_session) -> ActionResult:
            import asyncio as _asyncio
            import json as _json
            def _ev(r):
                if isinstance(r, str):
                    try:
                        return _json.loads(r)
                    except Exception:
                        return {}
                return r or {}
            try:
                page = await browser_session.get_current_page()
                cdp = page._client
                session_id = await page._ensure_session()
                text_json = _json.dumps(text)
                result = _ev(await page.evaluate(f"""(function(){{
                    var text = {text_json};
                    var opts = Array.from(document.querySelectorAll('[id^="headlessui-combobox-option"], [role="option"], [role="listitem"]'));
                    for (var i = 0; i < opts.length; i++) {{
                        var el = opts[i];
                        if (el.textContent.trim() === text || el.textContent.includes(text)) {{
                            var r = el.getBoundingClientRect();
                            if (r.height > 0) return {{x: r.left + r.width/2, y: r.top + r.height/2, found: true, text: el.textContent.trim()}};
                        }}
                    }}
                    var all = Array.from(document.querySelectorAll('*'));
                    for (var i = 0; i < all.length; i++) {{
                        var el = all[i];
                        if (!el.firstElementChild && el.textContent.trim() === text) {{
                            var r = el.getBoundingClientRect();
                            if (r.height > 0) return {{x: r.left + r.width/2, y: r.top + r.height/2, found: true, text: el.textContent.trim()}};
                        }}
                    }}
                    return {{found: false}};
                }})()"""))
                if not result.get("found"):
                    return ActionResult(
                        extracted_content=f"No visible option found with text '{text}'. Make sure the dropdown is open first (type in the combobox).",
                        include_in_memory=True,
                    )
                x, y = float(result["x"]), float(result["y"])
                await cdp.send.Input.dispatchMouseEvent(
                    params={"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1, "buttons": 1},
                    session_id=session_id,
                )
                await _asyncio.sleep(0.05)
                await cdp.send.Input.dispatchMouseEvent(
                    params={"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1, "buttons": 0},
                    session_id=session_id,
                )
                return ActionResult(
                    extracted_content=f"CDP real-click on option '{result.get('text', text)}' at ({x:.0f},{y:.0f}). The framework state should now be updated.",
                    include_in_memory=True,
                )
            except Exception as e:
                return ActionResult(
                    extracted_content=f"click_headlessui_option failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        @tools.action(
            description=(
                "Solve reCAPTCHA v2. Call when you see div.g-recaptcha or iframe from www.google.com/recaptcha. "
                "Pass sitekey = value of data-sitekey on .g-recaptcha element. "
                "Pass invisible=True if reCAPTCHA is invisible (no visible checkbox, data-size='invisible', or triggered automatically on form submit)."
            )
        )
        async def solve_recaptcha_v2(sitekey: str, browser_session, invisible: bool = False) -> ActionResult:
            try:
                page = await browser_session.get_current_page()
                page_url = await page.get_url()
                token = await capsolver.solve_recaptcha_v2(page_url, sitekey, invisible=invisible)
                await inject_recaptcha_token(page, token)
                return ActionResult(
                    extracted_content=f"reCAPTCHA v2 solved & injected (token len={len(token)}). Now click submit.",
                    include_in_memory=True,
                )
            except Exception as e:
                return ActionResult(
                    extracted_content=f"reCAPTCHA solve failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        @tools.action(
            description=(
                "Solve hCaptcha. Call when you see div.h-captcha or iframe from hcaptcha.com. "
                "Pass sitekey = value of data-sitekey on .h-captcha element."
            )
        )
        async def solve_hcaptcha(sitekey: str, browser_session) -> ActionResult:
            try:
                page = await browser_session.get_current_page()
                page_url = await page.get_url()
                ua_raw = await page.evaluate("() => navigator.userAgent")
                ua = (ua_raw or "").strip('"')
                token = await capsolver.solve_hcaptcha(page_url, sitekey, user_agent=ua)
                await page.evaluate(
                    """(t) => {
                        const setVal = (el) => {
                            el.value = t; el.innerHTML = t;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                        };
                        document.querySelectorAll('textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"], input[name="h-captcha-response"]').forEach(setVal);
                        document.querySelectorAll('.h-captcha[data-callback]').forEach(el => {
                            const cb = el.getAttribute('data-callback');
                            if (cb && typeof window[cb] === 'function') try { window[cb](t); } catch(e) {}
                        });
                        ['onHCaptchaSuccess', 'onhCaptchaSuccess', 'hCaptchaCallback', 'onCaptchaSuccess'].forEach(n => {
                            if (typeof window[n] === 'function') try { window[n](t); } catch(e) {}
                        });
                        if (window.hcaptcha && typeof window.hcaptcha.close === 'function') try { window.hcaptcha.close(); } catch(e) {}
                    }""",
                    token,
                )
                return ActionResult(
                    extracted_content=f"hCaptcha solved & injected (token len={len(token)}). Now click submit.",
                    include_in_memory=True,
                )
            except Exception as e:
                return ActionResult(
                    extracted_content=f"hCaptcha solve failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        @tools.action(
            description=(
                "Solve reCAPTCHA v3 (invisible, score-based). Call when page has grecaptcha.execute() "
                "or invisible reCAPTCHA badge. Pass sitekey (data-sitekey) and pageAction (the "
                "action name passed to grecaptcha.execute, e.g. 'submit', 'login', 'signup')."
            )
        )
        async def solve_recaptcha_v3(sitekey: str, action: str, browser_session) -> ActionResult:
            try:
                page = await browser_session.get_current_page()
                page_url = await page.get_url()
                token = await capsolver.solve_recaptcha_v3(page_url, sitekey, action=action or "submit", min_score=0.7)
                await inject_recaptcha_token(page, token)
                return ActionResult(
                    extracted_content=f"reCAPTCHA v3 solved & injected (action={action}, token len={len(token)}). Now click submit.",
                    include_in_memory=True,
                )
            except Exception as e:
                return ActionResult(
                    extracted_content=f"reCAPTCHA v3 solve failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        @tools.action(
            description=(
                "Solve FunCaptcha / Arkose Labs challenge (rotate images, etc). Call when you see "
                "iframe from arkoselabs.com or 'funcaptcha'. Pass public_key (data-pkey attribute "
                "on the funcaptcha element, e.g. '3D6F2C0E-...'). Optional surl = funcaptcha API subdomain."
            )
        )
        async def solve_funcaptcha(public_key: str, surl: str, browser_session) -> ActionResult:
            try:
                page = await browser_session.get_current_page()
                page_url = await page.get_url()
                token = await capsolver.solve_funcaptcha(page_url, public_key, surl)
                # FunCaptcha token thường được điền vào input[name=fc-token] hoặc verification-token
                await page.evaluate(
                    """(tok) => {
                        document.querySelectorAll('input[name="fc-token"], input[name="verification-token"], #FunCaptcha-Token').forEach(i => {
                            i.value = tok;
                            i.dispatchEvent(new Event('change', {bubbles: true}));
                        });
                    }""",
                    token,
                )
                return ActionResult(
                    extracted_content=f"FunCaptcha solved & injected (token len={len(token)}). Now click submit.",
                    include_in_memory=True,
                )
            except Exception as e:
                return ActionResult(
                    extracted_content=f"FunCaptcha solve failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        @tools.action(
            description=(
                "Bypass AWS WAF challenge page (used by many SaaS sites). Call when page shows "
                "'Verify you are human' from AWS WAF or stuck on aws-waf JS challenge. "
                "Solver gets aws-waf-token cookies and injects them; page will be reloaded."
            )
        )
        async def solve_aws_waf_challenge(browser_session) -> ActionResult:
            try:
                page = await browser_session.get_current_page()
                page_url = await page.get_url()
                cookies = await capsolver.solve_aws_waf(page_url)
                await inject_cookies_and_reload(browser_session, page, cookies, page_url)
                return ActionResult(
                    extracted_content=f"AWS WAF cookies injected ({len(cookies)} keys), page reloaded. Continue with form.",
                    include_in_memory=True,
                )
            except Exception as e:
                return ActionResult(
                    extracted_content=f"AWS WAF solve failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        @tools.action(
            description=(
                "Bypass Cloudflare full-page interstitial (5-second JS check / 'Verify you are human' from Cloudflare). "
                "This is DIFFERENT from the Turnstile widget — use this when the ENTIRE page is the Cloudflare challenge "
                "(not just a checkbox on the signup form). Solver gets cf_clearance cookies; page will be reloaded."
            )
        )
        async def solve_cloudflare_interstitial(browser_session) -> ActionResult:
            try:
                page = await browser_session.get_current_page()
                page_url = await page.get_url()
                res = await capsolver.solve_cloudflare_challenge(page_url)
                await inject_cookies_and_reload(browser_session, page, res.get("cookies") or {}, page_url)
                return ActionResult(
                    extracted_content=f"Cloudflare cf_clearance injected, page reloaded. Now proceed to signup form.",
                    include_in_memory=True,
                )
            except Exception as e:
                return ActionResult(
                    extracted_content=f"Cloudflare challenge solve failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        logger.info(f"[job={job_id}] CapSolver tools enabled (balance check skipped)")
    else:
        logger.warning(f"[job={job_id}] CapSolver disabled — no CAPSOLVER_API_KEY")

    # 4b. SMS OTP tools (5sim) — chỉ đăng ký khi có API key
    sms_service = SmsOtpService()
    sms_state: dict = {"rental_id": "", "phone": ""}
    if sms_service.enabled:
        @tools.action(
            description=(
                "Rent a temporary phone number from SMS provider (5sim) for OTP verification. "
                "Call this when the signup form requires phone number verification via SMS. "
                "Returns the phone number to enter into the form. After form submit, "
                "call `read_sms_otp_code` to fetch the SMS code."
            )
        )
        async def request_sms_phone_number() -> ActionResult:
            try:
                # Override per-program nếu có preset; rỗng = SmsOtpService dùng default env
                country_override = str(program.get("sms_country_id") or "")
                service_override = str(program.get("sms_service_id") or "")
                info = await sms_service.buy_number(
                    country=country_override,
                    product=service_override,
                )
                sms_state["rental_id"] = info["rental_id"]
                sms_state["phone"] = info["phone"]
                return ActionResult(
                    extracted_content=f"Phone rented: {info['phone']} (rental_id={info['rental_id']}). Enter this phone number into the form, then submit. After submit, call read_sms_otp_code.",
                    include_in_memory=True,
                )
            except Exception as e:
                return ActionResult(
                    extracted_content=f"SMS rent failed: {type(e).__name__}: {e}. Report failed with 'SMS_PROVIDER_ERROR'.",
                    include_in_memory=True,
                )

        @tools.action(
            description=(
                "Poll SMS provider for the OTP code sent to the rented phone number. "
                "Call AFTER you submitted the form with the rented phone. "
                "Returns the OTP code digits to enter into the verification field."
            )
        )
        async def read_sms_otp_code(timeout_sec: int = 0) -> ActionResult:
            if not sms_state.get("rental_id"):
                return ActionResult(
                    extracted_content="No active SMS rental. Call request_sms_phone_number first.",
                    include_in_memory=True,
                )
            timeout = timeout_sec if timeout_sec > 0 else settings.sms_otp_timeout_sec
            try:
                code = await sms_service.wait_for_code(sms_state["rental_id"], timeout_sec=timeout)
                # mark complete (free up number)
                await sms_service.finish(sms_state["rental_id"])
                return ActionResult(
                    extracted_content=f"SMS OTP code received: {code}. Enter this code into the verification field on the form.",
                    include_in_memory=True,
                )
            except Exception as e:
                await sms_service.cancel(sms_state["rental_id"])
                return ActionResult(
                    extracted_content=f"SMS OTP wait failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        logger.info(f"[job={job_id}] SMS OTP tools enabled (provider={sms_service.provider})")
    else:
        logger.warning(f"[job={job_id}] SMS OTP disabled — no SMS_OTP_API_KEY")

    # 4c. Email verification tool (IMAP) — chỉ đăng ký khi có IMAP credentials
    imap_user_cfg = (profile.get("imap") or {}).get("user") or settings.imap_user
    imap_pass_cfg = (profile.get("imap") or {}).get("password") or settings.imap_password
    if imap_user_cfg and imap_pass_cfg:
        @tools.action(
            description=(
                "Wait for verification email and return its OTP code and/or verification link. "
                "Call AFTER submitting the signup form when the site says 'we sent you an email'. "
                "Optional sender_contains: filter emails by sender domain/name (e.g. 'webflow'). "
                "Optional want_link: set to true if site sends a click-link instead of code. "
                "Returns: code (digits) and/or link (URL to visit)."
            )
        )
        async def read_email_verification(sender_contains: str = "", want_link: bool = False, timeout_sec: int = 0) -> ActionResult:
            timeout = timeout_sec if timeout_sec > 0 else settings.imap_timeout_sec
            try:
                res = await wait_for_verification(
                    profile,
                    sender_contains=sender_contains,
                    want_link=want_link,
                    timeout_sec=timeout,
                )
                code = res.get("code") or ""
                link = res.get("link") or ""
                subject = res.get("subject") or ""
                msg_parts = [f"Email received from '{res.get('from','')}' subject='{subject}'."]
                if code:
                    msg_parts.append(f"OTP code: {code}")
                if link:
                    msg_parts.append(f"Verification link: {link}")
                if not code and not link:
                    msg_parts.append("No code/link found in email body.")
                return ActionResult(
                    extracted_content=" | ".join(msg_parts),
                    include_in_memory=True,
                )
            except Exception as e:
                return ActionResult(
                    extracted_content=f"Email verification failed: {type(e).__name__}: {e}",
                    include_in_memory=True,
                )

        logger.info(f"[job={job_id}] Email verification tool enabled (user={imap_user_cfg})")
    else:
        logger.warning(f"[job={job_id}] Email verification disabled — no IMAP credentials (profile.imap or env)")


    screenshot_path: str | None = None
    captured_quota_errors: list[str] = []

    class _QuotaLogCapture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                msg = record.getMessage()
            except Exception:
                return
            low = msg.lower()
            if (
                "resource_exhausted" in low
                or "generaterequestsperdayperprojectpermodel" in low
                or "quota_exceeded" in low
                or ("quota" in low and "exceeded" in low)
            ):
                captured_quota_errors.append(msg[:500])

    quota_capture = _QuotaLogCapture(level=logging.WARNING)
    logging.getLogger().addHandler(quota_capture)
    try:
        _key_counts = {"gemini": gemini_key_count, "openai": openai_key_count, "deepseek": deepseek_key_count}
        max_llm_attempts = max(1, _key_counts.get(_effective_provider, gemini_key_count)())
        _mark_quota = {"gemini": mark_gemini_quota_error, "openai": mark_openai_quota_error, "deepseek": mark_deepseek_quota_error}
        max_browser_attempts = 3
        history = None
        for browser_attempt in range(max_browser_attempts):
            browser_session = create_signup_browser_session(headless=headless, proxy_url=proxy_override)
            try:
                for llm_attempt in range(max_llm_attempts):
                    llm, provider, api_key = _make_llm(llm_key_index if llm_attempt == 0 else 0)
                    try:
                        agent = Agent(
                            task=task,
                            llm=llm,
                            browser_session=browser_session,
                            tools=tools,
                            use_vision=True,
                            max_actions_per_step=3,
                        )
                        history = await agent.run(max_steps=settings.signup_max_steps)
                        break
                    except Exception as e:
                        if is_quota_error(e) and llm_attempt < max_llm_attempts - 1:
                            _mark_quota.get(provider, mark_gemini_quota_error)(api_key)
                            logger.warning(
                                "[job=%s] %s key quota/rate-limit hit; retrying with next key (%s/%s)",
                                job_id,
                                provider,
                                llm_attempt + 1,
                                max_llm_attempts,
                            )
                            continue
                        raise
                if history is None:
                    raise RuntimeError("Agent did not return history")
                break
            except Exception as e:
                if _is_cdp_startup_error(e) and browser_attempt < max_browser_attempts - 1:
                    logger.warning(
                        "[job=%s] CDP startup timeout; restarting browser session (%s/%s): %s",
                        job_id,
                        browser_attempt + 1,
                        max_browser_attempts,
                        e,
                    )
                    await _close_browser_session(browser_session, job_id)
                    browser_session = None
                    await asyncio.sleep(2 + browser_attempt * 2)
                    continue
                raise
        if history is None:
            raise RuntimeError("Agent did not return history")
        parsed_result = _parse_agent_result(history)
        screenshot_path = _extract_last_screenshot_from_history(history, job_id, program_id, str(profile_id))
        parsed_result["screenshot"] = screenshot_path

        # Nếu chưa có final_url, trích từ history
        if not parsed_result.get("final_url"):
            try:
                for h in reversed(getattr(history, "history", []) or []):
                    state = getattr(h, "state", None)
                    url = (
                        getattr(state, "url", None)
                        or (state.get("url") if isinstance(state, dict) else None)
                    )
                    if url:
                        parsed_result["final_url"] = url
                        break
            except Exception:
                pass

        if not (parsed_result.get("message") or "").strip():
            steps_run = parsed_result.get("steps", 0)
            # Heuristic: URL đổi khỏi signup_url sau >= 5 bước → khả năng đã submit xong
            _signup_url = (program.get("signup_url") or program.get("url") or "").rstrip("/")
            _final_url = (parsed_result.get("final_url") or "").rstrip("/")
            if (
                _signup_url and _final_url
                and _final_url != _signup_url
                and steps_run >= 5
            ):
                parsed_result["status"] = "pending_verify"
                parsed_result["message"] = (
                    f"Agent hoàn thành {steps_run} bước, URL đã đổi → chờ xác nhận email."
                )
            elif captured_quota_errors and steps_run < 3:
                parsed_result["status"] = "error"
                parsed_result["message"] = (
                    "Gemini quota/rate-limit exhausted. Add another GEMINI_API_KEY or wait for quota reset."
                )
            elif steps_run >= settings.signup_max_steps:
                parsed_result["message"] = (
                    f"Agent reached max_steps ({settings.signup_max_steps}) without completing the task. "
                    "Check the saved screenshot for the current browser state."
                )
            else:
                parsed_result["message"] = (
                    f"Agent dừng sau {steps_run} bước mà không có kết quả. "
                    "Xem screenshot để biết trạng thái browser."
                )
        parsed_result["duration_sec"] = round(time.time() - started, 2)
        # After a successful run, extract steps from history and upsert playbook
        if parsed_result.get("status") in ("success", "pending_verify") and history is not None:
            try:
                from app.services.playbook_extractor import extract_and_upsert
                await extract_and_upsert(history, program, profile, user_id, job_id)
            except Exception as _pe:
                logger.debug("[job=%s] playbook extraction: %s", job_id, _pe)
        return parsed_result
    except Exception as e:
        logger.exception(f"run_signup_attempt error: {e}")
        return {
            "status": "error",
            "message": f"{type(e).__name__}: {e}"[:500],
            "steps": 0,
            "screenshot": screenshot_path,
            "duration_sec": round(time.time() - started, 2),
        }
    finally:
        logging.getLogger().removeHandler(quota_capture)
        # Đóng triệt để trình duyệt — tránh rác process Chromium sau khi job xong.
        if browser_session is not None:
            await _close_browser_session(browser_session, job_id)
        logger.info(f"[job={job_id}] browser session closed")
