"""CloudFlare ImgBed upload transport."""

import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import curl_cffi
import orjson

from app.control.proxy.models import (
    ProxyFeedback,
    ProxyFeedbackKind,
    ProxyScope,
    RequestKind,
)
from app.dataplane.proxy import get_proxy_runtime
from app.dataplane.proxy.adapters.session import ResettableSession
from app.dataplane.reverse.transport.assets import download_asset
from app.platform.config.snapshot import get_config
from app.platform.errors import UpstreamError, ValidationError
from app.platform.logging.logger import logger


def is_imgbed_enabled() -> bool:
    return get_config().get_bool("imgbed.enabled", False)


def _require_config() -> tuple[str, str, str]:
    cfg = get_config()
    upload_api_url = cfg.get_str("imgbed.upload_api_url", "").strip()
    auth_code = cfg.get_str("imgbed.auth_code", "").strip()
    upload_folder = cfg.get_str("imgbed.upload_folder", "").strip()

    if not upload_api_url:
        raise ValidationError("ImgBed upload_api_url is required", param="imgbed.upload_api_url")
    parsed = urlparse(upload_api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError(
            "ImgBed upload_api_url must be a valid HTTP URL",
            param="imgbed.upload_api_url",
        )
    if not auth_code:
        raise ValidationError("ImgBed auth_code is required", param="imgbed.auth_code")
    return upload_api_url, auth_code, upload_folder


def _extract_uploaded_url(upload_api_url: str, payload: Any) -> str:
    src = ""
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            src = str(first.get("src") or "").strip()
    elif isinstance(payload, dict):
        direct = payload.get("src")
        if isinstance(direct, str) and direct.strip():
            src = direct.strip()
        data = payload.get("data")
        if not src and isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict):
                src = str(first.get("src") or "").strip()

    if not src:
        raise UpstreamError("ImgBed upload failed: missing src in response")

    parsed = urlparse(src)
    if parsed.scheme and parsed.netloc:
        return src

    api_parsed = urlparse(upload_api_url)
    origin = f"{api_parsed.scheme}://{api_parsed.netloc}"
    return urljoin(origin, src)


def _guess_mime(filename: str, fallback: str) -> str:
    mime, _encoding = mimetypes.guess_type(filename)
    return mime or fallback


def filename_from_url(url: str, media_type: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    if name:
        return name
    return "generated_video.mp4" if media_type == "video" else "generated_image.png"


def is_managed_url(value: str) -> bool:
    upload_api_url = get_config().get_str("imgbed.upload_api_url", "").strip()
    if not upload_api_url or not value:
        return False
    upload_parsed = urlparse(upload_api_url)
    value_parsed = urlparse(value)
    if not (value_parsed.scheme and value_parsed.netloc):
        return False
    if value_parsed.path == upload_parsed.path:
        return False
    return (
        value_parsed.scheme == upload_parsed.scheme
        and value_parsed.netloc == upload_parsed.netloc
    )


async def upload_bytes_to_imgbed(filename: str, content: bytes, mime: str) -> str:
    if not is_imgbed_enabled():
        raise ValidationError("ImgBed is not enabled", param="imgbed.enabled")
    if not content:
        raise UpstreamError("ImgBed upload failed: empty content")

    upload_api_url, auth_code, upload_folder = _require_config()
    cfg = get_config()
    timeout_s = cfg.get_float("asset.upload_timeout", 60.0)
    params = {"authCode": auth_code, "returnFormat": "full"}
    if upload_folder:
        params["uploadFolder"] = upload_folder

    proxy = await get_proxy_runtime()
    lease = await proxy.acquire(scope=ProxyScope.ASSET, kind=RequestKind.HTTP, resource=True)
    multipart = curl_cffi.CurlMime()
    multipart.addpart(
        name="file",
        content_type=mime or _guess_mime(filename, "application/octet-stream"),
        filename=filename,
        data=content,
    )
    try:
        try:
            async with ResettableSession(lease=lease) as session:
                response = await session.post(
                    upload_api_url,
                    params=params,
                    multipart=multipart,
                    timeout=timeout_s,
                )
        except UpstreamError:
            await proxy.feedback(lease, ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR))
            raise
        except Exception as exc:
            await proxy.feedback(lease, ProxyFeedback(kind=ProxyFeedbackKind.TRANSPORT_ERROR))
            raise UpstreamError(f"ImgBed upload transport failed: {exc}") from exc

        if response.status_code < 200 or response.status_code >= 300:
            await proxy.feedback(
                lease,
                ProxyFeedback(
                    kind=ProxyFeedbackKind.UPSTREAM_5XX
                    if response.status_code >= 500
                    else ProxyFeedbackKind.FORBIDDEN,
                    status_code=response.status_code,
                ),
            )
            body = response.content.decode("utf-8", "replace")[:300]
            raise UpstreamError(
                f"ImgBed upload failed, {response.status_code}",
                status=response.status_code,
                body=body,
            )

        await proxy.feedback(
            lease,
            ProxyFeedback(kind=ProxyFeedbackKind.SUCCESS, status_code=response.status_code),
        )
        try:
            payload = response.json()
        except Exception as exc:
            body = response.content.decode("utf-8", "replace")[:300]
            logger.error("ImgBed upload returned invalid JSON: {}", exc)
            raise UpstreamError(
                "ImgBed upload failed: invalid JSON response",
                body=body,
            ) from exc

        final_url = _extract_uploaded_url(upload_api_url, payload)
        logger.info("ImgBed upload success: {} -> {}", filename, final_url)
        return final_url
    finally:
        multipart.close()


async def upload_source_to_imgbed(token: str, source: str, media_type: str) -> str:
    if is_managed_url(source):
        return source
    try:
        stream, content_type = await download_asset(token, source)
        chunks: list[bytes] = []
        async for chunk in stream:
            chunks.append(chunk)
    except UpstreamError:
        raise
    except Exception as exc:
        raise UpstreamError(f"ImgBed source download failed: {exc}") from exc

    filename = filename_from_url(source, media_type)
    fallback = "video/mp4" if media_type == "video" else "image/jpeg"
    mime = content_type or _guess_mime(filename, fallback)
    return await upload_bytes_to_imgbed(filename, b"".join(chunks), mime)


def parse_json_bytes(raw: bytes) -> Any:
    return orjson.loads(raw)


__all__ = [
    "filename_from_url",
    "is_imgbed_enabled",
    "is_managed_url",
    "parse_json_bytes",
    "upload_bytes_to_imgbed",
    "upload_source_to_imgbed",
    "_extract_uploaded_url",
]
