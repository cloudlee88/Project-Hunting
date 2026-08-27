"""Experiment API — browser agent chạy tác vụ tùy ý."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.deps import get_current_user
from app.services import experiment_service

router = APIRouter(prefix="/api/experiment", tags=["experiment"])


class RunIn(BaseModel):
    task: str
    model: str = "gemini"   # "gemini" | "openai"
    max_steps: int = 30


@router.post("/run")
async def run_experiment(
    payload: RunIn,
    user=Depends(get_current_user),
):
    """Bắt đầu chạy browser agent với task bất kỳ. Trả về job_id để poll."""
    if not payload.task.strip():
        raise HTTPException(400, "task không được để trống")
    if not (1 <= payload.max_steps <= 60):
        raise HTTPException(400, "max_steps phải từ 1 đến 60")
    job_id = await experiment_service.run_experiment(
        payload.task.strip(),
        model=payload.model,
        max_steps=payload.max_steps,
    )
    return {"job_id": job_id}


@router.get("/jobs")
async def list_jobs(
    limit: int = 50,
    user=Depends(get_current_user),
):
    return experiment_service.list_jobs(limit)


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: int,
    user=Depends(get_current_user),
):
    job = experiment_service.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job không tồn tại")
    return job


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    user=Depends(get_current_user),
):
    ok = experiment_service.cancel_job(job_id)
    if not ok:
        raise HTTPException(400, "Job không thể hủy (đã kết thúc hoặc không tồn tại)")
    return {"ok": True}
