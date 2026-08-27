from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.discovery import DiscoveryCandidate

TRACKING_PATTERNS = [
    r'\?ref=', r'\?aff=', r'bit\.ly', r'/go/', r'/out/',
    r'/redirect', r'\butm_', r'/r/', r'/track/',
]
_TRACKING_RE = re.compile('|'.join(TRACKING_PATTERNS), re.IGNORECASE)

FETCH_TIMEOUT = 10.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def is_tracking_link(url: str) -> bool:
    return bool(_TRACKING_RE.search(url))


async def resolve_homepage(candidate_id: int, session: AsyncSession) -> None:
    c = (await session.execute(
        select(DiscoveryCandidate).where(DiscoveryCandidate.id == candidate_id)
    )).scalar_one_or_none()
    if not c:
        return

    parsed = urlparse(c.raw_url)
    resolved = True

    if is_tracking_link(c.raw_url):
        try:
            async with httpx.AsyncClient(
                timeout=FETCH_TIMEOUT,
                follow_redirects=True,
                headers=HEADERS,
            ) as client:
                resp = await client.get(c.raw_url)
                final = urlparse(str(resp.url))
                homepage = f"{final.scheme}://{final.netloc}/"
        except Exception:
            homepage = f"{parsed.scheme}://{parsed.netloc}/"
            resolved = False
    else:
        homepage = f"{parsed.scheme}://{parsed.netloc}/"

    c.homepage_url = homepage
    c.is_redirect_resolved = resolved
    c.status = "homepage_resolved"
    c.updated_at = datetime.utcnow()
    await session.commit()
