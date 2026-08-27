from __future__ import annotations

import logging
import time
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)

_gemini_index = 0
_openai_index = 0
_deepseek_index = 0
_blocked_until: dict[str, float] = {}
_BLOCK_SECONDS = 60 * 60


# ── Generic key-rotation helper ────────────────────────────────────────────────

def _rotate(keys: list[str], index_box: list[int], ns: str) -> tuple[str, int, int]:
    """Round-robin rotate keys, skipping temporarily blocked ones."""
    if not keys:
        return "", 0, 0
    now = time.time()
    total = len(keys)
    for offset in range(total):
        idx = (index_box[0] + offset) % total
        key = keys[idx]
        if _blocked_until.get(f"{ns}:{key}", 0) > now:
            continue
        index_box[0] = (idx + 1) % total
        return key, idx + 1, total
    # All blocked — return next anyway so the error surfaces to caller.
    idx = index_box[0] % total
    index_box[0] = (idx + 1) % total
    return keys[idx], idx + 1, total


# ── Gemini ─────────────────────────────────────────────────────────────────────

_gi = [0]  # use list so _rotate can mutate in-place


def gemini_keys() -> list[str]:
    return settings.gemini_api_keys_list


def gemini_key_count() -> int:
    return len(gemini_keys())


def get_gemini_key() -> tuple[str, int, int]:
    return _rotate(gemini_keys(), _gi, "gemini")


def get_gemini_key_by_index(index: int) -> tuple[str, int, int]:
    keys = gemini_keys()
    total = len(keys)
    if not total:
        return "", 0, 0
    if index < 1 or index > total:
        return get_gemini_key()
    return keys[index - 1], index, total


def mark_gemini_quota_error(key: str) -> None:
    if key:
        _blocked_until[f"gemini:{key}"] = time.time() + _BLOCK_SECONDS
        log.warning("Gemini key temporarily disabled for quota/rate-limit fallback")


# ── OpenAI ─────────────────────────────────────────────────────────────────────

_oai = [0]


def openai_keys() -> list[str]:
    return settings.openai_api_keys_list


def openai_key_count() -> int:
    return len(openai_keys())


def get_openai_key() -> tuple[str, int, int]:
    return _rotate(openai_keys(), _oai, "openai")


def get_openai_key_by_index(index: int) -> tuple[str, int, int]:
    keys = openai_keys()
    total = len(keys)
    if not total:
        return "", 0, 0
    if index < 1 or index > total:
        return get_openai_key()
    return keys[index - 1], index, total


def mark_openai_quota_error(key: str) -> None:
    if key:
        _blocked_until[f"openai:{key}"] = time.time() + _BLOCK_SECONDS
        log.warning("OpenAI key temporarily disabled for quota/rate-limit fallback")


# ── DeepSeek ───────────────────────────────────────────────────────────────────

_dsk = [0]


def deepseek_keys() -> list[str]:
    return settings.deepseek_api_keys_list


def deepseek_key_count() -> int:
    return len(deepseek_keys())


def get_deepseek_key() -> tuple[str, int, int]:
    return _rotate(deepseek_keys(), _dsk, "deepseek")


def get_deepseek_key_by_index(index: int) -> tuple[str, int, int]:
    keys = deepseek_keys()
    total = len(keys)
    if not total:
        return "", 0, 0
    if index < 1 or index > total:
        return get_deepseek_key()
    return keys[index - 1], index, total


def mark_deepseek_quota_error(key: str) -> None:
    if key:
        _blocked_until[f"deepseek:{key}"] = time.time() + _BLOCK_SECONDS
        log.warning("DeepSeek key temporarily disabled for quota/rate-limit fallback")


# ── Shared ─────────────────────────────────────────────────────────────────────

def is_quota_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    response: Any = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in {429, 403}:
        return True
    return any(
        marker in text
        for marker in (
            "429",
            "quota",
            "rate limit",
            "ratelimit",
            "resource_exhausted",
            "resource exhausted",
            "too many requests",
            "exceeded",
        )
    )
