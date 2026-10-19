from types import SimpleNamespace
import pytest
from ._loader import load_personification_module

runtime = load_personification_module('plugin.personification.core.runtime_integrations')


def test_shared_backend_missing_configuration_fails_closed():
    with pytest.raises(ValueError, match='共享好感度'):
        runtime.build_sign_in_fallbacks(SimpleNamespace(personification_favorability_shared_enabled=True))


def test_shared_backend_failure_does_not_fall_back(monkeypatch):
    import sys
    import types
    accounts = types.ModuleType('shared_accounts')
    accounts.SharedAccounts = lambda db: object()
    profiles = types.ModuleType('shared_favorability_profiles')
    class BrokenBackend:
        def __init__(self, *args, **kwargs):
            pass
        def load_document(self):
            raise RuntimeError('migration marker absent')
    profiles.SharedFavorabilityProfiles = BrokenBackend
    monkeypatch.setitem(sys.modules, 'shared_accounts', accounts)
    monkeypatch.setitem(sys.modules, 'shared_favorability_profiles', profiles)
    config = SimpleNamespace(personification_favorability_shared_enabled=True,
                             personification_favorability_shared_db='isolated',
                             personification_favorability_shared_app='123')
    with pytest.raises(RuntimeError, match='migration marker absent'):
        runtime.build_sign_in_fallbacks(config)


@pytest.mark.parametrize('shared', [False, True])
def test_isolated_runtime_never_builds_legacy_sign_in_adapter(monkeypatch, shared):
    import sys
    import types
    def forbidden():
        pytest.fail('legacy sign_in adapter must not be built')
    monkeypatch.setattr(runtime, 'build_external_sign_in_adapter', forbidden)
    accounts = types.ModuleType('shared_accounts')
    accounts.SharedAccounts = lambda db: object()
    profiles = types.ModuleType('shared_favorability_profiles')
    class Backend:
        def __init__(self, *args, **kwargs):
            pass
        def load_document(self):
            return {}
    profiles.SharedFavorabilityProfiles = Backend
    monkeypatch.setitem(sys.modules, 'shared_accounts', accounts)
    monkeypatch.setitem(sys.modules, 'shared_favorability_profiles', profiles)
    result = runtime.build_sign_in_fallbacks(SimpleNamespace(
        personification_favorability_shared_enabled=shared,
        personification_favorability_shared_db='isolated',
        personification_favorability_shared_app='test',
        personification_qq_official_enabled=not shared,
    ))
    external = result[-1].external
    assert result[-2] is False
    assert external.available is False
    assert external.get_user_data('123') == {}
    assert external.load_data() == {}
    external.update_user_data('123', {})
    assert isinstance(external.get_level_name(0), str)


def test_onebot_runtime_retains_legacy_adapter(monkeypatch):
    calls = []
    external = runtime.ExternalFavorabilityAdapter(True, lambda uid: {}, lambda *a: None, lambda: {}, lambda score: 'test')
    def legacy():
        calls.append('legacy')
        return external
    monkeypatch.setattr(runtime, 'build_external_sign_in_adapter', legacy)
    result = runtime.build_sign_in_fallbacks(SimpleNamespace())
    assert calls == ['legacy']
    assert result[-2] is True
    assert result[-1].external is external
