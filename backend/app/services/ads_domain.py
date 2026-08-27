"""Trích domain quảng cáo từ creative của Google Ads Transparency (SerpAPI).

Google trả 2 kiểu creative:
  • Text ad → có `link` (content.js) chứa tham số `overlay` = gzip+base64 của dữ
    liệu ad; field `visurl` trong đó là domain hiển thị (vd measureup.com). Giải
    mã bằng code — MIỄN PHÍ.
  • Image ad → chỉ có `image` (ảnh render); domain nằm trong pixel → dùng
    Gemini-vision đọc (rẻ, ~1 call/ảnh). Đã cấu hình sẵn key ở llm_keyring.

`extract_domains()` chạy song song (giới hạn concurrency cho vision) và trả về
map {ad_creative_id: domain}.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import re
import zlib
from typing import Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from app.core.config import settings
from app.core.logger import get_logger
from app.services.llm_keyring import get_gemini_key, gemini_key_count, mark_gemini_quota_error

log = get_logger("ads_domain")

VISION_CONCURRENCY = 2      # nhẹ tay với quota Gemini (429 RESOURCE_EXHAUSTED theo phút)
VISION_TIMEOUT_SEC = 10.0   # cap mỗi call (SDK google-genai tự retry nội bộ → phải chặn)


def _vision_models() -> list[str]:
    """Chỉ dùng model đã cấu hình. KHÔNG hardcode fallback 'gemini-flash-latest'
    (401 — service account hỏng) và 'gemini-2.0-flash' (404 — model gỡ bỏ): chúng
    luôn fail, chỉ gây nhiễu + tốn thời gian. Chống lỗi TẠM (429/503) bằng retry +
    xoay key (xem extract_domains pass-2) thay vì đổi sang model chết."""
    return [settings.signup_llm_model or "gemini-3.5-flash"]


_VISION_PROMPT = (
    "This image is a screenshot of a Google search ad. Return ONLY the advertiser's "
    "website domain shown in the ad (for example: example.com) — no protocol, no path, "
    "no www, no extra words. If no domain is visible, return exactly NONE."
)


def _normalize_domain(s: Optional[str]) -> Optional[str]:
    """Chuẩn hoá 1 chuỗi URL/host thành domain. KHÔNG cắt theo danh sách TLD (dễ
    hỏng .co.za, .com.my, .studio…). Chỉ bỏ scheme/path/www và validate dạng host."""
    if not s:
        return None
    s = s.strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0].split(":")[0]
    if s.startswith("www."):
        s = s[4:]
    m = re.match(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$", s)
    if not m or len(s) < 4:
        return None
    return s


def _visurl_from_link(link: Optional[str]) -> Optional[str]:
    """Giải `overlay` (gzip+base64) trong link content.js → domain từ field visurl.

    `visurl` là map<string,string> trong protobuf: sau khoá 'visurl' là
    `\\x12<olen>\\x0a<slen><value>` — value dài đúng <slen> byte. PHẢI đọc theo
    length-prefix, KHÔNG regex đoán biên (dễ dính byte tag field kế → 'blancvpn.comj')."""
    if not link:
        return None
    ov = parse_qs(urlparse(link).query).get("overlay", [None])[0]
    if not ov:
        return None
    ov = unquote(ov).lstrip("=")
    raw: Optional[bytes] = None
    for pad in ("", "=", "==", "==="):
        try:
            data = base64.urlsafe_b64decode(ov + pad)
        except Exception:
            continue
        for dec in (gzip.decompress, zlib.decompress, lambda b: zlib.decompress(b, -15)):
            try:
                raw = dec(data)
                break
            except Exception:
                continue
        if raw:
            break
    if not raw:
        return None

    i = raw.find(b"visurl")
    if i < 0:
        return None
    # Tìm tag string 0x0a ngay sau 'visurl' (qua header \x12<olen>), rồi đọc <slen> byte.
    seg = raw[i + 6: i + 12]
    k = seg.find(b"\x0a")
    if k >= 0:
        p = i + 6 + k + 1
        if p < len(raw):
            slen = raw[p]
            if 0 < slen < 128 and p + 1 + slen <= len(raw):
                val = raw[p + 1: p + 1 + slen].decode("utf-8", "replace")
                dom = _normalize_domain(val)
                if dom:
                    return dom
    # Fallback: regex trên text (ít tin cậy, chỉ khi cấu trúc byte khác thường).
    text = raw.decode("utf-8", "replace")
    m = re.search(r"visurl[\s\S]{0,8}?([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+)", text, re.I)
    return _normalize_domain(m.group(1)) if m else None


async def _domain_via_vision(image_url: str, client: httpx.AsyncClient) -> Optional[str]:
    """Đọc domain từ ảnh ad bằng Gemini-vision."""
    if gemini_key_count() == 0:
        return None
    try:
        resp = await client.get(image_url, timeout=20.0)
        if resp.status_code != 200 or not resp.content:
            return None
        img = resp.content
    except Exception as e:
        log.debug("tải ảnh ad lỗi: %s", e)
        return None
    import google.genai as genai
    from google.genai import types

    model = _vision_models()[0]
    attempts = min(max(2, gemini_key_count()), 4)   # xoay key, thử tối đa 4 lần
    for i in range(attempts):
        key, _, _ = get_gemini_key()   # xoay key; _rotate tự bỏ key đang bị tạm khoá
        try:
            gclient = genai.Client(api_key=key)
            # asyncio.wait_for để CHẶN retry nội bộ dài của SDK (nguyên nhân treo ~45s).
            r = await asyncio.wait_for(
                gclient.aio.models.generate_content(
                    model=model,
                    contents=[
                        types.Part.from_bytes(data=img, mime_type="image/png"),
                        types.Part.from_text(text=_VISION_PROMPT),
                    ],
                ),
                timeout=VISION_TIMEOUT_SEC,
            )
            out = (r.text or "").strip()
            if not out or out.upper().startswith("NONE"):
                return None            # ảnh không có domain nhìn thấy → kết luận
            return _normalize_domain(out)
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            # 429 hết quota / 401 key hỏng (service account disabled) → tạm khoá key
            # này 1h để lần sau xoay sang key tốt (né lặp lại lỗi trên cùng key).
            if any(k in low for k in ("429", "resource_exhausted", "quota", "rate limit",
                                      "401", "unauthenticated", "permission_denied")):
                mark_gemini_quota_error(key)
            log.warning("Gemini vision lỗi (model %s, %d/%d): %s", model, i + 1, attempts, msg[:110])
            if i < attempts - 1:
                await asyncio.sleep(0.6 * (i + 1))
                continue
            return None
    return None


async def extract_domains(creatives: List[dict]) -> Dict[str, Optional[str]]:
    """Trả {ad_creative_id: domain|None}. Text ad giải free; image ad dùng vision."""
    result: Dict[str, Optional[str]] = {}
    vision_jobs: List[tuple[str, str]] = []  # (creative_id, image_url)

    for c in creatives:
        cid = c.get("ad_creative_id") or ""
        if not cid:
            continue
        # Tạm thời BỎ QUA video ad: domain không có ở text/link, thumbnail video hầu
        # như không hiện domain → không tốn vision/quota cho nó.
        if (c.get("format") or "").lower() == "video":
            result[cid] = None
            continue
        dom = _normalize_domain(c.get("target_domain")) or _visurl_from_link(c.get("link"))
        if dom:
            result[cid] = dom
        elif c.get("image"):
            vision_jobs.append((cid, c["image"]))
            result[cid] = None
        else:
            result[cid] = None

    if vision_jobs:
        sem = asyncio.Semaphore(VISION_CONCURRENCY)
        # Circuit breaker: nếu Gemini đang hỏng (503/429/404) → nhiều call liên
        # tiếp fail thì NGƯNG gọi vision cho phần còn lại (khỏi treo lâu + đốt quota).
        st = {"fail": 0, "off": False}
        ok = 0
        async with httpx.AsyncClient(follow_redirects=True) as client:
            async def run(cid: str, url: str):
                nonlocal ok
                async with sem:
                    if st["off"]:
                        result[cid] = None
                        return
                    d = await _domain_via_vision(url, client)
                    result[cid] = d
                    if d is None:
                        st["fail"] += 1
                        if st["fail"] >= 8:
                            st["off"] = True
                            log.warning("ads_domain: Gemini vision hỏng liên tiếp → tạm ngưng vision cho batch")
                    else:
                        st["fail"] = 0
                        ok += 1
            await asyncio.gather(*(run(cid, url) for cid, url in vision_jobs))
        log.info("ads_domain: vision %d/%d ảnh đọc được domain%s",
                 ok, len(vision_jobs), " (đã ngắt vì Gemini không khả dụng)" if st["off"] else "")

    # ── Pass 2: retry TUẦN TỰ (giãn cách) các ad ảnh chưa ra domain ──
    # Quota Gemini (429) tính theo PHÚT nên gọi chậm rãi thường tự hồi → vớt lại
    # phần bị miss ở pass-1 (thực nghiệm: vớt 17/17). Cũng vớt ad bị circuit-breaker bỏ qua.
    pending = [(c.get("ad_creative_id"), c.get("image")) for c in creatives
               if c.get("ad_creative_id") and c.get("image")
               and (c.get("format") or "").lower() != "video"
               and result.get(c.get("ad_creative_id")) is None]
    if pending and gemini_key_count() > 0:
        got = 0
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for cid, url in pending:
                for _ in range(2):                       # 2 lần thử / ad, cách nhau
                    d = await _domain_via_vision(url, client)
                    if d:
                        result[cid] = d
                        got += 1
                        break
                    await asyncio.sleep(0.8)
        log.info("ads_domain: pass-2 vớt thêm %d/%d ad còn thiếu domain", got, len(pending))

    return result
