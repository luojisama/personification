from __future__ import annotations

from pathlib import Path


def test_config_search_is_ime_aware_and_searches_aliases() -> None:
    app_js = (Path(__file__).resolve().parents[1] / "webui" / "app.py").read_text(encoding="utf-8")

    assert "oncompositionstart=\"onConfigSearchCompositionStart(this)\"" in app_js
    assert "oncompositionend=\"onConfigSearchCompositionEnd(this)\"" in app_js
    assert "oninput=\"onConfigSearchInput(this,event)\"" in app_js
    assert "event.isComposing" in app_js
    assert "state.configSearchComposing" in app_js
    assert "entry.aliases" in app_js
    assert "configSearchHaystack" in app_js
