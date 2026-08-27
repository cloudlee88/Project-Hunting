"""
Q&A Match Engine — tìm câu trả lời phù hợp từ platform_qa_library.

Thứ tự ưu tiên match (cao → thấp):
  1. Exact program_id + exact question hash
  2. Platform + category + exact question hash
  3. Platform (chung) + exact question hash
  4. Fuzzy text match (similarity >= 0.75)

Template rendering hỗ trợ {profile.x}, {program.x}, và traffic_source_lookup:PlatformType.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# ── Traffic source cache (lazy-loaded from qa_library.json) ───────────────────

_TRAFFIC_CACHE: Optional[dict] = None


def _load_traffic_cache() -> dict:
    global _TRAFFIC_CACHE
    if _TRAFFIC_CACHE is not None:
        return _TRAFFIC_CACHE
    try:
        from app.core.config import settings
        json_path = settings.data_path("qa_library.json")
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        _TRAFFIC_CACHE = {
            "sources": data.get("traffic_sources", []),
            "mapping": data.get("category_mapping", {}),
        }
    except Exception as e:
        log.warning("Failed to load traffic cache: %s", e)
        _TRAFFIC_CACHE = {"sources": [], "mapping": {}}
    return _TRAFFIC_CACHE


def _invalidate_traffic_cache() -> None:
    global _TRAFFIC_CACHE
    _TRAFFIC_CACHE = None


def resolve_traffic_source_lookup(template: str, category: str, required: bool = True) -> str:
    """Resolve 'traffic_source_lookup:Website' or 'traffic_source_lookup:Youtube,Website' to real URL(s).

    Logic:
    - Map category → ngach via category_mapping
    - Query traffic_sources WHERE ngach=mapped AND platform=type → first match
    - If no match + required → fallback General Website
    - If no match + not required → return ""
    - Multi-type (Youtube,Website) → one of each, joined by newline
    """
    if not template.startswith("traffic_source_lookup:"):
        return template
    platform_types = [p.strip() for p in template[len("traffic_source_lookup:"):].split(",")]

    cache = _load_traffic_cache()
    sources = cache["sources"]
    mapping = cache["mapping"]

    cat_info = mapping.get(category or "") or mapping.get("General", {})
    ngach = cat_info.get("ngach", "General")

    results = []
    for pt in platform_types:
        link = next((s["link"] for s in sources if s.get("ngach") == ngach and s.get("platform") == pt), None)
        if link is None:
            if required:
                link = next((s["link"] for s in sources if s.get("ngach") == "General" and s.get("platform") == "Website"), None)
            else:
                continue
        if link:
            results.append(link)

    return "\n".join(results)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _q_hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode()).hexdigest()


def _similarity(a: str, b: str) -> float:
    """Tỷ lệ ký tự chung / tổng ký tự — đủ nhanh cho ~100 entries."""
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b:
        return 0.0
    # Check if b is a regex pattern that matches a
    try:
        if re.search(b, a, re.IGNORECASE):
            return 1.0
    except re.error:
        pass
    # Simple token overlap
    ta = set(re.split(r'\W+', a))
    tb = set(re.split(r'\W+', b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def _build_social_links(profile: dict) -> str:
    parts = []
    for key in ("linkedin", "twitter", "instagram", "youtube", "facebook"):
        val = profile.get(key, "")
        if val:
            parts.append(val)
    return ", ".join(parts) if parts else profile.get("website", "")


def _get_similar_programs(category: str) -> str:
    """Trả về danh sách program tên giả để điền vào template."""
    cat = (category or "").lower()
    mapping = {
        "saas": "Notion, Canva, Monday.com",
        "ai": "Jasper, Copy.ai, Midjourney",
        "finance": "Binance, Interactive Brokers, eToro",
        "health": "Noom, BetterHelp, Calm",
        "video": "Vimeo, Loom, Streamable",
        "audio": "Spotify for Podcasters, Buzzsprout",
    }
    for k, v in mapping.items():
        if k in cat:
            return v
    return "various SaaS and digital products"


def render_answer(answer_template: str, profile: dict, program: dict, required: bool = True) -> str:
    """Thay thế {profile.x} và {program.x} trong template. Resolve traffic_source_lookup nếu cần."""
    if answer_template.startswith("traffic_source_lookup:"):
        return resolve_traffic_source_lookup(answer_template, program.get("category", ""), required)
    first = profile.get("first_name") or profile.get("ten", "")
    last = profile.get("last_name") or profile.get("ho", "")
    full_name = profile.get("full_name") or f"{first} {last}".strip()

    replacements = {
        "{profile.first_name}": first,
        "{profile.last_name}": last,
        "{profile.full_name}": full_name,
        "{profile.email}": profile.get("_picked_email", {}).get("address", "") or profile.get("email", ""),
        "{profile.phone}": profile.get("phone", ""),
        "{profile.website}": profile.get("website", ""),
        "{profile.company}": profile.get("company", ""),
        "{profile.country}": profile.get("country", ""),
        "{profile.city}": profile.get("city", ""),
        "{profile.zip_code}": profile.get("zip", "") or profile.get("zip_code", ""),
        "{profile.linkedin}": profile.get("linkedin", ""),
        "{profile.telegram}": profile.get("telegram", ""),
        "{profile.youtube}": profile.get("youtube", ""),
        "{profile.instagram}": profile.get("instagram", ""),
        "{profile.facebook}": profile.get("facebook", ""),
        "{profile.twitter}": profile.get("twitter", ""),
        "{profile.paypal}": profile.get("paypal", ""),
        "{profile.password}": profile.get("password", ""),
        "{profile.coupon_code}": profile.get("coupon_code", ""),
        "{profile.social_links}": _build_social_links(profile),
        "{program.name}": program.get("name", ""),
        "{program.similar_programs}": _get_similar_programs(program.get("category", "")),
    }
    result = answer_template
    for key, val in replacements.items():
        result = result.replace(key, val or "")
    return result


# ── Main match function ───────────────────────────────────────────────────────

async def find_qa_answer(
    question_text: str,
    platform: str,
    category: Optional[str],
    program_id: Optional[int],
    min_confidence: float = 0.6,
) -> Optional[dict]:
    """
    Tìm Q&A phù hợp nhất từ library.
    Trả về dict entry hoặc None nếu không tìm thấy.
    """
    from sqlalchemy import select
    from app.core.db import SessionLocal
    from app.models.platform_qa import PlatformQAEntry

    q_hash = _q_hash(question_text)
    cat_lower = (category or "").lower()
    plat_lower = (platform or "").lower()

    async with SessionLocal() as session:
        # Load tất cả active entries cho platform này (tối đa ~100 entries → OK)
        all_entries = (
            await session.execute(
                select(PlatformQAEntry).where(
                    PlatformQAEntry.platform == plat_lower,
                    PlatformQAEntry.status == "active",
                    PlatformQAEntry.confidence >= min_confidence,
                )
            )
        ).scalars().all()

        if not all_entries:
            return None

        # Score each entry
        best: Optional[PlatformQAEntry] = None
        best_score = -1.0

        for entry in all_entries:
            # Tính điểm ưu tiên (priority tier: 1=exact program, 2=platform+cat, 3=platform)
            if entry.program_id is not None and entry.program_id == program_id:
                priority = 3.0
            elif (entry.category or "").lower() == cat_lower and cat_lower:
                priority = 2.0
            elif entry.category is None:
                priority = 1.0
            else:
                # Wrong category, skip
                continue

            # Hash exact match
            if entry.question_hash == q_hash:
                sim = 1.0
            else:
                # Regex / fuzzy match
                sim = _similarity(question_text, entry.question_pattern)
                if sim < 0.6:
                    continue

            score = priority + sim * entry.confidence
            if score > best_score:
                best_score = score
                best = entry

        if best is None:
            return None

        return {
            "id": best.id,
            "platform": best.platform,
            "category": best.category,
            "question_pattern": best.question_pattern,
            "answer_template": best.answer_template,
            "answer_type": best.answer_type,
            "required": best.required,
            "confidence": best.confidence,
            "note": best.note,
        }


# ── Confidence updater ────────────────────────────────────────────────────────

async def update_qa_confidence(qa_id: int, success: bool) -> None:
    """Cập nhật stats sau khi dùng 1 Q&A entry."""
    from sqlalchemy import select
    from app.core.db import SessionLocal
    from app.models.platform_qa import PlatformQAEntry

    async with SessionLocal() as session:
        entry = (
            await session.execute(select(PlatformQAEntry).where(PlatformQAEntry.id == qa_id))
        ).scalar_one_or_none()
        if not entry:
            return

        entry.usage_count = (entry.usage_count or 0) + 1
        entry.updated_at = datetime.utcnow()

        if success:
            entry.success_count = (entry.success_count or 0) + 1
            entry.consecutive_failures = 0
        else:
            entry.failure_count = (entry.failure_count or 0) + 1
            entry.consecutive_failures = (entry.consecutive_failures or 0) + 1

        # Tính lại confidence
        total = (entry.success_count or 0) + (entry.failure_count or 0)
        if total > 0:
            entry.confidence = round((entry.success_count or 0) / total, 3)

        # Đánh dấu needs_review nếu fail liên tiếp >= 3
        if (entry.consecutive_failures or 0) >= 3:
            entry.status = "needs_review"

        await session.commit()


# ── Learning log writer ───────────────────────────────────────────────────────

async def log_learning_event(
    *,
    job_id: Optional[int] = None,
    program_id: Optional[int] = None,
    platform: Optional[str] = None,
    category: Optional[str] = None,
    event_type: str,
    question: Optional[str] = None,
    answer_used: Optional[str] = None,
    qa_library_id: Optional[int] = None,
    selector_old: Optional[str] = None,
    selector_new: Optional[str] = None,
    tier_used: Optional[int] = None,
    token_cost: float = 0.0,
    outcome: Optional[str] = None,
) -> None:
    """Ghi 1 event vào script_learning_log."""
    from app.core.db import SessionLocal
    from app.models.platform_qa import ScriptLearningLog

    try:
        async with SessionLocal() as session:
            entry = ScriptLearningLog(
                job_id=job_id,
                program_id=program_id,
                platform=platform,
                category=category,
                event_type=event_type,
                question=question,
                answer_used=answer_used,
                qa_library_id=qa_library_id,
                selector_old=selector_old,
                selector_new=selector_new,
                tier_used=tier_used,
                token_cost=token_cost,
                outcome=outcome,
            )
            session.add(entry)
            await session.commit()
    except Exception as e:
        log.warning("log_learning_event failed: %s", e)


# ── Q&A hints block for T3 agent ─────────────────────────────────────────────

async def get_qa_hints_for_platform(
    platform: str,
    category: Optional[str],
    profile: dict,
    program: dict,
) -> str:
    """Trả về block Q&A hints cho T3 fallback agent (script_llm mode only).
    Không dùng cho Auto Signup. Chỉ gồm entries có câu trả lời thực sự.
    """
    from sqlalchemy import select
    from app.core.db import SessionLocal
    from app.models.platform_qa import PlatformQAEntry

    plat_lower = (platform or "").lower()
    cat_lower = (category or "").lower()
    if not plat_lower:
        return ""

    _SKIP_TYPES = ("profile_field",)
    _BASIC_PAT = (
        "first name", "last name", "^email$", "^password$", "^country$",
        "phone", "^website$", "vat id", "tax id", "telegram", "linkedin url",
        "paypal", "coupon", "^name$", "full name", "^facebook$", "^youtube$",
        "^instagram$", "^twitter$",
    )

    async with SessionLocal() as session:
        entries = (
            await session.execute(
                select(PlatformQAEntry).where(
                    PlatformQAEntry.platform == plat_lower,
                    PlatformQAEntry.status == "active",
                    PlatformQAEntry.confidence >= 0.6,
                )
            )
        ).scalars().all()

    if not entries:
        return ""

    lines: list[str] = []
    for entry in entries:
        if entry.category and entry.category.lower() != cat_lower:
            continue
        if entry.answer_type in _SKIP_TYPES:
            continue
        if entry.answer_type == "traffic_source_lookup" or entry.answer_template.startswith("traffic_source_lookup:"):
            continue
        if entry.answer_template.strip() in ("SELECT_AVAILABLE", ""):
            continue
        pat_low = entry.question_pattern.lower()
        if any(b in pat_low for b in _BASIC_PAT):
            continue
        rendered = render_answer(entry.answer_template, profile, program, required=entry.required)
        if not rendered.strip():
            continue
        lines.append(f'  - "{entry.question_pattern}": {rendered}')

    if not lines:
        return ""

    return (
        "### Pre-built câu trả lời form (dùng chính xác khi gặp câu hỏi tương tự, KHÔNG tự tạo):\n"
        + "\n".join(lines)
    )


# ── Seed from JSON ────────────────────────────────────────────────────────────

async def seed_qa_library_if_empty(force: bool = False) -> int:
    """Import qa_library.json nếu bảng đang trống (hoặc force=True). Trả về số entries đã seed."""
    from sqlalchemy import func as sqlfunc, select
    from app.core.db import SessionLocal
    from app.models.platform_qa import PlatformQAEntry
    from app.core.config import settings

    if not force:
        async with SessionLocal() as session:
            count_row = (
                await session.execute(select(sqlfunc.count()).select_from(PlatformQAEntry))
            ).scalar()
            if count_row and count_row > 0:
                return 0  # Đã có data, không seed lại

    # Load JSON
    json_path = settings.data_path("qa_library.json")
    if not json_path.exists():
        log.warning("qa_library.json not found at %s", json_path)
        return 0

    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    entries_data = data.get("qa_library", [])
    if not entries_data:
        return 0

    now = datetime.utcnow()
    async with SessionLocal() as session:
        for e in entries_data:
            pattern = e.get("question_pattern", "")
            entry = PlatformQAEntry(
                platform=(e.get("platform") or "").lower(),
                category=e.get("category"),
                program_id=None,
                question_pattern=pattern,
                question_hash=_q_hash(pattern),
                answer_template=e.get("answer_template", ""),
                answer_type=e.get("answer_type", "static"),
                required=bool(e.get("required", True)),
                confidence=float(e.get("confidence", 0.95)),
                usage_count=0,
                success_count=0,
                failure_count=0,
                consecutive_failures=0,
                status="active",
                source="seed",
                note=e.get("note"),
                created_at=now,
                updated_at=now,
            )
            session.add(entry)
        await session.commit()

    _invalidate_traffic_cache()  # reload traffic_sources từ JSON mới
    log.info("Seeded %d Q&A entries from qa_library.json", len(entries_data))
    return len(entries_data)
