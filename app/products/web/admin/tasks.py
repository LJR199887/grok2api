"""Admin media task dashboard API."""

from fastapi import APIRouter

from app.products.tasks import get_media_task_service

router = APIRouter(prefix="/tasks", tags=["Admin - Tasks"])


@router.get("")
async def get_tasks_dashboard():
    return await get_media_task_service().dashboard_payload()


__all__ = ["router"]
