from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from app.core.config import settings
from app.core.db import init_db, SessionLocal
from app.core.logger import get_logger
from app.models import User
from app.services import job_runner, signup_runner, traffic_runner, user_service
from app.services.storage import profile_store, instruction_store
from app.api import auth, sources, crawl, jobs, programs, profiles, instructions, shortlists, signup, system, ads_transparency, emails, proxies, sms, sms_profiles, experiment, captcha_memory, playbooks, qa_library, discovery, admin

log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting %s", settings.app_name)
    await init_db()
    async with SessionLocal() as s:
        await user_service.seed_default_admin(s)
        admin = (await s.execute(select(User).where(User.email == "admin"))).scalar_one_or_none()
    log.info("Default admin user ensured (admin/123456)")
    # Migrate legacy file (data/profiles/*.json + data/instructions/*) sang subfolder user
    if admin is not None:
        try:
            moved_p = profile_store.migrate_legacy_to_user(admin.id)
            moved_i = instruction_store.migrate_legacy_to_user(admin.id)
            if moved_p or moved_i:
                log.info("Migrated legacy files → user folder: profiles=%s instructions=%s", moved_p, moved_i)
        except Exception:
            log.exception("Legacy storage migration failed")
        try:
            from app.core.db import backfill_discovery_owner
            moved_d = await backfill_discovery_owner(admin.id)
            if moved_d:
                log.info("Gán %d dòng discovery chưa có chủ cho admin", moved_d)
        except Exception:
            log.exception("Discovery owner backfill failed")
    # Zombie recovery: crawl jobs cannot resume safely; signup/traffic runners
    # requeue their unfinished jobs on startup.
    try:
        from app.models.crawl_job import CrawlJob as _CrawlJob
        from sqlalchemy import update as _sql_update
        async with SessionLocal() as _zs:
            _crawl_r = await _zs.execute(
                _sql_update(_CrawlJob)
                .where(_CrawlJob.status == "running")
                .values(status="failed", error="Killed by server restart", finished_at=datetime.utcnow())
                .execution_options(synchronize_session=False)
            )
            await _zs.commit()
            if _crawl_r.rowcount:
                log.warning("Zombie recovery: marked %d running crawl_job(s) as failed", _crawl_r.rowcount)
            # Reset discovery sources stuck in "crawling" (crawl killed by restart)
            from app.models.discovery import DiscoverySource as _DiscSource
            _disc_r = await _zs.execute(
                _sql_update(_DiscSource)
                .where(_DiscSource.status == "crawling")
                .values(status="active")
                .execution_options(synchronize_session=False)
            )
            await _zs.commit()
            if _disc_r.rowcount:
                log.warning("Zombie recovery: reset %d stuck discovery source(s)", _disc_r.rowcount)
    except Exception:
        log.exception("Zombie recovery failed")
    # Seed Q&A library nếu bảng trống
    try:
        from app.services.qa_match import seed_qa_library_if_empty
        seeded = await seed_qa_library_if_empty()
        if seeded:
            log.info("Q&A Library: seeded %d entries", seeded)
    except Exception:
        log.exception("Q&A Library seed failed")

    job_runner.start()
    signup_runner.start()
    traffic_runner.start()
    log.info("Job runners started (crawl + signup + traffic)")
    yield
    log.info("Shutting down...")
    await job_runner.stop()
    await signup_runner.stop()
    await traffic_runner.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.app_name}


for r in (auth.router, sources.router, crawl.router, jobs.router,
          programs.router, profiles.router, instructions.router, shortlists.router, signup.router, system.router,
          ads_transparency.router, emails.router, proxies.router, sms.router, sms_profiles.router,
          experiment.router, captcha_memory.router, playbooks.router, qa_library.router,
          discovery.router, admin.router):
    app.include_router(r)
