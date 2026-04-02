import asyncio
import time

from app.services.tasks.service import MediaTaskService


class FakeStorage:
    def __init__(self):
        self.records = {}

    async def upsert_media_task(self, data):
        self.records[data["task_id"]] = dict(data)

    async def list_media_tasks(self, *, statuses=None, since=None, limit=None):
        items = list(self.records.values())
        if since is not None:
            items = [item for item in items if int(item.get("created_at") or 0) >= int(since)]
        if statuses:
            allowed = set(statuses)
            items = [item for item in items if item.get("status") in allowed]
        items.sort(key=lambda item: int(item.get("created_at") or 0), reverse=True)
        if limit is not None and limit > 0:
            items = items[:limit]
        return [dict(item) for item in items]


def test_media_task_create_and_success_flow():
    service = MediaTaskService()
    service.storage = FakeStorage()

    task = asyncio.run(
        service.create_task(
            task_type="image",
            source="images_api",
            model="grok-imagine-1.0",
            endpoint="/v1/images/generations",
        )
    )

    assert task["status"] == "running"
    assert task["task_type"] == "image"

    updated = asyncio.run(service.mark_success(task))

    assert updated["status"] == "success"
    assert updated["completed_at"] is not None
    assert service.storage.records[task["task_id"]]["status"] == "success"


def test_media_task_failure_records_error_message():
    service = MediaTaskService()
    service.storage = FakeStorage()

    task = asyncio.run(
        service.create_task(
            task_type="video",
            source="videos_api",
            model="grok-imagine-1.0-video",
            endpoint="/v1/videos",
        )
    )

    updated = asyncio.run(service.mark_failure(task, RuntimeError("upstream timeout")))

    assert updated["status"] == "failure"
    assert updated["error_message"] == "upstream timeout"
    assert updated["completed_at"] is not None


def test_media_task_dashboard_groups_last_seven_days():
    service = MediaTaskService()
    service.storage = FakeStorage()
    now_ms = int(time.time() * 1000)
    day_ms = 24 * 60 * 60 * 1000

    asyncio.run(
        service.storage.upsert_media_task(
            {
                "task_id": "running-image",
                "task_type": "image",
                "source": "chat_completions",
                "status": "running",
                "model": "grok-imagine-1.0",
                "endpoint": "/v1/chat/completions",
                "created_at": now_ms,
                "updated_at": now_ms,
                "completed_at": None,
                "error_message": None,
            }
        )
    )
    asyncio.run(
        service.storage.upsert_media_task(
            {
                "task_id": "success-video",
                "task_type": "video",
                "source": "videos_api",
                "status": "success",
                "model": "grok-imagine-1.0-video",
                "endpoint": "/v1/videos",
                "created_at": now_ms - day_ms,
                "updated_at": now_ms - day_ms,
                "completed_at": now_ms - day_ms + 1000,
                "error_message": None,
            }
        )
    )
    asyncio.run(
        service.storage.upsert_media_task(
            {
                "task_id": "failure-image",
                "task_type": "image",
                "source": "function_imagine",
                "status": "failure",
                "model": "grok-imagine-1.0",
                "endpoint": "/v1/function/imagine/sse",
                "created_at": now_ms - (2 * day_ms),
                "updated_at": now_ms - (2 * day_ms),
                "completed_at": now_ms - (2 * day_ms) + 1000,
                "error_message": "blocked",
            }
        )
    )

    payload = asyncio.run(service.dashboard_payload())

    assert len(payload["daily_stats"]) == 7
    assert len(payload["active_tasks"]) == 1
    assert payload["active_tasks"][0]["task_id"] == "running-image"
    assert payload["summary_today"]["image"]["running"] == 1
    totals = {item["date"]: item for item in payload["daily_stats"]}
    assert sum(item["total"] for item in totals.values()) == 3
