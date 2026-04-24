"""Media task tracking for generated image/video outputs."""

import asyncio
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, AsyncIterable

import orjson

from app.platform.paths import data_path

_TASK_FILE = data_path("media_tasks.json")
_TASK_LOCK = asyncio.Lock()
_MARKDOWN_VIDEO_URL_RE = re.compile(r"\[video\]\(([^)\s]+)\)")
_HTML_VIDEO_URL_RE = re.compile(r"""<video[^>]+src=["']([^"']+)["']""")
_HTML_SOURCE_URL_RE = re.compile(r"""<source[^>]+src=["']([^"']+)["']""")
_GENERIC_HTTP_URL_RE = re.compile(r"""https?://[^\s"'<>]+""")
_TRAILING_ESCAPE_SUFFIX_RE = re.compile(r"""(?:\\[nrt]|/[nrt])+$""")


def _normalize_media_url(value: Any) -> str:
    text = str(value or "").strip().strip("\"'")
    if not text:
        return ""
    text = text.replace("\\/", "/")
    text = _TRAILING_ESCAPE_SUFFIX_RE.sub("", text)
    text = text.rstrip("\\")
    text = text.rstrip(".,)")
    return text.strip().strip("\"'")


def extract_media_result_url(payload: Any) -> str:
    if payload is None:
        return ""

    if isinstance(payload, dict):
        direct_url = payload.get("url") or payload.get("video_url")
        if isinstance(direct_url, str):
            normalized = _normalize_media_url(direct_url)
            if normalized:
                return normalized
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                url = extract_media_result_url(item)
                if url:
                    return url
        choices = payload.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                for key in ("message", "delta"):
                    value = choice.get(key)
                    if isinstance(value, dict):
                        url = extract_media_result_url(value.get("content"))
                        if url:
                            return url
        return ""

    if isinstance(payload, (list, tuple)):
        for item in payload:
            url = extract_media_result_url(item)
            if url:
                return url
        return ""

    text = str(payload).strip()
    if not text:
        return ""

    for pattern in (_MARKDOWN_VIDEO_URL_RE, _HTML_VIDEO_URL_RE, _HTML_SOURCE_URL_RE):
        match = pattern.search(text)
        if match:
            return _normalize_media_url(match.group(1))

    match = _GENERIC_HTTP_URL_RE.search(text)
    return _normalize_media_url(match.group(0)) if match else ""


def _normalize_record(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    task_id = str(record.get("task_id") or "").strip()
    if not task_id:
        return None
    status = str(record.get("status") or "running").strip().lower()
    if status not in {"running", "success", "failure"}:
        status = "running"
    return {
        "task_id": task_id,
        "task_type": "video" if record.get("task_type") == "video" else "image",
        "source": str(record.get("source") or ""),
        "status": status,
        "model": str(record.get("model") or ""),
        "endpoint": str(record.get("endpoint") or ""),
        "created_at": int(record.get("created_at") or 0),
        "updated_at": int(record.get("updated_at") or 0),
        "completed_at": record.get("completed_at"),
        "error_message": record.get("error_message"),
        "result_url": _normalize_media_url(record.get("result_url")),
    }


async def _load_task_map() -> dict[str, dict[str, Any]]:
    if not _TASK_FILE.exists():
        return {}
    try:
        raw = await asyncio.to_thread(_TASK_FILE.read_bytes)
        payload = orjson.loads(raw or b"{}")
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for value in payload.values():
        record = _normalize_record(value)
        if record:
            result[record["task_id"]] = record
    return result


async def _save_task_map(tasks: dict[str, dict[str, Any]]) -> None:
    _TASK_FILE.parent.mkdir(parents=True, exist_ok=True)
    raw = orjson.dumps(tasks, option=orjson.OPT_INDENT_2)
    await asyncio.to_thread(_TASK_FILE.write_bytes, raw)


def _stringify_error(error: Any) -> str:
    if error is None:
        return ""
    if isinstance(error, str):
        return error[:500]
    message = getattr(error, "message", None) or str(error)
    return (message or "unknown_error")[:500]


class MediaTaskService:
    async def create_task(
        self,
        *,
        task_type: str,
        source: str,
        model: str,
        endpoint: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        now = int(time.time() * 1000)
        record = {
            "task_id": task_id or uuid.uuid4().hex,
            "task_type": "video" if task_type == "video" else "image",
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
        await self.upsert(record)
        return record

    async def upsert(self, record: dict[str, Any]) -> None:
        normalized = _normalize_record(record)
        if not normalized:
            return
        async with _TASK_LOCK:
            tasks = await _load_task_map()
            tasks[normalized["task_id"]] = normalized
            await _save_task_map(tasks)

    async def mark_success(self, task: str | dict[str, Any], *, result_url: str | None = None) -> None:
        record = await self._coerce_task(task)
        now = int(time.time() * 1000)
        record.update(
            {
                "status": "success",
                "updated_at": now,
                "completed_at": now,
                "error_message": None,
                "result_url": _normalize_media_url(result_url),
            }
        )
        await self.upsert(record)

    async def mark_failure(self, task: str | dict[str, Any], error: Any) -> None:
        record = await self._coerce_task(task)
        now = int(time.time() * 1000)
        record.update(
            {
                "status": "failure",
                "updated_at": now,
                "completed_at": now,
                "error_message": _stringify_error(error),
                "result_url": None,
            }
        )
        await self.upsert(record)

    async def wrap_stream(
        self,
        task: str | dict[str, Any],
        stream: AsyncIterable[str],
        *,
        capture_result_url: bool = False,
    ) -> AsyncGenerator[str, None]:
        record = await self._coerce_task(task)
        result_url = str(record.get("result_url") or "").strip() or None
        buffer = ""
        try:
            async for chunk in stream:
                if capture_result_url:
                    buffer = f"{buffer}{chunk}"[-32768:]
                    extracted = extract_media_result_url(buffer)
                    if extracted:
                        result_url = extracted
                yield chunk
            await self.mark_success(record, result_url=result_url)
        except asyncio.CancelledError:
            await self.mark_failure(record, "client_disconnected")
            raise
        except Exception as exc:
            await self.mark_failure(record, exc)
            raise

    async def dashboard_payload(self) -> dict[str, Any]:
        tasks = await self.list_tasks(limit=2000)
        now = datetime.now()
        midnight = datetime(now.year, now.month, now.day)
        start_day = midnight - timedelta(days=6)
        start_ms = int(start_day.timestamp() * 1000)
        daily_lookup = {
            (start_day + timedelta(days=offset)).strftime("%Y-%m-%d"): self._empty_day(
                (start_day + timedelta(days=offset)).strftime("%Y-%m-%d")
            )
            for offset in range(7)
        }

        for task in tasks:
            created_at = int(task.get("created_at") or 0)
            if created_at < start_ms:
                continue
            date_key = datetime.fromtimestamp(created_at / 1000).strftime("%Y-%m-%d")
            bucket = daily_lookup.get(date_key)
            if not bucket:
                continue
            media_type = "video" if task.get("task_type") == "video" else "image"
            status = str(task.get("status") or "")
            bucket["total"] += 1
            if status in {"running", "success", "failure"}:
                bucket[media_type][status] += 1

        now_ms = int(time.time() * 1000)
        task_list = [self._with_duration(task, now_ms) for task in tasks[:200]]
        return {
            "server_now": now_ms,
            "active_tasks": [task for task in task_list if task.get("status") == "running"],
            "task_list": task_list,
            "daily_stats": [daily_lookup[key] for key in sorted(daily_lookup)],
            "summary_total": self._summarize(tasks),
        }

    async def list_tasks(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        async with _TASK_LOCK:
            tasks = await _load_task_map()
        values = sorted(tasks.values(), key=lambda item: int(item.get("created_at") or 0), reverse=True)
        return values[:limit] if limit else values

    async def _coerce_task(self, task: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(task, dict):
            return dict(task)
        async with _TASK_LOCK:
            tasks = await _load_task_map()
        record = tasks.get(str(task))
        if record:
            return dict(record)
        return await self.create_task(
            task_id=str(task),
            task_type="video" if str(task).startswith("video_") else "image",
            source="unknown",
            model="",
            endpoint="",
        )

    def _with_duration(self, task: dict[str, Any], now_ms: int) -> dict[str, Any]:
        item = dict(task)
        end_ms = int(item.get("completed_at") or now_ms)
        item["duration_ms"] = max(0, end_ms - int(item.get("created_at") or end_ms))
        return item

    def _empty_day(self, date_key: str) -> dict[str, Any]:
        return {
            "date": date_key,
            "total": 0,
            "image": {"running": 0, "success": 0, "failure": 0},
            "video": {"running": 0, "success": 0, "failure": 0},
        }

    def _summarize(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        summary = {
            "total": 0,
            "image": {"running": 0, "success": 0, "failure": 0},
            "video": {"running": 0, "success": 0, "failure": 0},
        }
        for task in tasks:
            summary["total"] += 1
            media_type = "video" if task.get("task_type") == "video" else "image"
            status = str(task.get("status") or "")
            if status in {"running", "success", "failure"}:
                summary[media_type][status] += 1
        return summary


_service: MediaTaskService | None = None


def get_media_task_service() -> MediaTaskService:
    global _service
    if _service is None:
        _service = MediaTaskService()
    return _service


__all__ = ["MediaTaskService", "extract_media_result_url", "get_media_task_service"]
