"""SerpAPI Google Ads Transparency Center client.

Đơn giản: 1 file, dùng httpx.AsyncClient. Xoay key khi gặp 429.
Tham khảo docs/SERPAPI.md.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger("serpapi")

_BASE_URL = "https://serpapi.com/search.json"
_TIMEOUT = 30.0

# `_idx` = key tốt gần nhất, chia sẻ giữa các call chỉ như GỢI Ý điểm bắt đầu.
# Mỗi _call tự duyệt LẦN LƯỢT hết mọi key nên dù chạy song song cũng không bao giờ
# bỏ sót key còn quota. (Bug cũ: N call song song cùng cộng dồn `_idx` qua _rotate_key
# → `_idx` nhảy vọt về lại key đã hết → tất cả 429 → 0 kết quả dù còn key còn quota.)
_lock = asyncio.Lock()
_idx = 0


def _keys() -> list[str]:
    return settings.serpapi_keys_list


async def _call(params: dict[str, Any]) -> dict[str, Any]:
    """Gọi SerpAPI, tự thử lần lượt MỌI key khi gặp 429 (an toàn khi chạy song song).
    Raise HTTPStatusError cho lỗi khác; raise RuntimeError khi tất cả key đều 429."""
    global _idx
    keys = _keys()
    if not keys:
        raise RuntimeError("SERPAPI_KEYS chưa cấu hình trong .env")
    async with _lock:
        start = _idx
    last_exc: Optional[httpx.HTTPStatusError] = None
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for offset in range(len(keys)):
            i = (start + offset) % len(keys)
            r = await client.get(_BASE_URL, params={**params, "api_key": keys[i]})
            if r.status_code == 429:
                log.warning("SerpAPI 429 trên key index=%s — thử key khác", i)
                continue
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                last_exc = e
                raise
            if i != start:
                async with _lock:
                    _idx = i        # nhớ key vừa chạy được cho các call sau
            return r.json()
    if last_exc:
        raise last_exc
    raise RuntimeError("Tất cả SerpAPI key đều bị 429")


async def search_ads_transparency(
    text: str = "",
    advertiser_id: str = "",
    platform: str = "",
    creative_format: str = "",
    start_date: str = "",
    end_date: str = "",
    region: str = "",
    political_ads: bool = False,
    num: int = 40,
    next_page_token: str = "",
) -> dict[str, Any]:
    """Search Google Ads Transparency Center.

    Tham số phải khớp engine `google_ads_transparency_center`. Xem docs/SERPAPI.md §7.
    """
    params: dict[str, Any] = {
        "engine": "google_ads_transparency_center",
    }
    if text:
        params["text"] = text
    if advertiser_id:
        params["advertiser_id"] = advertiser_id
    if platform:
        params["platform"] = platform
    if creative_format:
        params["creative_format"] = creative_format.lower()
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    if region:
        params["region"] = region
    if political_ads:
        params["political_ads"] = "true"
    if num:
        params["num"] = int(num)
    if next_page_token:
        params["next_page_token"] = next_page_token
    return await _call(params)


async def get_ad_details(advertiser_id: str, creative_id: str, region: str = "") -> dict[str, Any]:
    params: dict[str, Any] = {
        "engine": "google_ads_transparency_center_ad_details",
        "advertiser_id": advertiser_id,
        "creative_id": creative_id,
    }
    if region:
        params["region"] = region
    return await _call(params)
