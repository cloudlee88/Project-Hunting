"""Per-user captcha solving memory.

Lưu kiến thức giải captcha theo user_id để:
- Inject vào agent prompt (học từ lần trước)
- User có thể edit JSON file trực tiếp
- API GET/PUT để FE hiển thị và chỉnh sửa
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)

_DEFAULT_MEMORY: dict = {
    "version": 1,
    "user_notes": "",
    "sites": {},
}

_DEFAULT_NOTES = """# Ghi chú cá nhân về giải captcha (Agent sẽ đọc và học từ đây)
# Ví dụ:
# - Sau khi giải reCAPTCHA image challenge, đợi 2 giây rồi click nút Apply/Submit
# - Site X dùng Turnstile → dùng solve_cloudflare_turnstile(sitekey)
"""


def _mem_dir() -> Path:
    d = settings.data_dir / "captcha_memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mem_path(user_id: int) -> Path:
    return _mem_dir() / f"{user_id}.json"


def load_memory(user_id: int) -> dict:
    """Load captcha memory cho user. Trả về default nếu chưa có."""
    p = _mem_path(user_id)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # Merge với default để đảm bảo đủ keys
            mem = dict(_DEFAULT_MEMORY)
            mem.update(data)
            return mem
        except Exception as e:
            log.warning(f"captcha_memory load error uid={user_id}: {e}")
    # Tạo file default lần đầu
    mem = dict(_DEFAULT_MEMORY)
    mem["user_notes"] = _DEFAULT_NOTES.strip()
    return mem


def save_memory(user_id: int, mem: dict) -> None:
    """Save captcha memory cho user."""
    p = _mem_path(user_id)
    try:
        p.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"captcha_memory save error uid={user_id}: {e}")


def add_captcha_success(
    user_id: int,
    site: str,
    captcha_type: str,
    sitekey: str = "",
    tips: list[str] | None = None,
) -> None:
    """Ghi nhận 1 lần giải captcha thành công. Tự merge vào memory file."""
    if not user_id:
        return
    mem = load_memory(user_id)
    sites: dict = mem.setdefault("sites", {})
    entry: dict = sites.setdefault(site, {})
    entry["type"] = captcha_type
    if sitekey:
        entry["sitekey"] = sitekey
    entry["successes"] = int(entry.get("successes") or 0) + 1
    entry["last_success"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if tips:
        existing: list = entry.get("tips") or []
        for t in tips:
            if t and t not in existing:
                existing.append(t)
        entry["tips"] = existing
    save_memory(user_id, mem)
    log.info(f"captcha_memory updated uid={user_id} site={site} type={captcha_type} successes={entry['successes']}")


def format_memory_for_prompt(user_id: int, site: str = "") -> str:
    """Format memory thành đoạn text để inject vào agent prompt.

    Trả về chuỗi rỗng nếu memory rỗng/default.
    """
    if not user_id:
        return ""
    mem = load_memory(user_id)
    lines: list[str] = []

    notes = (mem.get("user_notes") or "").strip()
    if notes and not notes.startswith("#"):  # skip nếu chỉ là comment template
        lines.append("### Ghi chú captcha từ người dùng (đã verify hiệu quả):")
        lines.append(notes)
        lines.append("")

    sites: dict = mem.get("sites") or {}
    # Hiển thị site cụ thể trước (nếu có)
    if site and site in sites:
        entry = sites[site]
        lines.append(f"### Captcha memory — site: {site}")
        lines.append(f"- Loại captcha: {entry.get('type', '?')}")
        if entry.get("sitekey"):
            lines.append(f"- Sitekey: {entry['sitekey']}")
        if entry.get("tips"):
            lines.append("- Tips đã verify thành công:")
            for t in entry["tips"]:
                lines.append(f"  • {t}")
        lines.append(f"- Số lần thành công trước: {entry.get('successes', 0)}")
        lines.append("")

    # Hiển thị tất cả các site khác (tóm tắt)
    other_sites = {k: v for k, v in sites.items() if k != site}
    if other_sites:
        lines.append("### Sites đã từng solve thành công:")
        for s, e in other_sites.items():
            lines.append(f"  • {s}: {e.get('type','?')} (×{e.get('successes',0)}) last={e.get('last_success','?')}")
        lines.append("")

    return "\n".join(lines)


def raw_memory(user_id: int) -> dict:
    """Trả về raw memory dict cho API."""
    return load_memory(user_id)


def update_memory_raw(user_id: int, data: dict) -> dict:
    """Update raw memory từ API. Validate version field."""
    data["version"] = int(data.get("version") or 1)
    data.setdefault("user_notes", "")
    data.setdefault("sites", {})
    save_memory(user_id, data)
    return data
