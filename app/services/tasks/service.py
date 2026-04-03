import asyncio
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, AsyncIterable, Awaitable, Callable, Dict, Optional

from app.core.storage import get_storage


TaskDisconnectChecker = Callable[[], Awaitable[bool]]

_MARKDOWN_VIDEO_URL_RE = re.compile(r"\[video\]\(([^)\s]+)\)")
_HTML_SOURCE_URL_RE = re.compile(r"""<source[^>]+src=["']([^"']+)["']""")
_GENERIC_HTTP_URL_RE = re.compile(r"""https?://[^\s"'<>]+""")


def extract_media_result_url(payload: Any) -> str:
    if payload is None:
        return ""

    if isinstance(payload, dict):
        direct_url = payload.get("url")
        if isinstance(direct_url, str) and direct_url.strip():
            return direct_url.strip()

        choices = payload.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if isinstance(message, dict):
                    content = extract_media_result_url(message.get("content"))
                    if content:
                        return content
                delta = choice.get("delta")
                if isinstance(delta, dict):
                    content = extract_media_result_url(delta.get("content"))
                    if content:
                        return content

    if isinstance(payload, (list, tuple)):
        for item in payload:
            url = extract_media_result_url(item)
            if url:
                return url
        return ""

    text = str(payload).strip()
    if not text:
        return ""

    md_match = _MARKDOWN_VIDEO_URL_RE.search(text)
    if md_match:
        return md_match.group(1).strip()

    html_match = _HTML_SOURCE_URL_RE.search(text)
    if html_match:
        return html_match.group(1).strip()

    url_match = _GENERIC_HTTP_URL_RE.search(text)
    if url_match:
        return url_match.group(0).strip().rstrip(".,)")

    return ""


class MediaTaskService:
    def __init__(self):
        self.storage = get_storage()

    async def create_task(
        self,
        *,
        task_type: str,
        source: str,
        model: str,
        endpoint: str,
    ) -> Dict[str, Any]:
        now = int(time.time() * 1000)
        record = {
            "task_id": uuid.uuid4().hex,
            "task_type": task_type,
            "source": source,
            "status": "running",
            "model": model,
            "endpoint": endpoint,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
            "error_message": None,
            "result_url": None,
        }
        await self.storage.upsert_media_task(record)
        return record

    async def mark_success(
        self,
        task: str | Dict[str, Any],
        *,
        result_url: Optional[str] = None,
    ):
        record = await self._get_updated_record(
            task,
            status="success",
            error_message=None,
            result_url=result_url,
        )
        record["completed_at"] = record["updated_at"]
        await self.storage.upsert_media_task(record)
        return record

    async def mark_failure(self, task: str | Dict[str, Any], error: Any):
        record = await self._get_updated_record(
            task,
            status="failure",
            error_message=self._stringify_error(error),
            result_url=None,
        )
        record["completed_at"] = record["updated_at"]
        await self.storage.upsert_media_task(record)
        return record

    async def wrap_stream(
        self,
        task: str | Dict[str, Any],
        stream: AsyncIterable[str],
        *,
        disconnect_checker: Optional[TaskDisconnectChecker] = None,
        cancel_message: str = "cancelled",
        capture_result_url: bool = False,
    ) -> AsyncGenerator[str, None]:
        record = await self._coerce_task(task)
        finished = False
        result_url = str(record.get("result_url") or "").strip() or None
        chunk_buffer = ""
        try:
            async for chunk in stream:
                if capture_result_url:
                    chunk_buffer = f"{chunk_buffer}{chunk}"[-32768:]
                    extracted = extract_media_result_url(chunk_buffer)
                    if extracted:
                        result_url = extracted
                if disconnect_checker and await disconnect_checker():
                    await self.mark_failure(record, "client_disconnected")
                    finished = True
                    break
                yield chunk
            if not finished:
                await self.mark_success(record, result_url=result_url)
        except asyncio.CancelledError:
            await self.mark_failure(record, cancel_message)
            raise
        except Exception as exc:
            await self.mark_failure(record, exc)
            raise

    async def dashboard_payload(self) -> Dict[str, Any]:
        now = datetime.now()
        midnight = datetime(now.year, now.month, now.day)
        start_day = midnight - timedelta(days=6)
        start_ms = int(start_day.timestamp() * 1000)
        all_tasks = await self.storage.list_media_tasks(limit=2000)
        task_list = list(all_tasks[:200])

        recent_tasks = await self.storage.list_media_tasks(since=start_ms)

        daily_lookup: Dict[str, Dict[str, Any]] = {}
        for day_offset in range(7):
            day = start_day + timedelta(days=day_offset)
            date_key = day.strftime("%Y-%m-%d")
            daily_lookup[date_key] = self._empty_day(date_key)

        for task in recent_tasks:
            date_key = datetime.fromtimestamp(
                int(task.get("created_at") or 0) / 1000
            ).strftime("%Y-%m-%d")
            bucket = daily_lookup.get(date_key)
            if not bucket:
                continue
            bucket["total"] += 1
            task_type = "video" if task.get("task_type") == "video" else "image"
            status = str(task.get("status") or "")
            if status not in ("running", "success", "failure"):
                continue
            bucket[task_type][status] += 1

        summary_total = self._summarize(all_tasks)
        active_payload = []
        now_ms = int(time.time() * 1000)
        for task in task_list:
            item = dict(task)
            end_ms = int(item.get("completed_at") or now_ms)
            item["duration_ms"] = max(
                0,
                end_ms - int(item.get("created_at") or end_ms),
            )
            if item.get("status") == "running":
                active_payload.append(dict(item))

        return {
            "server_now": now_ms,
            "active_tasks": active_payload,
            "task_list": [
                {
                    **dict(task),
                    "duration_ms": max(
                        0,
                        int(task.get("completed_at") or now_ms)
                        - int(task.get("created_at") or int(task.get("completed_at") or now_ms)),
                    ),
                }
                for task in task_list
            ],
            "daily_stats": [daily_lookup[key] for key in sorted(daily_lookup.keys())],
            "summary_total": summary_total,
        }

    async def _get_updated_record(
        self,
        task: str | Dict[str, Any],
        *,
        status: str,
        error_message: Optional[str],
        result_url: Optional[str],
    ) -> Dict[str, Any]:
        record = await self._coerce_task(task)
        updated = dict(record)
        updated["status"] = status
        updated["updated_at"] = int(time.time() * 1000)
        updated["error_message"] = error_message
        updated["result_url"] = (result_url or "").strip() or None
        return updated

    async def _coerce_task(self, task: str | Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(task, dict):
            return dict(task)
        task_id = str(task)
        records = await self.storage.list_media_tasks(limit=10000)
        for record in records:
            if record.get("task_id") == task_id:
                return dict(record)
        raise KeyError(f"Unknown media task: {task_id}")

    def _empty_day(self, date_key: str) -> Dict[str, Any]:
        return {
            "date": date_key,
            "total": 0,
            "image": {"running": 0, "success": 0, "failure": 0},
            "video": {"running": 0, "success": 0, "failure": 0},
        }

    def _summarize(self, tasks: list[Dict[str, Any]]) -> Dict[str, Any]:
        summary = {
            "total": 0,
            "image": {"running": 0, "success": 0, "failure": 0},
            "video": {"running": 0, "success": 0, "failure": 0},
        }
        for task in tasks:
            summary["total"] += 1
            task_type = "video" if task.get("task_type") == "video" else "image"
            status = str(task.get("status") or "")
            if status in ("running", "success", "failure"):
                summary[task_type][status] += 1
        return summary

    def _stringify_error(self, error: Any) -> str:
        if error is None:
            return ""
        if isinstance(error, str):
            return error[:500]
        message = getattr(error, "message", None) or str(error)
        return (message or "unknown_error")[:500]


_service: Optional[MediaTaskService] = None


def get_media_task_service() -> MediaTaskService:
    global _service
    if _service is None:
        _service = MediaTaskService()
    return _service
