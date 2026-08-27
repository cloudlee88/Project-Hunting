"""
Trích xuất playbook steps từ browser-use AgentHistoryList sau khi LLM chạy thành công.
Kết quả là danh sách bước JSON có thể replay bằng Playwright (không cần LLM).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app.core.db import SessionLocal

log = logging.getLogger(__name__)

# ── Variable templates ──────────────────────────────────────────────────────

_PROFILE_FIELDS = [
    "email", "first_name", "last_name", "full_name",
    "company", "website", "phone", "address", "city",
    "country", "zip", "state", "niche",
]


def _parameterize(text: str, profile: dict) -> str:
    """Thay giá trị cụ thể bằng template variable nếu khớp với profile."""
    if not text:
        return text
    t = text.strip()
    tl = t.lower()

    for field in _PROFILE_FIELDS:
        val = profile.get(field, "")
        if val and str(val).strip().lower() == tl:
            return f"{{{{profile.{field}}}}}"

    # Email pattern
    if re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", t):
        return "{{profile.email}}"

    return t


# ── Selector building ───────────────────────────────────────────────────────

def _elem_selector(elem: Any) -> str:
    """Xây selector ưu tiên: id > name > data-testid > placeholder > xpath."""
    if not elem:
        return ""
    attrs: dict = {}
    try:
        attrs = dict(getattr(elem, "attributes", {}) or {})
    except Exception:
        pass

    tag = (getattr(elem, "tag_name", "") or "").lower()

    # Submit button
    if tag in ("button", "input") and attrs.get("type") in ("submit", "button"):
        text = (getattr(elem, "text", "") or "").strip()
        if text:
            return f"text={text}"
        return f"{tag}[type='{attrs['type']}']"

    if attrs.get("id"):
        return f"#{_esc(attrs['id'])}"
    if attrs.get("name"):
        return f"[name='{_esc(attrs['name'])}']"
    if attrs.get("data-testid"):
        return f"[data-testid='{_esc(attrs['data-testid'])}']"
    if attrs.get("placeholder"):
        return f"[placeholder='{_esc(attrs['placeholder'])}']"
    if attrs.get("type") and tag == "input":
        return f"input[type='{attrs['type']}']"

    # Fallback to xpath
    xpath = getattr(elem, "xpath", "") or ""
    if xpath:
        return f"xpath={xpath}"

    return ""


def _esc(s: str) -> str:
    return s.replace("'", "\\'")


def _elem_label(elem: Any) -> str:
    if not elem:
        return ""
    try:
        attrs = dict(getattr(elem, "attributes", {}) or {})
        return (
            attrs.get("placeholder")
            or attrs.get("aria-label")
            or attrs.get("name")
            or attrs.get("id")
            or (getattr(elem, "text", "") or "")[:40]
            or ""
        )
    except Exception:
        return ""


# ── Core extractor ──────────────────────────────────────────────────────────

def _extract_steps(history: Any, program: dict, profile: dict) -> list[dict]:
    """Parse browser-use AgentHistoryList → list of playbook step dicts."""
    signup_url = program.get("signup_url") or program.get("url") or ""
    steps: list[dict] = [{"action": "navigate", "url": "{{signup_url}}"}]

    for hist_item in (getattr(history, "history", []) or []):
        model_output = getattr(hist_item, "model_output", None)
        state = getattr(hist_item, "state", None)
        if not model_output:
            continue

        actions = getattr(model_output, "action", None) or []
        # interacted_element: list[Optional[DOMHistoryElement]], 1 per action
        interacted: list = []
        try:
            ie = getattr(state, "interacted_element", None)
            interacted = list(ie) if ie else []
        except Exception:
            pass

        for act_idx, action in enumerate(actions):
            elem = interacted[act_idx] if act_idx < len(interacted) else None

            try:
                act_dict: dict = {}
                try:
                    act_dict = action.model_dump(exclude_none=True)
                except AttributeError:
                    act_dict = dict(action) if isinstance(action, dict) else {}

                for act_name, act_data in act_dict.items():
                    if isinstance(act_data, str):
                        # Some fields store URL directly as string
                        act_data = {"url": act_data} if act_name == "go_to_url" else {}

                    if act_name == "go_to_url":
                        url = (act_data or {}).get("url", "")
                        if url and url.rstrip("/") != signup_url.rstrip("/"):
                            steps.append({"action": "navigate", "url": url})

                    elif act_name == "input_text":
                        text = (act_data or {}).get("text", "")
                        sel = _elem_selector(elem)
                        if sel and text:
                            steps.append({
                                "action": "fill",
                                "selector": sel,
                                "value": _parameterize(text, profile),
                                "label": _elem_label(elem),
                            })

                    elif act_name == "click_element":
                        sel = _elem_selector(elem)
                        if sel:
                            steps.append({
                                "action": "click",
                                "selector": sel,
                                "label": _elem_label(elem),
                            })

                    elif act_name == "select_dropdown_option":
                        sel = _elem_selector(elem)
                        opt = (act_data or {}).get("text", "") or (act_data or {}).get("value", "")
                        if sel:
                            steps.append({
                                "action": "select",
                                "selector": sel,
                                "value": _parameterize(opt, profile),
                                "label": _elem_label(elem),
                            })

                    elif act_name == "check_checkbox":
                        sel = _elem_selector(elem)
                        if sel:
                            steps.append({"action": "check", "selector": sel})

                    # Skip: scroll_down, scroll_up, done, wait, etc.

            except Exception as e:
                log.debug("Step extraction skipped for %s: %s", act_idx, e)
                continue

    # Success assertion — common patterns across affiliate signup pages
    steps.append({"action": "wait", "ms": 2000})
    steps.append({
        "action": "assert_success",
        "patterns": [
            "thank you", "thanks", "check your email", "verify",
            "registered", "confirmation", "success", "congratulations",
            "đã đăng ký", "cảm ơn",
        ],
    })

    return steps


def _build_name(program: dict) -> str:
    """Sinh tên playbook tự động: {platform}_{category}."""
    platform = (program.get("source") or "unknown").lower().replace(" ", "_")
    category = (program.get("category") or "general").lower().replace(" ", "_")
    # Giữ chỉ alphanum + underscore
    platform = re.sub(r"[^\w]", "_", platform).strip("_")
    category = re.sub(r"[^\w]", "_", category).strip("_")
    return f"{platform}_{category}"


# ── Public API ──────────────────────────────────────────────────────────────

async def extract_and_upsert(
    history: Any,
    program: dict,
    profile: dict,
    user_id: int,
    job_id: int,
) -> Optional[int]:
    """
    Trích xuất steps từ history, lưu/cập nhật playbook trong DB.
    - Nếu đã có playbook cho program này → cập nhật steps + reset consecutive_fails.
    - Nếu chưa → tạo mới với status="active".
    Trả về playbook_id hoặc None nếu extraction thất bại.
    """
    from sqlalchemy import select
    from app.models.signup_playbook import SignupPlaybook

    try:
        steps = _extract_steps(history, program, profile)
    except Exception as e:
        log.warning("[job=%s] extract_steps failed: %s", job_id, e)
        return None

    # Cần ít nhất: navigate + 1 fill/click + assert_success
    meaningful = [s for s in steps if s.get("action") in ("fill", "click", "select", "check")]
    if not meaningful:
        log.info("[job=%s] No meaningful steps extracted, skipping playbook save", job_id)
        return None

    program_id = program.get("id")
    name = _build_name(program)
    platform = (program.get("source") or "").lower()
    category = (program.get("category") or "").lower()

    async with SessionLocal() as session:
        # Tìm playbook hiện có cho program này
        existing = None
        if program_id:
            existing = (
                await session.execute(
                    select(SignupPlaybook).where(
                        SignupPlaybook.user_id == user_id,
                        SignupPlaybook.program_id == program_id,
                        SignupPlaybook.status != "archived",
                    )
                )
            ).scalar_one_or_none()

        if existing:
            existing.steps_json = json.dumps(steps, ensure_ascii=False)
            existing.consecutive_fails = 0
            existing.pending_llm_approval = False
            existing.status = "active"
            existing.created_from_job_id = job_id
            from datetime import datetime
            existing.updated_at = datetime.utcnow()
            await session.commit()
            log.info("[job=%s] Playbook #%s updated (%d steps)", job_id, existing.id, len(steps))
            return existing.id
        else:
            pb = SignupPlaybook(
                user_id=user_id,
                name=name,
                platform=platform,
                category=category,
                program_id=program_id,
                program_name=program.get("name", ""),
                signup_url=program.get("signup_url") or program.get("url") or "",
                status="active",
                steps_json=json.dumps(steps, ensure_ascii=False),
                created_from_job_id=job_id,
                consecutive_fails=0,
            )
            session.add(pb)
            await session.commit()
            await session.refresh(pb)
            log.info("[job=%s] Playbook #%s created (%d steps) name=%s", job_id, pb.id, len(steps), name)
            return pb.id
