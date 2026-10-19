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
