"""Signup job runner — asyncio.Queue pool worker chạy song song."""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime
from typing import List, Optional

import httpx
from sqlalchemy import and_, or_, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logger import get_logger
from app.models.affiliate_program import AffiliateProgram
from app.models.signup_job import SignupJob
from app.services.signup.agent_runner import run_signup_attempt
from app.services.storage import instruction_store, profile_store, email_store, proxy_store, sms_profile_store

log = get_logger("signup_runner")

_queue: "asyncio.Queue[int]" = asyncio.Queue()
_worker_tasks: List[asyncio.Task] = []
_recovery_task: Optional[asyncio.Task] = None


async def _load_job(session, job_id: int) -> SignupJob | None:
    res = await session.execute(select(SignupJob).where(SignupJob.id == job_id))
    return res.scalar_one_or_none()


def _program_dict(p: AffiliateProgram) -> dict:
    return {
        "id": p.id,
        "source": p.source,
        "name": p.name,
        "url": p.url,
        "signup_url": p.signup_url,
        "category": p.category,
        "description": p.description,
        "sms_country_id": p.sms_country_id or "",
        "sms_service_id": p.sms_service_id or "",
        "sms_profile_id": p.sms_profile_id or "",
    }


def _resolve_sms_profile(prog: dict, user_id: int, run_profile_id: str = "") -> dict:
    """Resolve sms_profile_id → country/service.

    Priority: run-level override > program-level profile_id > program raw country/service.
    """
    pid = (run_profile_id or prog.get("sms_profile_id") or "").strip()
    if pid and user_id:
        prof = sms_profile_store.get_profile(user_id, pid)
        if prof:
            # Override nếu profile có set, giữ nguyên nếu rỗng (để fallback env)
            if prof.get("country_id"):
                prog["sms_country_id"] = prof["country_id"]
            if prof.get("service_id"):
                prog["sms_service_id"] = prof["service_id"]
            prog["sms_profile_id"] = pid
            prog["sms_profile_name"] = prof.get("name", "")
    return prog


async def _update_job(session, job_id: int, **fields) -> None:
    job = await _load_job(session, job_id)
    if not job:
        return
    for k, v in fields.items():
        setattr(job, k, v)
    await session.commit()


async def _proxy_is_usable(proxy: dict) -> tuple[bool, str]:
    url = (proxy.get("url") or "").strip()
    if not url:
        return False, "missing proxy url"
    try:
        async with httpx.AsyncClient(
            proxy=url,
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
        ) as client:
            resp = await client.get("https://api.ipify.org?format=json")
            resp.raise_for_status()
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


async def _run_job(job_id: int) -> None:
    # Đọc job + load programs + profiles + instruction
    async with SessionLocal() as session:
        job = await _load_job(session, job_id)
        if not job:
            return
        program_ids: list[int] = json.loads(job.program_ids_json or "[]")
        profile_ids: list[str] = json.loads(job.profile_ids_json or "[]")
        email_ids: list[str] = json.loads(job.email_ids_json or "[]")
        proxy_ids: list[str] = json.loads(job.proxy_ids_json or "[]")
        instruction_names: list[str] = json.loads(job.instruction_names_json or "[]")
        instruction_name = job.instruction_name or ""
        if not instruction_names and instruction_name:
            instruction_names = [instruction_name]
        extra_prompt = job.extra_prompt or ""
        headless = bool(job.headless)
        owner_user_id = int(job.user_id) if job.user_id else 0
        run_sms_profile_id = job.sms_profile_id or ""
        run_gemini_key_index = int(job.gemini_key_index or 0)
        run_llm_provider = job.llm_provider or ""
        run_llm_key_index = int(job.llm_key_index or 0)
        run_mode = job.run_mode or "llm"
        run_playbook_id = job.playbook_id
        tier3_behavior = job.tier3_behavior or "ask"
        try:
            script_overrides: dict = json.loads(job.script_overrides_json or "{}")
        except Exception:
            script_overrides = {}
        # Legacy fallback: if old gemini_key_index set but new fields not, migrate meaning
        if run_gemini_key_index and not run_llm_provider:
            run_llm_provider = "gemini"
            run_llm_key_index = run_gemini_key_index
        try:
            existing_results: list[dict] = json.loads(job.results_json or "[]")
        except Exception:
            existing_results = []
        completed_program_ids = {
            int(r.get("program_id"))
            for r in existing_results
            if isinstance(r, dict) and r.get("program_id") is not None
        }

        def _counts(rows: list[dict]) -> tuple[int, int]:
            ok = 0
            bad = 0
            for row in rows:
                status = (row.get("status") or "").lower()
                if status in ("success", "pending_verify"):
                    ok += 1
                else:
                    bad += 1
            return ok, bad

        initial_succeeded, initial_failed = _counts(existing_results)

        rows = (
            await session.execute(
                select(AffiliateProgram).where(AffiliateProgram.id.in_(program_ids))
            )
        ).scalars().all()
        programs = {p.id: _program_dict(p) for p in rows}

        await _update_job(
            session,
            job_id,
            status="running",
            started_at=job.started_at or datetime.utcnow(),
            total=len(program_ids),
            succeeded=initial_succeeded,
            failed=initial_failed,
            results_json=json.dumps(existing_results, ensure_ascii=False),
            error=None,
            finished_at=None,
        )

    # Load profile data (file-based, sync) + instruction content
    profiles: list[dict] = []
    for pid in profile_ids:
        try:
            p = profile_store.get_profile(owner_user_id, pid) if owner_user_id else None
            if p:
                profiles.append(p)
        except Exception as e:
            log.warning(f"profile {pid} load failed: {e}")
    if not profiles:
        async with SessionLocal() as session:
            await _update_job(
                session,
                job_id,
                status="failed",
                error="Không load được profile nào",
                finished_at=datetime.utcnow(),
            )
        return

    instruction_content = ""
    if instruction_names and owner_user_id:
        # Ghép nhiều instruction theo thứ tự, ngăn cách header
        chunks: list[str] = []
        for nm in instruction_names:
            try:
                doc = instruction_store.get_instruction(owner_user_id, nm)
                if doc and doc.get("content"):
                    chunks.append(f"# === {nm} ===\n{doc['content']}")
            except Exception as e:
                log.warning(f"instruction {nm} load failed: {e}")
        instruction_content = "\n\n".join(chunks)
    elif instruction_name and owner_user_id:
        try:
            doc = instruction_store.get_instruction(owner_user_id, instruction_name)
            if doc:
                instruction_content = doc.get("content", "")
        except Exception as e:
            log.warning(f"instruction {instruction_name} load failed: {e}")

    # Load emails — tuyệt đối chỉ lấy đúng những gì user chọn, KHÔNG fallback sang email khác
    emails: list[dict] = []
    for eid in email_ids:
        try:
            e = email_store.get_email(owner_user_id, eid) if owner_user_id else None
            if e:
                emails.append(e)
        except Exception as ex:
            log.warning(f"email {eid} load failed: {ex}")

    # Load proxies: only use explicitly selected proxies that pass a quick connectivity check.
    # Dead proxies make Chrome fail with ERR_TUNNEL_CONNECTION_FAILED, so skip them and run direct.
    proxies: list[dict] = []
    for pid in proxy_ids:
        try:
            p = proxy_store.get_proxy(owner_user_id, pid) if owner_user_id else None
            if p:
                ok, err = await _proxy_is_usable(p)
                if ok:
                    proxies.append(p)
                else:
                    log.warning("proxy %s unusable; signup will skip it: %s", pid, err)
        except Exception as ex:
            log.warning(f"proxy {pid} load failed: {ex}")

    def _enrich(prof: dict, idx: int) -> dict:
        """Bơm email IMAP + proxy_url vào bản sao profile theo round-robin."""
        out = dict(prof)
        if emails:
            em = emails[idx % len(emails)]
            out["email"] = em.get("address", out.get("email", ""))
            if em.get("app_password"):
                out["imap"] = {"user": em.get("address", ""), "password": em["app_password"]}
            # Đặt cũng vào emails list để form dùng (backward-compat)
            out["_picked_email"] = em
        if proxies:
            px = proxies[idx % len(proxies)]
            out["proxy_url"] = px.get("url", "")
            out["_picked_proxy"] = px
        return out

    # Loop programs — mỗi program được gán đúng 1 profile/email do user chỉ định.
    # Nếu user chọn nhiều profile/email → phân theo round-robin theo thứ tự program,
    # KHÔNG dùng profile khác làm fallback khi fail (tuân thủ lựa chọn của user).
    # Proxy và SMS pool được phép tự chọn linh hoạt (xem _enrich + _resolve_sms_profile).
    results: list[dict] = list(existing_results)
    succeeded = initial_succeeded
    failed = initial_failed
    for prog_idx, pid in enumerate(program_ids):
        if pid in completed_program_ids:
            continue
        prog = programs.get(pid)
        if prog:
            prog = _resolve_sms_profile(prog, owner_user_id, run_sms_profile_id)
        if not prog or not (prog.get("signup_url") or prog.get("url")):
            results.append({
                "program_id": pid,
                "profile_id": None,
                "status": "failed",
                "message": "Program không có signup_url",
                "started_at": datetime.utcnow().isoformat() + "Z",
                "finished_at": datetime.utcnow().isoformat() + "Z",
            })
            failed += 1
            await _persist_progress(job_id, results, succeeded, failed)
            continue

        # Gán đúng 1 profile theo thứ tự program (round-robin nếu profiles < programs)
        prof = profiles[prog_idx % len(profiles)]
        enriched = _enrich(prof, prog_idx)  # email/proxy round-robin theo prog_idx
        attempt_started = datetime.utcnow()
        log.info(
            f"[signup job#{job_id}] program={pid} profile={prof.get('id')} "
            f"email={enriched.get('email','-')} proxy={'yes' if enriched.get('proxy_url') else 'no'} → start"
        )
        try:
            if run_mode in ("script", "script_llm"):
                # Per-program script override (user chọn thủ công trên UI)
                per_prog_playbook_id = script_overrides.get(str(pid)) or script_overrides.get(pid)
                effective_playbook_id = per_prog_playbook_id or run_playbook_id

                # Tier 2: lấy Gemini key cho per-step LLM recovery (chỉ script_llm)
                _script_llm_key = ""
                _script_llm_model = ""
                if run_mode == "script_llm":
                    try:
                        from app.services.llm_keyring import get_gemini_key, gemini_key_count
                        from app.core.config import settings as _s
                        if gemini_key_count():
                            _script_llm_key = get_gemini_key()[0]
                            _script_llm_model = _s.signup_llm_model or "gemini-2.0-flash"
                    except Exception as _ke:
                        log.warning("[job=%s] script_llm: không lấy được Gemini key: %s", job_id, _ke)

                r = await _run_script_attempt(
                    job_id=job_id,
                    program=prog,
                    profile=enriched,
                    headless=headless,
                    user_id=owner_user_id,
                    explicit_playbook_id=effective_playbook_id,
                    llm_api_key=_script_llm_key,
                    llm_model=_script_llm_model,
                )

                # Tier 3 gate: kiểm tra behavior trước khi chạy full LLM
                if run_mode == "script_llm" and (r.get("status") or "") not in ("success", "pending_verify"):
                    log.info(
                        "[job=%s] script_llm: script thất bại (%s), kiểm tra tier3_behavior=%s",
                        job_id, r.get("message", ""), tier3_behavior,
                    )
                    # IP_BLOCKED: T3 cùng IP cũng bị block → skip, không tốn API
                    if "IP_BLOCKED" in (r.get("message") or ""):
                        r["status"] = "failed"
                        r["message"] = "IP bị chặn bởi trang đích. Hãy đổi proxy hoặc chờ."
                    elif tier3_behavior == "never":
                        r = {
                            "status": "failed",
                            "message": "Không có script phù hợp. Tier 3 bị tắt (never).",
                            "steps": r.get("steps", 0),
                            "duration_sec": r.get("duration_sec", 0),
                        }
                    elif tier3_behavior == "ask":
                        # Ghi lại là đang chờ duyệt — job tiếp tục xử lý các program khác
                        r = {
                            "status": "tier3_waiting",
                            "message": "Cần duyệt Tier 3 (~$0.15) để tiếp tục đăng ký chương trình này.",
                            "steps": r.get("steps", 0),
                            "duration_sec": r.get("duration_sec", 0),
                        }
                        # Đặt flag job-level waiting_approval (chỉ set lần đầu)
                        async with SessionLocal() as _ws:
                            _wjob = await _load_job(_ws, job_id)
                            if _wjob and _wjob.tier3_status != "waiting_approval":
                                _wjob.tier3_status = "waiting_approval"
                                await _ws.commit()
                    else:  # tier3_behavior == "auto"
                        # Inject Q&A hints vào context — giảm LLM steps cho open-ended questions
                        _t3_extra = extra_prompt
                        try:
                            from app.services.qa_match import get_qa_hints_for_platform
                            _qa_hints = await get_qa_hints_for_platform(
                                platform=(prog.get("source") or "").lower(),
                                category=prog.get("category") or "",
                                profile=enriched,
                                program=prog,
                            )
                            if _qa_hints:
                                _t3_extra = (extra_prompt + "\n\n" + _qa_hints).strip() if extra_prompt else _qa_hints
                        except Exception as _qe:
                            log.warning("[job=%s] Q&A hints error: %s", job_id, _qe)
                        r = await run_signup_attempt(
                            job_id=job_id,
                            program=prog,
                            profile=enriched,
                            instruction_content=instruction_content,
                            instruction_filename=instruction_names[0] if instruction_names else instruction_name,
                            extra_prompt=_t3_extra,
                            headless=headless,
                            user_id=owner_user_id,
                            llm_provider=run_llm_provider,
                            llm_key_index=run_llm_key_index,
                        )
            else:
                r = await run_signup_attempt(
                    job_id=job_id,
                    program=prog,
                    profile=enriched,
                    instruction_content=instruction_content,
                    instruction_filename=instruction_names[0] if instruction_names else instruction_name,
                    extra_prompt=extra_prompt,
                    headless=headless,
                    user_id=owner_user_id,
                    gemini_key_index=run_gemini_key_index,
                    llm_provider=run_llm_provider,
                    llm_key_index=run_llm_key_index,
                )
        except Exception as e:
            log.exception("attempt crash")
            r = {
                "status": "error",
                "message": f"{type(e).__name__}: {e}"[:500],
                "steps": 0,
                "duration_sec": 0,
            }
        entry = {
            "program_id": pid,
            "profile_id": prof.get("id"),
            "status": r.get("status"),
            "message": r.get("message"),
            "steps": r.get("steps"),
            "final_url": r.get("final_url"),
            "screenshot": r.get("screenshot"),
            "duration_sec": r.get("duration_sec"),
            "started_at": attempt_started.isoformat() + "Z",
            "finished_at": datetime.utcnow().isoformat() + "Z",
            # Extra audit fields
            "email_used": enriched.get("email") or (enriched.get("_picked_email") or {}).get("address") or "",
            "proxy_label": (enriched.get("_picked_proxy") or {}).get("label") or "",
            "proxy_host": (enriched.get("_picked_proxy") or {}).get("host") or "",
            "proxy_url_used": enriched.get("proxy_url") or "",
            "instruction_names_used": instruction_names,
            "profile_snapshot": {
                "full_name": enriched.get("full_name") or "",
                "ho": enriched.get("ho") or "",
                "ten": enriched.get("ten") or "",
                "email": enriched.get("email") or "",
                "phone": enriched.get("phone") or "",
                "website": enriched.get("website") or "",
                "country": enriched.get("country") or "",
                "niche": enriched.get("niche") or [],
                "company": enriched.get("company") or "",
                "password": enriched.get("password") or "",
                "notes": enriched.get("notes") or "",
            },
        }
        results.append(entry)
        status = (r.get("status") or "").lower()
        if status in ("success", "pending_verify"):
            succeeded += 1
        else:
            failed += 1
        await _persist_progress(job_id, results, succeeded, failed)


    # Final status
    final_status = "success" if failed == 0 else ("partial" if succeeded > 0 else "failed")
    async with SessionLocal() as session:
        await _update_job(
            session,
            job_id,
            status=final_status,
            succeeded=succeeded,
            failed=failed,
            results_json=json.dumps(results, ensure_ascii=False),
            finished_at=datetime.utcnow(),
        )
    log.info(f"[signup job#{job_id}] DONE — ok={succeeded} fail={failed}")


async def _run_script_attempt(
    *,
    job_id: int,
    program: dict,
    profile: dict,
    headless: bool,
    user_id: int,
    explicit_playbook_id: Optional[int] = None,
    llm_api_key: str = "",
    llm_model: str = "gemini-2.0-flash",
) -> dict:
    """
    Chạy signup qua playbook/script.
    Khi llm_api_key được cung cấp: mỗi step thất bại được thử recover bằng Gemini Flash (Tier 2).
    Returns same shape as run_signup_attempt.
    """
    from app.services.playbook_runner import run_with_playbook, find_best_playbook, update_playbook_stats

    # Resolve playbook
    playbook_id = explicit_playbook_id
    if not playbook_id:
        pb = await find_best_playbook(user_id, program)
        if pb:
            playbook_id = pb.id

    if not playbook_id:
        return {
            "status": "failed",
            "message": "Không tìm thấy playbook phù hợp cho chương trình này",
            "steps": 0,
            "duration_sec": 0,
        }

    proxy_url = (profile.get("proxy_url") or "").strip() or None

    # First attempt (với Tier 2 LLM recovery nếu có key)
    result = await run_with_playbook(
        playbook_id=playbook_id,
        program=program,
        profile=profile,
        headless=headless,
        user_id=user_id,
        job_id=job_id,
        proxy_url=proxy_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
    )

    status = (result.get("status") or "").lower()
    if status in ("success", "pending_verify"):
        await update_playbook_stats(playbook_id, success=True)
        return result

    # IP bị block → retry cùng IP vô ích, bỏ qua ngay + không tính consecutive_fails
    msg1 = result.get("message", "")
    if "IP_BLOCKED" in msg1:
        log.warning("[job=%s] IP_BLOCKED, skipping retry and T3: %s", job_id, msg1)
        return result  # không update consecutive_fails — lỗi proxy, không phải lỗi script

    # Lỗi proxy/mạng → retry cũng sẽ chết tương tự, bỏ qua ngay
    _PROXY_ERRORS = ("ERR_TUNNEL_CONNECTION_FAILED", "ERR_PROXY_CONNECTION_FAILED",
                     "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_REFUSED", "ERR_INTERNET_DISCONNECTED")
    if any(e in msg1 for e in _PROXY_ERRORS):
        log.warning("[job=%s] proxy/network error, skipping retry: %s", job_id, msg1)
        await update_playbook_stats(playbook_id, success=False, max_consecutive_before_llm=2)
        result["message"] = f"Lỗi proxy/mạng (bỏ qua retry): {msg1}"
        return result

    # First fail — retry once (không dùng proxy lần 2 nếu lần 1 có proxy)
    log.info("[job=%s] script attempt 1 failed (%s), retrying…", job_id, msg1)
    result2 = await run_with_playbook(
        playbook_id=playbook_id,
        program=program,
        profile=profile,
        headless=headless,
        user_id=user_id,
        job_id=job_id,
        proxy_url=proxy_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
    )
    status2 = (result2.get("status") or "").lower()
    if status2 in ("success", "pending_verify"):
        await update_playbook_stats(playbook_id, success=True)
        return result2

    # Both failed → record consecutive fails
    await update_playbook_stats(playbook_id, success=False, max_consecutive_before_llm=2)
    result2["message"] = (
        f"Script thất bại 2 lần. Lần 1: {result.get('message','')}. "
        f"Lần 2: {result2.get('message','')}"
    )
    return result2


async def _persist_progress(job_id: int, results: list[dict], succeeded: int, failed: int) -> None:
    async with SessionLocal() as session:
        await _update_job(
            session,
            job_id,
            succeeded=succeeded,
            failed=failed,
            results_json=json.dumps(results, ensure_ascii=False),
        )


async def _worker_loop() -> None:
    log.info("Signup worker started")
    while True:
        try:
            job_id = await _queue.get()
        except asyncio.CancelledError:
            break
        try:
            await _run_job(job_id)
        except Exception:
            log.exception("Unhandled in signup worker")
            try:
                async with SessionLocal() as session:
                    await _update_job(
                        session,
                        job_id,
                        status="failed",
                        error=traceback.format_exc()[-800:],
                        finished_at=datetime.utcnow(),
                    )
            except Exception:
                pass
        finally:
            _queue.task_done()


async def _recover_unfinished_jobs() -> None:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(SignupJob).where(
                    or_(
                        SignupJob.status.in_(["pending", "running"]),
                        and_(
                            SignupJob.status == "failed",
                            SignupJob.error == "Killed by server restart",
                            SignupJob.finished_at.is_(None),
                            SignupJob.succeeded == 0,
                            SignupJob.failed == 0,
                        ),
                    )
                )
            )
        ).scalars().all()

        for job in rows:
            job.status = "pending"
            job.error = None
            job.finished_at = None
            if job.succeeded == 0 and job.failed == 0 and not (job.results_json or "").strip():
                job.started_at = None
        await session.commit()

    for job in rows:
        await _queue.put(job.id)
    if rows:
        log.warning("Recovered %d unfinished signup job(s) after startup", len(rows))


async def enqueue(
    *,
    user_id: int | None,
    program_ids: list[int],
    profile_ids: list[str],
    email_ids: list[str] | None = None,
    proxy_ids: list[str] | None = None,
    instruction_names: list[str] | None = None,
    instruction_name: str = "",
    extra_prompt: str = "",
    headless: bool = False,
    sms_profile_id: str = "",
    gemini_key_index: int = 0,
    llm_provider: str = "",
    llm_key_index: int = 0,
    run_mode: str = "llm",
    playbook_id: Optional[int] = None,
    batch_id: str = "",
    tier3_behavior: str = "ask",
    script_overrides: dict | None = None,
) -> int:
    async with SessionLocal() as session:
        job = SignupJob(
            user_id=user_id,
            program_ids_json=json.dumps(program_ids),
            profile_ids_json=json.dumps(profile_ids),
            email_ids_json=json.dumps(email_ids or []),
            proxy_ids_json=json.dumps(proxy_ids or []),
            instruction_names_json=json.dumps(instruction_names or []),
            instruction_name=instruction_name or None,
            extra_prompt=extra_prompt or None,
            headless=headless,
            sms_profile_id=sms_profile_id or None,
            gemini_key_index=gemini_key_index or None,
            llm_provider=llm_provider or None,
            llm_key_index=llm_key_index or None,
            run_mode=run_mode or "llm",
            playbook_id=playbook_id or None,
            batch_id=batch_id or None,
            tier3_behavior=tier3_behavior or "ask",
            script_overrides_json=json.dumps(script_overrides or {}),
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
    global _worker_tasks, _recovery_task
    n = max(1, int(getattr(settings, "signup_worker_concurrency", 1) or 1))
    # Loại bỏ task đã done
    _worker_tasks = [t for t in _worker_tasks if not t.done()]
    need = n - len(_worker_tasks)
    for i in range(need):
        _worker_tasks.append(asyncio.create_task(_worker_loop()))
    if need > 0:
        log.info(f"Signup worker pool: {len(_worker_tasks)} worker(s) running")
    if _recovery_task is None or _recovery_task.done():
        _recovery_task = asyncio.create_task(_recover_unfinished_jobs())


async def stop() -> None:
    global _worker_tasks, _recovery_task
    if _recovery_task and not _recovery_task.done():
        _recovery_task.cancel()
        try:
            await _recovery_task
        except asyncio.CancelledError:
            pass
        _recovery_task = None
    for t in _worker_tasks:
        if not t.done():
            t.cancel()
    for t in _worker_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    _worker_tasks = []
