"""System status endpoint — báo các config nào đã set, subsystem nào enable.

Dùng để FE hiển thị banner '⚠️ Thiếu config: ...' cho user biết cần điền key gì.
"""

import asyncio
import time
from pathlib import Path
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.deps import get_current_user
from app.models import User
from app.services.llm_keyring import (
    gemini_key_count, gemini_keys,
    openai_key_count, deepseek_key_count,
)
from app.services.captcha.capsolver import CapSolver
from app.services.signup.sms_otp import SmsOtpService
from app.services.storage import email_store, proxy_store

router = APIRouter(prefix="/api/system", tags=["system"])


def _mask(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 8:
        return "***"
    return v[:4] + "***" + v[-4:]


class LlmKeyCreate(BaseModel):
    key: str

# Backward compat alias
GeminiKeyCreate = LlmKeyCreate

PROVIDER_ENV_MAP: dict[str, str] = {
    "gemini":   "GEMINI_API_KEY",
    "openai":   "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


def _env_path() -> Path:
    env_file = Path(str(settings.model_config.get("env_file") or ".env"))
    return env_file if env_file.is_absolute() else Path.cwd() / env_file


def _normalize_provider_key(raw: str, env_var: str) -> str:
    """Strip quotes and optional 'ENV_VAR=' prefix from a raw key string."""
    value = (raw or "").strip().strip('"').strip("'")
    prefix = f"{env_var}="
    if value.startswith(prefix):
        value = value[len(prefix):].strip().strip('"').strip("'")
    return value


def _split_provider_keys(raw: str, env_var: str) -> List[str]:
    return [
        v
        for part in (raw or "").replace("\n", ",").split(",")
        if (v := _normalize_provider_key(part, env_var))
    ]


def _read_provider_keys(provider: str) -> List[str]:
    env_var = PROVIDER_ENV_MAP[provider]
    path = _env_path()
    if not path.exists():
        return []
    keys: List[str] = []
    prefix = f"{env_var}="
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or not line.startswith(prefix):
                continue
            keys.extend(_split_provider_keys(line.split("=", 1)[1], env_var))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Không đọc được backend/.env: {exc}") from exc
    deduped: List[str] = []
    seen: set[str] = set()
    for key in keys:
        if key not in seen:
            deduped.append(key)
            seen.add(key)
    return deduped


def _write_provider_keys(provider: str, keys: List[str]) -> None:
    env_var = PROVIDER_ENV_MAP[provider]
    path = _env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{env_var}="

    clean_keys: List[str] = []
    seen: set[str] = set()
    for key in keys:
        v = _normalize_provider_key(key, env_var)
        if v and v not in seen:
            clean_keys.append(v)
            seen.add(v)

    new_lines: List[str] = []
    inserted = False
    for line in original_lines:
        if line.strip().startswith(prefix):
            if not inserted:
                new_lines.extend([f"{env_var}={k}" for k in clean_keys])
                inserted = True
            continue
        new_lines.append(line)
    if not inserted:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.extend([f"{env_var}={k}" for k in clean_keys])
    path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")

    # Update in-memory settings so the keyring reads fresh values immediately
    if provider == "gemini":
        settings.gemini_api_key = ",".join(clean_keys)
    elif provider == "openai":
        settings.openai_api_key = clean_keys[0] if clean_keys else ""
    elif provider == "deepseek":
        settings.deepseek_api_key = clean_keys[0] if clean_keys else ""


def _provider_key_list_payload(provider: str) -> dict:
    keys = _read_provider_keys(provider)
    return {
        "provider": provider,
        "items": [
            {"index": idx, "masked": _mask(key), "source": "backend/.env"}
            for idx, key in enumerate(keys, start=1)
        ],
        "total": len(keys),
    }


async def _test_provider_key_value(provider: str, key: str, index: int) -> dict:
    started = time.time()
    elapsed = lambda: int((time.time() - started) * 1000)
    masked = _mask(key)

    if provider == "gemini":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": key},
                )
            if r.status_code == 200:
                return {"ok": True, "provider": "gemini", "model": settings.signup_llm_model,
                        "key_index": index, "masked": masked, "elapsed_ms": elapsed()}
            return {"ok": False, "provider": "gemini", "key_index": index, "masked": masked,
                    "error": f"HTTP {r.status_code}: {r.text[:240]}", "elapsed_ms": elapsed()}
        except Exception as exc:
            return {"ok": False, "provider": "gemini", "key_index": index, "masked": masked,
                    "error": f"{exc.__class__.__name__}: {exc}", "elapsed_ms": elapsed()}

    if provider == "openai":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
            if r.status_code == 200:
                return {"ok": True, "provider": "openai", "key_index": index,
                        "masked": masked, "elapsed_ms": elapsed()}
            return {"ok": False, "provider": "openai", "key_index": index, "masked": masked,
                    "error": f"HTTP {r.status_code}: {r.text[:240]}", "elapsed_ms": elapsed()}
        except Exception as exc:
            return {"ok": False, "provider": "openai", "key_index": index, "masked": masked,
                    "error": f"{exc.__class__.__name__}: {exc}", "elapsed_ms": elapsed()}

    if provider == "deepseek":
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.deepseek.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
            if r.status_code == 200:
                return {"ok": True, "provider": "deepseek", "key_index": index,
                        "masked": masked, "elapsed_ms": elapsed()}
            return {"ok": False, "provider": "deepseek", "key_index": index, "masked": masked,
                    "error": f"HTTP {r.status_code}: {r.text[:240]}", "elapsed_ms": elapsed()}
        except Exception as exc:
            return {"ok": False, "provider": "deepseek", "key_index": index, "masked": masked,
                    "error": f"{exc.__class__.__name__}: {exc}", "elapsed_ms": elapsed()}

    return {"ok": False, "error": f"Provider không hỗ trợ: {provider}"}


# ── Generic LLM key endpoints ──────────────────────────────────────────────────

@router.get("/llm-keys/{provider}")
async def list_llm_keys(provider: str, user: User = Depends(get_current_user)):
    if provider not in PROVIDER_ENV_MAP:
        raise HTTPException(status_code=400, detail=f"Provider không hỗ trợ: {provider}")
    return _provider_key_list_payload(provider)


@router.post("/llm-keys/{provider}")
async def create_llm_key(provider: str, body: LlmKeyCreate, user: User = Depends(get_current_user)):
    if provider not in PROVIDER_ENV_MAP:
        raise HTTPException(status_code=400, detail=f"Provider không hỗ trợ: {provider}")
    env_var = PROVIDER_ENV_MAP[provider]
    new_keys = _split_provider_keys(body.key, env_var)
    if not new_keys:
        raise HTTPException(status_code=400, detail=f"{env_var} không được để trống")
    keys = _read_provider_keys(provider)
    added = [k for k in new_keys if k not in set(keys)]
    if not added:
        raise HTTPException(status_code=409, detail="Key này đã tồn tại trong backend/.env")
    keys.extend(added)
    _write_provider_keys(provider, keys)
    return _provider_key_list_payload(provider)


@router.delete("/llm-keys/{provider}/{index}")
async def delete_llm_key(provider: str, index: int, user: User = Depends(get_current_user)):
    if provider not in PROVIDER_ENV_MAP:
        raise HTTPException(status_code=400, detail=f"Provider không hỗ trợ: {provider}")
    keys = _read_provider_keys(provider)
    if index < 1 or index > len(keys):
        raise HTTPException(status_code=404, detail=f"Không tìm thấy key #{index}")
    del keys[index - 1]
    _write_provider_keys(provider, keys)
    return _provider_key_list_payload(provider)


@router.post("/llm-keys/{provider}/{index}/test")
async def test_llm_key(provider: str, index: int, user: User = Depends(get_current_user)):
    if provider not in PROVIDER_ENV_MAP:
        raise HTTPException(status_code=400, detail=f"Provider không hỗ trợ: {provider}")
    keys = _read_provider_keys(provider)
    if index < 1 or index > len(keys):
        raise HTTPException(status_code=404, detail=f"Không tìm thấy key #{index}")
    return await _test_provider_key_value(provider, keys[index - 1], index)


# ── Backward-compat Gemini endpoints (alias → generic) ────────────────────────

@router.get("/gemini-keys")
async def list_gemini_keys(user: User = Depends(get_current_user)):
    return _provider_key_list_payload("gemini")


@router.post("/gemini-keys")
async def create_gemini_key(body: LlmKeyCreate, user: User = Depends(get_current_user)):
    return await create_llm_key("gemini", body, user)


@router.delete("/gemini-keys/{index}")
async def delete_gemini_key(index: int, user: User = Depends(get_current_user)):
    return await delete_llm_key("gemini", index, user)


@router.post("/gemini-keys/{index}/test")
async def test_gemini_key(index: int, user: User = Depends(get_current_user)):
    return await test_llm_key("gemini", index, user)


@router.get("/status")
async def system_status(user: User = Depends(get_current_user)):
    """Trả về tình trạng config + subsystem.

    `enabled` = có thể chạy hay không.
    `note` = hướng dẫn fix nếu disabled.
    """
    # LLM — ưu tiên Gemini → OpenAI → DeepSeek
    llm_provider = ""
    gemini_total = gemini_key_count()
    oa_total = openai_key_count()
    ds_total = deepseek_key_count()
    if gemini_total:
        llm_provider = "gemini"
    elif oa_total:
        llm_provider = "openai"
    elif ds_total:
        llm_provider = "deepseek"

    # IMAP & Proxy: ưu tiên Library (file-based) → fallback .env
    library_emails = email_store.list_emails(user.id) if user else []
    library_proxies = proxy_store.list_proxies(user.id) if user else []
    library_email_count = len(library_emails)
    library_proxy_count = len(library_proxies)
    # Chỉ tính là "verified" khi đã test kết nối thật thành công
    library_email_ok = sum(1 for e in library_emails if e.get("last_test_result") == "ok")
    library_proxy_ok = sum(1 for p in library_proxies if p.get("last_test_result") == "ok")

    imap_env_ok = bool(settings.imap_user and settings.imap_password)
    # Khi user đã thêm vào Library nhưng chưa cái nào test ok → coi như chưa sẵn sàng
    imap_ok = (library_email_ok > 0) if library_email_count > 0 else imap_env_ok
    proxy_ok = library_proxy_ok > 0

    subsystems = [
        {
            "key": "llm",
            "label": "LLM (Browser Agent)",
            "enabled": bool(llm_provider),
            "value": (
                f"gemini:{settings.signup_llm_model} ({gemini_total} keys)" if llm_provider == "gemini"
                else f"openai ({oa_total} keys)" if llm_provider == "openai"
                else f"deepseek ({ds_total} keys)" if llm_provider == "deepseek"
                else ""
            ),
            "required": True,
            "note": "" if llm_provider else "Thiếu API key LLM. Thêm GEMINI_API_KEY, OPENAI_API_KEY hoặc DEEPSEEK_API_KEY tại Thư viện → API KEY",
        },
        {
            "key": "capsolver",
            "label": "CapSolver (CAPTCHA)",
            "enabled": bool(settings.capsolver_api_key),
            "value": _mask(settings.capsolver_api_key),
            "required": False,
            "note": "" if settings.capsolver_api_key
                else "Thiếu CAPSOLVER_API_KEY → site có Turnstile/reCAPTCHA/hCaptcha/FunCaptcha/WAF sẽ FAIL. Lấy key: https://capsolver.com",
        },
        {
            "key": "sms_otp",
            "label": f"SMS OTP ({settings.sms_otp_provider})",
            "enabled": bool(settings.sms_otp_api_key),
            "value": _mask(settings.sms_otp_api_key),
            "required": False,
            "note": "" if settings.sms_otp_api_key
                else (
                    "Thiếu SMS_OTP_API_KEY → site yêu cầu phone OTP sẽ FAIL. "
                    + (
                        "Lấy key: https://www.smspool.net/my/settings" if settings.sms_otp_provider == "smspool"
                        else "Lấy key: https://5sim.net/profile" if settings.sms_otp_provider == "5sim"
                        else "Lấy key từ provider hiện hành."
                    )
                ),
        },
        {
            "key": "imap_email",
            "label": "IMAP Email Verification",
            "enabled": imap_ok,
            "value": (
                f"Library: {library_email_ok}/{library_email_count} email đã test ok"
                if library_email_count > 0
                else (settings.imap_user if imap_env_ok else "")
            ),
            "required": False,
            "note": "" if imap_ok
                else (
                    f"Có {library_email_count} email trong Thư viện nhưng chưa cái nào test IMAP thành công. Bấm nút 'Test' tại /library?tab=email để kiểm tra app password."
                    if library_email_count > 0
                    else "Chưa có email nào trong Thư viện và IMAP_USER/IMAP_PASSWORD (env) cũng trống → site yêu cầu email verify sẽ FAIL. Thêm email tại /library?tab=email hoặc Gmail App Password: https://myaccount.google.com/apppasswords"
                ),
        },
        {
            "key": "proxy",
            "label": "Proxy (residential rotating)",
            "enabled": proxy_ok,
            "value": (
                f"Library: {library_proxy_ok}/{library_proxy_count} proxy đã test ok"
                if library_proxy_count > 0
                else ""
            ),
            "required": False,
            "note": "" if proxy_ok
                else (
                    f"Có {library_proxy_count} proxy trong Thư viện nhưng chưa cái nào test thành công. Bấm nút 'Test' tại /library?tab=proxy."
                    if library_proxy_count > 0
                    else "Khuyến nghị thêm proxy tại /library?tab=proxy để tránh ban IP khi đăng ký nhiều site."
                ),
        },
    ]

    missing_required = [s for s in subsystems if s["required"] and not s["enabled"]]
    missing_optional = [s for s in subsystems if not s["required"] and not s["enabled"]]

    return {
        "app": settings.app_name,
        "ready": len(missing_required) == 0,
        "fully_configured": len(missing_required) == 0 and len(missing_optional) == 0,
        "subsystems": subsystems,
        "missing_required": [s["key"] for s in missing_required],
        "missing_optional": [s["key"] for s in missing_optional],
        "signup_max_steps": settings.signup_max_steps,
    }


# ---------------------- Real connectivity tests ---------------------- #


async def _test_llm() -> dict:
    """Light check: verify key exists. Real ping tốn token, không nên auto."""
    started = time.time()
    keys = gemini_keys()
    if keys:
        # Verify key format + ping models.list (free, không tốn token)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                errors = []
                for idx, key in enumerate(keys, start=1):
                    r = await client.get(
                        "https://generativelanguage.googleapis.com/v1beta/models",
                        params={"key": key},
                    )
                    if r.status_code == 200:
                        return {
                            "ok": True,
                            "provider": "gemini",
                            "model": settings.signup_llm_model,
                            "key_index": idx,
                            "key_count": len(keys),
                            "elapsed_ms": int((time.time() - started) * 1000),
                        }
                    errors.append(f"key {idx}: HTTP {r.status_code}: {r.text[:120]}")
            return {
                "ok": False,
                "provider": "gemini",
                "error": " | ".join(errors)[:500],
                "key_count": len(keys),
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        except Exception as e:
            return {"ok": False, "provider": "gemini", "error": f"{e.__class__.__name__}: {e}", "elapsed_ms": int((time.time() - started) * 1000)}
    from app.services.llm_keyring import get_openai_key, get_deepseek_key
    oa_key, _, _ = get_openai_key()
    if oa_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {oa_key}"},
                )
            if r.status_code == 200:
                return {"ok": True, "provider": "openai", "model": "gpt-4o", "elapsed_ms": int((time.time() - started) * 1000)}
            return {"ok": False, "provider": "openai", "error": f"HTTP {r.status_code}: {r.text[:120]}", "elapsed_ms": int((time.time() - started) * 1000)}
        except Exception as e:
            return {"ok": False, "provider": "openai", "error": f"{e.__class__.__name__}: {e}", "elapsed_ms": int((time.time() - started) * 1000)}
    ds_key, _, _ = get_deepseek_key()
    if ds_key:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.deepseek.com/v1/models",
                    headers={"Authorization": f"Bearer {ds_key}"},
                )
            if r.status_code == 200:
                return {"ok": True, "provider": "deepseek", "elapsed_ms": int((time.time() - started) * 1000)}
            return {"ok": False, "provider": "deepseek", "error": f"HTTP {r.status_code}: {r.text[:120]}", "elapsed_ms": int((time.time() - started) * 1000)}
        except Exception as e:
            return {"ok": False, "provider": "deepseek", "error": f"{e.__class__.__name__}: {e}", "elapsed_ms": int((time.time() - started) * 1000)}
    return {"ok": False, "error": "Chưa có GEMINI_API_KEY, OPENAI_API_KEY hoặc DEEPSEEK_API_KEY trong .env"}


async def _test_capsolver() -> dict:
    started = time.time()
    if not settings.capsolver_api_key:
        return {"ok": False, "error": "Chưa có CAPSOLVER_API_KEY trong .env"}
    try:
        bal = await CapSolver(settings.capsolver_api_key).balance()
        return {"ok": True, "balance": f"{bal:.3f}", "currency": "USD", "elapsed_ms": int((time.time() - started) * 1000)}
    except Exception as e:
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}", "elapsed_ms": int((time.time() - started) * 1000)}


async def _test_sms() -> dict:
    started = time.time()
    svc = SmsOtpService()
    if not svc.enabled:
        return {"ok": False, "error": "Chưa có SMS_OTP_API_KEY trong .env"}
    r = await svc.check_balance()
    r["elapsed_ms"] = int((time.time() - started) * 1000)
    r["provider"] = svc.provider
    return r


async def _test_imap_library(user_id: int) -> dict:
    """Pick email mới nhất trong Library + thử IMAP login (1 cái thôi)."""
    from app.api.emails import _imap_login_test
    started = time.time()
    emails = email_store.list_emails(user_id)
    if not emails:
        return {"ok": False, "error": "Thư viện chưa có email nào. Thêm tại /library?tab=email"}
    # Ưu tiên email đã test ok trước
    target = next((e for e in emails if e.get("last_test_result") == "ok"), None) or emails[0]
    provider = (target.get("provider") or "").lower()
    host_map = {
        "gmail": "imap.gmail.com",
        "outlook": "outlook.office365.com",
        "hotmail": "outlook.office365.com",
        "yahoo": "imap.mail.yahoo.com",
    }
    host = target.get("imap_host") or host_map.get(provider) or "imap.gmail.com"
    port = int(target.get("imap_port") or 993)
    try:
        n = await asyncio.to_thread(
            _imap_login_test, host, port, True,
            target.get("address") or "", target.get("password") or "", 12,
        )
        return {"ok": True, "email": target.get("address"), "inbox_count": n,
                "elapsed_ms": int((time.time() - started) * 1000)}
    except Exception as e:
        return {"ok": False, "email": target.get("address"),
                "error": f"{e.__class__.__name__}: {e}",
                "elapsed_ms": int((time.time() - started) * 1000)}


async def _test_proxy_library(user_id: int) -> dict:
    started = time.time()
    proxies = proxy_store.list_proxies(user_id)
    if not proxies:
        return {"ok": False, "error": "Thư viện chưa có proxy nào. Thêm tại /library?tab=proxy"}
    target = next((p for p in proxies if p.get("last_test_result") == "ok"), None) or proxies[0]
    url = target.get("url") or ""
    if not url:
        return {"ok": False, "error": "Proxy đầu tiên không có URL hợp lệ"}
    try:
        async with httpx.AsyncClient(proxy=url, timeout=12, follow_redirects=True) as client:
            r = await client.get("https://api.ipify.org?format=json")
            r.raise_for_status()
            ip = r.json().get("ip", "")
        return {"ok": True, "ip": ip, "proxy_name": target.get("name") or target.get("id"),
                "elapsed_ms": int((time.time() - started) * 1000)}
    except Exception as e:
        return {"ok": False, "proxy_name": target.get("name") or target.get("id"),
                "error": f"{e.__class__.__name__}: {e}",
                "elapsed_ms": int((time.time() - started) * 1000)}


_TEST_DISPATCH = {
    "llm": lambda uid: _test_llm(),
    "capsolver": lambda uid: _test_capsolver(),
    "sms": lambda uid: _test_sms(),
    "imap": lambda uid: _test_imap_library(uid),
    "proxy": lambda uid: _test_proxy_library(uid),
}


@router.post("/test-one")
async def test_one(key: str, user: User = Depends(get_current_user)):
    """Test 1 subsystem theo `key` ∈ {llm, capsolver, sms, imap, proxy}."""
    fn = _TEST_DISPATCH.get(key)
    if not fn:
        return {"ok": False, "error": f"Unknown key: {key}"}
    t0 = time.time()
    r = await fn(user.id)
    if "elapsed_ms" not in r:
        r["elapsed_ms"] = int((time.time() - t0) * 1000)
    return {"key": key, "result": r}


@router.post("/test-all")
async def test_all(user: User = Depends(get_current_user)):
    """Chạy song song mọi test kết nối thật → trả về detail từng subsystem.

    Trả {results: {llm, capsolver, sms, imap, proxy}, started_at, total_ms}
    """
    t0 = time.time()
    llm, cap, sms, imap, proxy = await asyncio.gather(
        _test_llm(),
        _test_capsolver(),
        _test_sms(),
        _test_imap_library(user.id),
        _test_proxy_library(user.id),
        return_exceptions=False,
    )
    return {
        "results": {
            "llm": llm,
            "capsolver": cap,
            "sms": sms,
            "imap": imap,
            "proxy": proxy,
        },
        "total_ms": int((time.time() - t0) * 1000),
    }
