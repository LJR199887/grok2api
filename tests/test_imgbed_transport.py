from app.dataplane.reverse.transport import imgbed


def test_defaults_include_imgbed_section():
    import tomllib
    from pathlib import Path

    defaults = Path(__file__).resolve().parents[1] / "config.defaults.toml"
    payload = tomllib.loads(defaults.read_text(encoding="utf-8"))

    assert payload["imgbed"] == {
        "enabled": False,
        "upload_api_url": "",
        "auth_code": "",
        "upload_folder": "",
    }


def test_extract_uploaded_url_supports_array_payload():
    value = imgbed._extract_uploaded_url(
        "https://demo.example/upload",
        [{"src": "/file/generated.png"}],
    )

    assert value == "https://demo.example/file/generated.png"


def test_extract_uploaded_url_supports_wrapped_data_payload():
    value = imgbed._extract_uploaded_url(
        "https://demo.example/upload",
        {"data": [{"src": "https://cdn.example/generated.png"}]},
    )

    assert value == "https://cdn.example/generated.png"


def test_extract_uploaded_url_rejects_missing_src():
    try:
        imgbed._extract_uploaded_url("https://demo.example/upload", {"data": []})
    except Exception as exc:
        assert "missing src" in str(exc)
    else:
        raise AssertionError("missing src should raise")


def test_is_managed_url_matches_upload_origin(monkeypatch):
    class FakeConfig:
        def get_str(self, key, default=""):
            if key == "imgbed.upload_api_url":
                return "https://demo.example/upload"
            return default

    monkeypatch.setattr(imgbed, "get_config", lambda: FakeConfig())

    assert imgbed.is_managed_url("https://demo.example/file/generated.png")
    assert not imgbed.is_managed_url("https://demo.example/upload")
    assert not imgbed.is_managed_url("https://other.example/file/generated.png")
