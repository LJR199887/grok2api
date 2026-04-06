import asyncio
import tomllib
from pathlib import Path

from app.services.grok.services.image import ImageWSCollectProcessor
from app.services.grok.utils.download import DownloadService
from app.services.grok.utils.imgbed import ImgBedUploadService


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
