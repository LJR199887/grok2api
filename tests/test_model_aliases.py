from app.control.model import registry


def test_legacy_imagine_model_aliases_resolve_to_current_models():
    assert registry.canonical_name("grok-imagine-1.0") == "grok-imagine-image"
    assert registry.canonical_name("grok-imagine-1.0-fast") == "grok-imagine-image-lite"
    assert registry.canonical_name("grok-imagine-1.0-edit") == "grok-imagine-image-edit"
    assert registry.canonical_name("grok-imagine-1.0-video") == "grok-imagine-video"


def test_legacy_imagine_model_aliases_return_specs():
    assert registry.get("grok-imagine-1.0").model_name == "grok-imagine-image"
    assert registry.get("grok-imagine-1.0-fast").model_name == "grok-imagine-image-lite"
    assert registry.get("grok-imagine-1.0-edit").model_name == "grok-imagine-image-edit"
    assert registry.get("grok-imagine-1.0-video").model_name == "grok-imagine-video"
