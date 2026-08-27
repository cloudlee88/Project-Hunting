"""Discovery crawl wrapped as a tracked CrawlJob.

Gives the discovery crawl the same job lifecycle as the other crawlers:
  - a row in `crawl_jobs` (source="discovery") that shows up on the Jobs screen
  - live progress: total_found grows as candidates are discovered
  - the source's status flips to "crawling" → "active" so the discovery screen
    can show an in-progress indicator
"""
from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime
from typing import Optional

from sqlalchemy import select, func

from app.core.db import SessionLocal
from app.core.logger import get_logger
from app.models import CrawlJob
from app.models.discovery import DiscoverySource, DiscoveryCandidate
from app.services import job_service
from app.services.discovery.crawler import crawl_discovery_source

log = get_logger("discovery.job")

PROGRESS_INTERVAL_SEC = 5


async def _count_candidates(source_id: int) -> int:
    async with SessionLocal() as s:
        return (await s.execute(
            select(func.count(DiscoveryCandidate.id)).where(DiscoveryCandidate.source_id == source_id)
        )).scalar() or 0


async def _set_source_status(source_id: int, status: str, live_total: Optional[int] = None) -> None:
    async with SessionLocal() as s:
        src = await s.get(DiscoverySource, source_id)
        if not src:
            return
        src.status = status
        if live_total is not None:
            src.total_candidates_found = live_total
        await s.commit()


async def _progress_loop(job_id: int, source_id: int, baseline: int, stop: asyncio.Event) -> None:
    """Update job.total_found (new candidates this run) + live source count every few seconds."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=PROGRESS_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass
        # If we were woken by stop (not the timeout), exit WITHOUT another write —
        # otherwise we'd overwrite the final "active" status with "crawling".
        if stop.is_set():
            break
        try:
            total = await _count_candidates(source_id)
            found = max(0, total - baseline)
            async with SessionLocal() as s:
                await job_service.update_job(job_id, s, total_found=found, total_saved=found)
            await _set_source_status(source_id, "crawling", live_total=total)
        except Exception:
            log.debug("progress update failed", exc_info=True)


async def run_discovery_crawl_job(
    source_id: int,
    user_id: Optional[int] = None,
    proxy_urls: Optional[list[str]] = None,
    proxy_labels: Optional[list[str]] = None,
    max_listing_pages: Optional[int] = None,
    incremental: bool = False,
) -> int:
    """Entry point used by the API. Creates the job, runs the crawl with live
    progress, finalizes job + source status. Returns the job id."""
    proxy_urls = proxy_urls or []
    proxy_labels = proxy_labels or []
    if proxy_urls:
        proxy_desc = f"pool {len(proxy_urls)} IP"
    else:
        proxy_desc = "không"
    async with SessionLocal() as s:
        src = await s.get(DiscoverySource, source_id)
        params = {
            "source_id": source_id,
            "url": src.url if src else None,
            "name": src.name if src else None,
            "max_pages": max_listing_pages or "all",
            "proxy": proxy_desc,
            "mode": "quét mới" if incremental else "toàn bộ",
        }
        job = await job_service.create_job("discovery", s, user_id=user_id, params=params)
        job_id = job.id
        await job_service.update_job(job_id, s, status="running", started_at=datetime.utcnow())

    baseline = await _count_candidates(source_id)
    await _set_source_status(source_id, "crawling")

    stop = asyncio.Event()
    reporter = asyncio.create_task(_progress_loop(job_id, source_id, baseline, stop))

    try:
        await crawl_discovery_source(
            source_id, proxy_urls=proxy_urls, proxy_labels=proxy_labels,
            max_listing_pages=max_listing_pages, incremental=incremental,
        )
        total = await _count_candidates(source_id)
        found = max(0, total - baseline)
        async with SessionLocal() as s:
            await job_service.update_job(
                job_id, s,
                status="success",
                total_found=found,
                total_saved=found,
                finished_at=datetime.utcnow(),
            )
        await _set_source_status(source_id, "active", live_total=total)
        log.info("Discovery job #%s done — source #%s, +%s candidates", job_id, source_id, found)
    except Exception as e:
        log.exception("Discovery job #%s failed", job_id)
        async with SessionLocal() as s:
            await job_service.update_job(
                job_id, s,
                status="failed",
                error=f"{e}\n{traceback.format_exc()[-800:]}",
                finished_at=datetime.utcnow(),
            )
        await _set_source_status(source_id, "active")
    finally:
        stop.set()
        try:
            await reporter
        except Exception:
            pass

    return job_id
