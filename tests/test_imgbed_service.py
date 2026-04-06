import asyncio
import tomllib
from pathlib import Path

import pytest

from app.core.exceptions import UpstreamException
from app.services.grok.services.image_edit import ImageCollectProcessor
from app.services.grok.services.image import ImageWSCollectProcessor
from app.services.grok.utils.download import DownloadService
from app.services.grok.utils.imgbed import ImgBedUploadService
from app.services.grok.utils.retry import rate_limited


def test_config_defaults_include_imgbed_section():
    defaults_path = Path(__file__).resolve().parents[1] / "config.defaults.toml"
    with defaults_path.open("rb") as fp:
        payload = tomllib.load(fp)

    assert "imgbed" in payload
    assert payload["imgbed"]["enabled"] is False
    assert payload["imgbed"]["upload_api_url"] == ""
    assert payload["imgbed"]["auth_code"] == ""
    assert payload["imgbed"]["upload_folder"] == ""


def test_imgbed_extract_uploaded_url_supports_both_payload_shapes():
    assert (
        ImgBedUploadService._extract_uploaded_url(
            "https://demo.example/upload",
            [{"src": "https://cdn.example/a.png"}],
        )
        == "https://cdn.example/a.png"
    )
    assert (
        ImgBedUploadService._extract_uploaded_url(
            "https://demo.example/upload",
            {"data": [{"src": "https://cdn.example/b.png"}]},
        )
        == "https://cdn.example/b.png"
    )


def test_imgbed_extract_uploaded_url_normalizes_relative_src():
    value = ImgBedUploadService._extract_uploaded_url(
        "https://demo.example/upload",
        [{"src": "/file/abc.png"}],
    )
    assert value == "https://demo.example/file/abc.png"


def test_imgbed_build_auth_variants_supports_auth_code_and_api_token():
    assert ImgBedUploadService._build_auth_variants("secret") == [
        ("authCode", {"authCode": "secret"}, {}),
        ("bearer", {}, {"Authorization": "Bearer secret"}),
        ("authorization", {}, {"Authorization": "secret"}),
    ]


def test_imgbed_does_not_treat_local_v1_files_as_uploaded(monkeypatch):
    monkeypatch.setattr(
        "app.services.grok.utils.imgbed.get_config",
        lambda key, default=None: "https://demo.example/upload" if key == "imgbed.upload_api_url" else default,
    )
    service = ImgBedUploadService()
    assert service.is_managed_url("https://demo.example/v1/files/image/demo.png") is False
    assert service.is_managed_url("https://demo.example/file/demo.png") is True


def test_imgbed_read_source_supports_local_v1_files(monkeypatch, tmp_path):
    monkeypatch.setattr("app.services.grok.utils.imgbed.DATA_DIR", tmp_path)
    image_dir = tmp_path / "tmp" / "image"
    image_dir.mkdir(parents=True, exist_ok=True)
    target = image_dir / "sample.png"
    target.write_bytes(b"png-bytes")

    service = ImgBedUploadService()
    filename, content, mime = asyncio.run(
        service._read_source("/v1/files/image/sample.png", token="", media_type="image")
    )

    assert filename == "sample.png"
    assert content == b"png-bytes"
    assert mime == "image/png"


def test_download_service_resolve_url_uses_imgbed(monkeypatch):
    class FakeImgBed:
        async def upload_from_source(self, source, token, media_type):
            return f"https://imgbed.example/{media_type}"

    monkeypatch.setattr(ImgBedUploadService, "is_enabled", staticmethod(lambda: True))
    monkeypatch.setattr(DownloadService, "_get_imgbed", lambda self: FakeImgBed())

    service = DownloadService()
    result = asyncio.run(service.resolve_url("https://assets.grok.com/demo.png", "tok", "image"))

    assert result == "https://imgbed.example/image"


def test_imgbed_upload_bytes_falls_back_to_authorization_header(monkeypatch):
    monkeypatch.setattr(
        "app.services.grok.utils.imgbed.get_config",
        lambda key, default=None: {
            "imgbed.enabled": True,
            "imgbed.upload_api_url": "https://demo.example/upload",
            "imgbed.auth_code": "secret-token",
            "imgbed.upload_folder": "",
            "asset.upload_timeout": 60,
            "proxy.browser": None,
            "proxy.base_proxy_url": "",
        }.get(key, default),
    )
    monkeypatch.setattr(
        "app.services.grok.utils.imgbed.get_current_proxy_from",
        lambda key: (None, None),
    )
    monkeypatch.setattr(
        "app.services.grok.utils.imgbed.build_http_proxies",
        lambda proxy_url: None,
    )

    class FakeResponse:
        def __init__(self, status_code, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = []

        async def post(self, url, params=None, headers=None, files=None, proxies=None, timeout=None, impersonate=None):
            self.calls.append({"url": url, "params": params, "headers": headers, "files": files})
            if len(self.calls) == 1:
                return FakeResponse(403, text="forbidden")
            return FakeResponse(200, payload=[{"src": "/file/uploaded.png"}])

    service = ImgBedUploadService()
    fake_session = FakeSession()

    async def fake_create():
        return fake_session

    monkeypatch.setattr(service, "create", fake_create)

    result = asyncio.run(service.upload_bytes("demo.png", b"png-bytes", "image/png"))

    assert result == "https://demo.example/file/uploaded.png"
    assert fake_session.calls[0]["params"]["authCode"] == "secret-token"
    assert fake_session.calls[1]["headers"]["Authorization"] == "Bearer secret-token"


def test_image_collect_processor_uploads_only_final_images(monkeypatch):
    class FakeImgBed:
        async def upload_from_source(self, source, token, media_type):
            return "https://imgbed.example/final.png"

    async def fake_save_blob(image_id, blob, is_final, ext=None):
        return f"/v1/files/image/{image_id}.png"

    monkeypatch.setattr(ImgBedUploadService, "is_enabled", staticmethod(lambda: True))

    processor = ImageWSCollectProcessor("grok-imagine-1.0", "tok", response_format="url")
    monkeypatch.setattr(processor, "_get_imgbed", lambda: FakeImgBed())
    monkeypatch.setattr(processor, "_save_blob", fake_save_blob)

    final_output = asyncio.run(
        processor._to_output(
            "final-image",
            {"blob": "ignored", "is_final": True, "ext": "png"},
        )
    )
    partial_output = asyncio.run(
        processor._to_output(
            "preview-image",
            {"blob": "ignored", "is_final": False, "ext": "png"},
        )
    )

    assert final_output == "https://imgbed.example/final.png"
    assert partial_output == "/v1/files/image/preview-image.png"


def test_image_collect_processor_raises_imgbed_error(monkeypatch):
    monkeypatch.setattr(ImgBedUploadService, "is_enabled", staticmethod(lambda: True))

    processor = ImageWSCollectProcessor("grok-imagine-1.0", "tok", response_format="url")

    async def fake_save_or_upload_blob(image_id, blob, is_final, ext=None):
        raise UpstreamException(
            "ImgBed upload failed, 429",
            details={"status": 429, "source": "imgbed"},
        )

    monkeypatch.setattr(processor, "_save_or_upload_blob", fake_save_or_upload_blob)

    with pytest.raises(UpstreamException, match="ImgBed upload failed, 429"):
        asyncio.run(
            processor._to_output(
                "final-image",
                {"blob": "ignored", "is_final": True, "ext": "png"},
            )
        )


def test_app_chat_image_collect_processor_raises_imgbed_error(monkeypatch):
    monkeypatch.setattr(ImgBedUploadService, "is_enabled", staticmethod(lambda: True))

    processor = ImageCollectProcessor("grok-imagine-1.0", "tok", response_format="url")

    async def fake_process_url(path, media_type="image"):
        raise UpstreamException(
            "ImgBed upload failed, 429",
            details={"status": 429, "source": "imgbed"},
        )

    async def response():
        yield (
            b'data: {"result":{"response":{"modelResponse":{"generatedImageUrls":'
            b'["https://assets.grok.com/demo.png"]}}}}\n\n'
        )

    monkeypatch.setattr(processor, "process_url", fake_process_url)

    with pytest.raises(UpstreamException, match="ImgBed upload failed, 429"):
        asyncio.run(processor.process(response()))


def test_rate_limited_ignores_imgbed_429():
    err = UpstreamException(
        "ImgBed upload failed, 429",
        details={"status": 429, "source": "imgbed"},
    )
    assert rate_limited(err) is False
