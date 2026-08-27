"""Traffic scan job runner — asyncio.Queue worker quét SimilarWeb cho nhiều program.

Pattern giống signup_runner: 1 worker, queue id, mỗi job chạy semaphore song song.
Cập nhật progress vào DB sau mỗi program hoàn tất để FE poll được.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.logger import get_logger
from app.models.affiliate_program import AffiliateProgram
from app.models.traffic_scan_job import TrafficScanJob
from app.services.traffic import scan_traffic
from app.services.whois import scan_whois
from app.services.ads_advertisers import count_advertisers_for_domain

log = get_logger("traffic_runner")

_queue: "asyncio.Queue[int]" = asyncio.Queue()
_worker_task: Optional[asyncio.Task] = None
_recovery_task: Optional[asyncio.Task] = None


async def _load_job(session, job_id: int) -> TrafficScanJob | None:
    res = await session.execute(select(TrafficScanJob).where(TrafficScanJob.id == job_id))
    return res.scalar_one_or_none()


async def _run_job(job_id: int) -> None:
    # Đọc job + load programs
    async with SessionLocal() as session:
        job = await _load_job(session, job_id)
        if not job:
            return
        program_ids: list[int] = json.loads(job.program_ids_json or "[]")
        kind = job.kind or "traffic"
        skip_existing = bool(job.skip_existing)
        months = max(1, min(12, int(job.months or 12)))
        start_date = (job.start_date or "").strip()   # YYYYMMDD — kind="advertisers"
        end_date = (job.end_date or "").strip()
        # WHOIS (RDAP) nhẹ → nhiều luồng; advertisers (SerpAPI) vừa phải để né 429.
        _cap = 16 if kind == "whois" else (5 if kind == "advertisers" else 4)
        concurrency = max(1, min(_cap, int(job.concurrency or 2)))

        rows = (
            await session.execute(
                select(AffiliateProgram).where(AffiliateProgram.id.in_(program_ids))
            )
        ).scalars().all()
        by_id = {p.id: p for p in rows}
        # Giữ thứ tự ids gốc
        programs = [by_id[pid] for pid in program_ids if pid in by_id]

        job.status = "running"
        job.started_at = datetime.utcnow()
        job.total = len(program_ids)
        job.scanned = 0
        job.found = 0
        job.skipped = 0
        job.failed = 0
        job.results_json = "[]"
        job.error = None
        job.finished_at = None
        await session.commit()

    sem = asyncio.Semaphore(concurrency)
    results: list[dict] = []
    counts = {"scanned": 0, "found": 0, "skipped": 0, "failed": 0}
    lock = asyncio.Lock()  # bảo vệ counts/results khi append

    async def _persist_progress():
        """Snapshot counts + results vào DB để FE poll thấy."""
        async with SessionLocal() as s:
            j = await _load_job(s, job_id)
            if not j:
                return
            j.scanned = counts["scanned"]
            j.found = counts["found"]
            j.skipped = counts["skipped"]
            j.failed = counts["failed"]
            j.results_json = json.dumps(results, ensure_ascii=False)
            await s.commit()

    async def _one_whois(p: AffiliateProgram):
        # Chỉ bỏ qua kết quả CHẮC CHẮN (ok / not_found thật). 'error' (lỗi tạm) và
        # chưa quét → vẫn quét lại.
        if skip_existing and p.domain_whois_status in ("ok", "not_found"):
            async with lock:
                counts["skipped"] += 1
                results.append({"program_id": p.id, "name": p.name, "status": "skipped"})
            await _persist_progress()
            return
        url = p.url or p.signup_url or p.source_url
        if not url:
            async with lock:
                counts["failed"] += 1
                results.append({"program_id": p.id, "name": p.name, "status": "failed", "error": "không có URL"})
            await _persist_progress()
            return
        async with sem:
            try:
                result = await scan_whois(url)
            except Exception as e:  # noqa: BLE001
                log.warning("whois-scan failed id=%s url=%s: %s", p.id, url, e)
                result = {"created": None, "expires": None, "found": False, "status": "error"}
        status = result.get("status") or ("ok" if result.get("found") else "not_found")
        created, expires = result.get("created"), result.get("expires")
        async with SessionLocal() as s:
            prog = await s.get(AffiliateProgram, p.id)
            if prog:
                # 'error' (tạm) → CHỈ đánh dấu status, giữ ngày cũ nếu có, để lần sau retry.
                if status != "error":
                    prog.domain_created_at = created
                    prog.domain_expires_at = expires
                prog.domain_whois_scanned_at = datetime.utcnow()
                prog.domain_whois_status = status
                await s.commit()
        async with lock:
            if status == "error":
                counts["failed"] += 1
                results.append({"program_id": p.id, "name": p.name, "status": "failed", "error": "RDAP tạm lỗi/timeout"})
            else:
                counts["scanned"] += 1
                if result.get("found"):
                    counts["found"] += 1
                results.append({
                    "program_id": p.id, "name": p.name,
                    "status": "ok" if result.get("found") else "empty",
                    "created": created.isoformat() if created else None,
                    "expires": expires.isoformat() if expires else None,
                })
        await _persist_progress()

    async def _one_advertisers(p: AffiliateProgram):
        # Domain trang chủ thật (không dùng link signup/intermediate).
        from app.services.crawlers.homepage_finder import is_intermediate_url
        url = None
        if p.url and not is_intermediate_url(p.url):
            url = p.url
        elif p.source_url and not is_intermediate_url(p.source_url):
            url = p.source_url
        if not url:
            async with lock:
                counts["failed"] += 1
                results.append({"program_id": p.id, "name": p.name, "status": "failed", "error": "không có URL"})
            await _persist_progress()
            return
        async with sem:
            try:
                res = await count_advertisers_for_domain(url, start_date=start_date, end_date=end_date)
            except Exception as e:  # noqa: BLE001
                log.warning("advertisers-scan failed id=%s url=%s: %s", p.id, url, e)
                async with lock:
                    counts["failed"] += 1
                    results.append({"program_id": p.id, "name": p.name, "status": "failed", "error": str(e)[:200]})
                await _persist_progress()
                return
        count = int(res.get("count") or 0)
        detail = {
            "count": count,
            "has_more": bool(res.get("has_more")),
            "start": start_date, "end": end_date,
            "list": res.get("advertisers") or [],
        }
        async with SessionLocal() as s:
            prog = await s.get(AffiliateProgram, p.id)
            if prog:
                prog.ads_advertisers_count = count
                prog.ads_advertisers_json = json.dumps(detail, ensure_ascii=False)
                prog.ads_advertisers_scanned_at = datetime.utcnow()
                await s.commit()
        async with lock:
            counts["scanned"] += 1
            if count > 0:
                counts["found"] += 1
            results.append({
                "program_id": p.id, "name": p.name,
                "status": "ok" if count > 0 else "empty",
                "count": count, "has_more": bool(res.get("has_more")),
            })
        await _persist_progress()

    async def _one(p: AffiliateProgram):
        if kind == "whois":
            return await _one_whois(p)
        if kind == "advertisers":
            return await _one_advertisers(p)
        # Skip CHỈ khi đã có ĐẦY ĐỦ chi tiết traffic (không chỉ traffic_score). Nhiều dự án
        # quét từ bản cũ chỉ có score mà chưa có traffic_details_json → phải quét lại để có
        # page/visit, thời lượng, tỷ lệ thoát, diễn biến, quốc gia (đồng bộ với nút chi tiết).
        if skip_existing and p.traffic_score and p.traffic_score > 0 and p.traffic_details_json:
            async with lock:
                counts["skipped"] += 1
                results.append({
                    "program_id": p.id, "name": p.name,
                    "status": "skipped", "monthly_visits": int(p.traffic_score or 0),
                })
            await _persist_progress()
            return
        # Chọn URL trang chủ thực cho SimilarWeb: ưu tiên link KHÔNG phải affiliate/redirect
        # (is_intermediate) trong p.url → p.signup_url → p.source_url; nếu không có link "sạch"
        # thì vẫn dùng theo thứ tự đó (khớp nút quét chi tiết, tránh fail "không có URL").
        from app.services.crawlers.homepage_finder import is_intermediate_url
        _cands = [p.url, p.signup_url, p.source_url]
        url = next((u for u in _cands if u and not is_intermediate_url(u)), None) \
            or next((u for u in _cands if u), None)
        if not url:
            async with lock:
                counts["failed"] += 1
                results.append({
                    "program_id": p.id, "name": p.name,
                    "status": "failed", "error": "không có URL",
                })
            await _persist_progress()
            return
        async with sem:
            try:
                result = await scan_traffic(url, months=months)
            except Exception as e:  # noqa: BLE001
                log.warning("traffic-scan failed id=%s url=%s: %s", p.id, url, e)
                async with lock:
                    counts["failed"] += 1
                    results.append({
                        "program_id": p.id, "name": p.name,
                        "status": "failed", "error": str(e)[:200],
                    })
                await _persist_progress()
                return
        # Update program record trong session ngắn
        visits = int(result.get("monthly_visits") or 0)
        details = result.get("traffic_details")
        async with SessionLocal() as s:
            prog = await s.get(AffiliateProgram, p.id)
            if prog:
                prog.traffic_score = float(visits)
                prog.traffic_period_month = result.get("period_month")
                prog.traffic_details_json = (
                    json.dumps(details, ensure_ascii=False) if details else None
                )
                prog.traffic_scanned_at = datetime.utcnow()
                await s.commit()
        async with lock:
            counts["scanned"] += 1
            if result.get("found"):
                counts["found"] += 1
            results.append({
                "program_id": p.id, "name": p.name,
                "status": "ok" if result.get("found") else "empty",
                "monthly_visits": visits,
                "period_month": result.get("period_month"),
            })
        # Persist progress sau mỗi item (FE thấy real-time)
        await _persist_progress()

    try:
        await asyncio.gather(*[_one(p) for p in programs])
        async with SessionLocal() as session:
            j = await _load_job(session, job_id)
            if j:
                j.status = "success"
                j.scanned = counts["scanned"]
                j.found = counts["found"]
                j.skipped = counts["skipped"]
                j.failed = counts["failed"]
                j.results_json = json.dumps(results, ensure_ascii=False)
                j.finished_at = datetime.utcnow()
                await session.commit()
    except Exception as e:
        log.exception("Traffic job %s failed", job_id)
        async with SessionLocal() as session:
            j = await _load_job(session, job_id)
            if j:
                j.status = "failed"
                j.error = f"{e}\n{traceback.format_exc()[-800:]}"
                j.finished_at = datetime.utcnow()
                await session.commit()


async def _worker_loop() -> None:
    log.info("Traffic worker started")
    while True:
        try:
            job_id = await _queue.get()
        except asyncio.CancelledError:
            break
        try:
            await _run_job(job_id)
        except Exception:
            log.exception("Unhandled in traffic worker")
        finally:
            _queue.task_done()


async def _recover_unfinished_jobs() -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(TrafficScanJob).where(TrafficScanJob.status.in_(["pending", "running"]))
            )
        ).scalars().all()
        for job in rows:
            job.status = "pending"
            job.scanned = 0
            job.found = 0
            job.skipped = 0
            job.failed = 0
            job.results_json = "[]"
            job.error = None
            job.started_at = None
            job.finished_at = None
        await session.commit()

    for job in rows:
        await _queue.put(job.id)
    if rows:
        log.warning("Recovered %d unfinished traffic job(s) after startup", len(rows))


async def enqueue(
    *,
    user_id: Optional[int],
    program_ids: list[int],
    skip_existing: bool = True,
    months: int = 3,
    concurrency: int = 2,
    kind: str = "traffic",
    start_date: str = "",
    end_date: str = "",
) -> int:
    _cap = 16 if kind == "whois" else (5 if kind == "advertisers" else 4)
    async with SessionLocal() as session:
        job = TrafficScanJob(
            user_id=user_id,
            program_ids_json=json.dumps(program_ids),
            kind=kind,
            skip_existing=skip_existing,
            months=max(1, min(12, int(months or 3))),
            concurrency=max(1, min(_cap, int(concurrency or 2))),
            start_date=(start_date or "").strip() or None,
            end_date=(end_date or "").strip() or None,
            status="pending",
            total=len(program_ids),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id
    await _queue.put(job_id)
    return job_id


def start() -> None:
    global _worker_task, _recovery_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop())
    if _recovery_task is None or _recovery_task.done():
        _recovery_task = asyncio.create_task(_recover_unfinished_jobs())


async def stop() -> None:
    global _worker_task, _recovery_task
    if _recovery_task and not _recovery_task.done():
        _recovery_task.cancel()
        try:
            await _recovery_task
        except asyncio.CancelledError:
            pass
        _recovery_task = None
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
