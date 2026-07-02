from __future__ import annotations

from pathlib import Path


def test_config_search_is_ime_aware_and_searches_aliases() -> None:
    app_core_js = (
        Path(__file__).resolve().parents[1] / "webui" / "static" / "app-core.js"
    ).read_text(encoding="utf-8")
    app_config_js = (
        Path(__file__).resolve().parents[1] / "webui" / "static" / "app-config.js"
    ).read_text(encoding="utf-8")

    assert "oncompositionstart=\"onConfigSearchCompositionStart(this)\"" in app_config_js
    assert "oncompositionend=\"onConfigSearchCompositionEnd(this)\"" in app_config_js
    assert "oninput=\"onConfigSearchInput(this,event)\"" in app_config_js
    assert "event.isComposing" in app_core_js
    assert "state.configSearchComposing" in app_core_js
    assert "entry.aliases" in app_config_js
    assert "entry.search_index" in app_config_js
    assert "configSearchHaystack" in app_config_js
    assert "configSearchEntryScore" in app_config_js
    assert "configEditDistanceWithin" in app_config_js


def test_config_api_pool_model_probe_dropdown_is_present() -> None:
    app_config_js = (
        Path(__file__).resolve().parents[1] / "webui" / "static" / "app-config.js"
    ).read_text(encoding="utf-8")
    assert "probeApiProviderModels" in app_config_js
    assert 'api("/config/provider-models"' in app_config_js
    assert "<datalist" in app_config_js
    assert "data-provider-model-select" in app_config_js
    assert "selectApiProviderModel" in app_config_js
    assert "syncApiProviderModelSelect" in app_config_js
    assert "normalizeApiProviderModels" in app_config_js
    assert "updateApiProviderModelControls" in app_config_js
    assert "apiProviderModelProbeCache" in app_config_js
    assert "hydrateApiProviderModelProbe" in app_config_js
    assert "cacheApiProviderModelProbe(field, index, providers[index])" in app_config_js
    assert "item.id || item.model || item.name || item.slug" in app_config_js
    assert "探测模型" in app_config_js
    assert "sanitizeApiProvider" in app_config_js
    assert "delete out._model_options" in app_config_js
    assert "delete out._model_probe_done" in app_config_js
    assert "_model_probe_done: true" in app_config_js
    assert "先探测模型" in app_config_js
    assert "未探测到可选模型" in app_config_js
    assert "options.length || probeDone" not in app_config_js
    assert "const models = normalizeApiProviderModels(result.models)" in app_config_js
