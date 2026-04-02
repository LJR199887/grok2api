from fastapi import APIRouter, Depends

from app.core.auth import verify_app_key
from app.services.tasks import get_media_task_service


router = APIRouter()


@router.get("/tasks", dependencies=[Depends(verify_app_key)])
async def get_tasks_dashboard():
    service = get_media_task_service()
    return await service.dashboard_payload()


__all__ = ["router"]
