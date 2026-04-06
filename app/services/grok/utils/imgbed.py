"""
CloudFlare ImgBed upload helpers.
"""

import mimetypes
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse

from app.core.config import get_config
from app.core.exceptions import UpstreamException, ValidationException
from app.core.logger import logger
from app.core.proxy_pool import (
    build_http_proxies,
    get_current_proxy_from,
    rotate_proxy,
    should_rotate_proxy,
)
from app.core.storage import DATA_DIR
from app.services.reverse.assets_download import AssetsDownloadReverse
from app.services.reverse.utils.retry import retry_on_status
from app.services.reverse.utils.session import ResettableSession


class ImgBedUploadService:
    """CloudFlare ImgBed upload service."""

    def __init__(self):
        self._session: Optional[ResettableSession] = None

    async def create(self) -> ResettableSession:
        if self._session is None:
            browser = get_config("proxy.browser")
            if browser:
                self._session = ResettableSession(impersonate=browser)
            else:
                self._session = ResettableSession()
        return self._session

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None

    @staticmethod
    def is_enabled() -> bool:
        return bool(get_config("imgbed.enabled", False))

    @staticmethod
    def _require_config() -> tuple[str, str, str]:
        upload_api_url = str(get_config("imgbed.upload_api_url", "") or "").strip()
        auth_code = str(get_config("imgbed.auth_code", "") or "").strip()
        upload_folder = str(get_config("imgbed.upload_folder", "") or "").strip()

        if not upload_api_url:
            raise ValidationException("ImgBed upload_api_url is required")
        parsed = urlparse(upload_api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValidationException("ImgBed upload_api_url must be a valid HTTP URL")
        if not auth_code:
            raise ValidationException("ImgBed auth_code is required")
        return upload_api_url, auth_code, upload_folder

    @staticmethod
    def _is_same_app_origin(parsed) -> bool:
        if not parsed.netloc:
            return True
        app_url = str(get_config("app.app_url", "") or "").strip()
        if not app_url:
            return False
        app_parsed = urlparse(app_url)
        return parsed.scheme == app_parsed.scheme and parsed.netloc == app_parsed.netloc

    @staticmethod
    def _guess_mime(filename: str, fallback: str = "application/octet-stream") -> str:
        mime, _ = mimetypes.guess_type(filename)
        return mime or fallback

    @staticmethod
    def _local_file_from_source(source: str) -> Optional[tuple[Path, str]]:
        parsed = urlparse(source)
        path = parsed.path if (parsed.scheme or parsed.netloc) else source
        if not path.startswith("/v1/files/"):
            return None
        if parsed.netloc and not ImgBedUploadService._is_same_app_origin(parsed):
            return None

        parts = path.strip("/").split("/", 3)
        if len(parts) < 4 or parts[0] != "v1" or parts[1] != "files":
            return None

        media_type = parts[2]
        name = parts[3].replace("/", "-")
        base_dir = DATA_DIR / "tmp"
        if media_type == "video":
            local_path = base_dir / "video" / name
            mime = "video/mp4"
        elif media_type == "image":
            local_path = base_dir / "image" / name
            mime = ImgBedUploadService._guess_mime(name, "image/jpeg")
        else:
            return None
        return local_path, mime

    async def _read_source(
        self,
        source: str,
        token: str,
        media_type: str,
    ) -> Tuple[str, bytes, str]:
        local_info = self._local_file_from_source(source)
        if local_info is not None:
            local_path, mime = local_info
            if not local_path.exists() or not local_path.is_file():
                raise ValidationException(f"Local file not found: {local_path}")
            filename = local_path.name or f"{media_type}"
            return filename, local_path.read_bytes(), mime

        session = await self.create()
        response = await AssetsDownloadReverse.request(session, token, source)

        if hasattr(response, "aiter_content"):
            chunks = bytearray()
            async for chunk in response.aiter_content():
                if chunk:
                    chunks.extend(chunk)
            content = bytes(chunks)
        else:
            content = response.content

        parsed = urlparse(source)
        path = parsed.path if (parsed.scheme or parsed.netloc) else source
        filename = Path(path).name or f"{media_type}"
        mime = response.headers.get("content-type", "").split(";", 1)[0].strip()
        if not mime:
            mime = self._guess_mime(filename, "application/octet-stream")
        return filename, content, mime

    @staticmethod
    def _extract_uploaded_url(upload_api_url: str, payload) -> str:
        src = ""
        if isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                src = str(first.get("src", "") or "").strip()
        elif isinstance(payload, dict):
            direct = payload.get("src")
            if isinstance(direct, str) and direct.strip():
                src = direct.strip()
            data = payload.get("data")
            if not src and isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict):
                    src = str(first.get("src", "") or "").strip()

        if not src:
            raise UpstreamException(
                "ImgBed upload failed: missing src in response",
                details={"payload": payload, "source": "imgbed"},
            )

        parsed = urlparse(src)
        if parsed.scheme and parsed.netloc:
            return src

        api_parsed = urlparse(upload_api_url)
        origin = f"{api_parsed.scheme}://{api_parsed.netloc}"
        return urljoin(origin, src)

    def is_managed_url(self, value: str) -> bool:
        upload_api_url = str(get_config("imgbed.upload_api_url", "") or "").strip()
        if not upload_api_url or not value:
            return False
        upload_parsed = urlparse(upload_api_url)
        value_parsed = urlparse(value)
        if (value_parsed.path or "").startswith("/v1/files/"):
            return False
        if (value_parsed.path or "") == (upload_parsed.path or ""):
            return False
        return bool(
            value_parsed.scheme
            and value_parsed.netloc
            and value_parsed.scheme == upload_parsed.scheme
            and value_parsed.netloc == upload_parsed.netloc
        )

    async def upload_bytes(self, filename: str, content: bytes, mime: str) -> str:
        if not self.is_enabled():
            raise ValidationException("ImgBed is not enabled")

        upload_api_url, auth_code, upload_folder = self._require_config()
        session = await self.create()
        timeout = float(get_config("asset.upload_timeout") or 60)
        browser = get_config("proxy.browser")
        active_proxy_key = None

        params = {
            "authCode": auth_code,
            "returnFormat": "full",
        }
        if upload_folder:
            params["uploadFolder"] = upload_folder

        async def _do_request():
            nonlocal active_proxy_key
            active_proxy_key, proxy_url = get_current_proxy_from("proxy.base_proxy_url")
            proxies = build_http_proxies(proxy_url)
            response = await session.post(
                upload_api_url,
                params=params,
                files={"file": (filename, content, mime)},
                proxies=proxies,
                timeout=timeout,
                impersonate=browser,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise UpstreamException(
                    f"ImgBed upload failed, {response.status_code}",
                    details={"status": response.status_code, "source": "imgbed"},
                )
            return response

        async def _on_retry(attempt: int, status_code: int, error: Exception, delay: float):
            if active_proxy_key and should_rotate_proxy(status_code):
                rotate_proxy(active_proxy_key)

        response = await retry_on_status(_do_request, on_retry=_on_retry)
        try:
            payload = response.json()
        except Exception as exc:
            logger.error(f"ImgBed upload returned invalid JSON: {exc}")
            raise UpstreamException(
                "ImgBed upload failed: invalid JSON response",
                details={
                    "status": response.status_code,
                    "error": str(exc),
                    "source": "imgbed",
                },
            )

        final_url = self._extract_uploaded_url(upload_api_url, payload)
        logger.info(f"ImgBed upload success: {filename} -> {final_url}")
        return final_url

    async def upload_from_source(self, source: str, token: str, media_type: str) -> str:
        if self.is_managed_url(source):
            return source
        filename, content, mime = await self._read_source(source, token, media_type)
        return await self.upload_bytes(filename, content, mime)


__all__ = ["ImgBedUploadService"]
