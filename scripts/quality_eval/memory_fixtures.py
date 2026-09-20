"""Isolated fixtures backed by the project's real ``MemoryStore``.

This module deliberately translates only explicit fixture facts.  It does not
infer a broader scope from a conversation surface or turn untrusted event text
into a memory write without a complete, declared update payload.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any


_TRUST_TO_PERMISSION = {
    "private": "private_fact",
    "confirmed": "public_preference",
    "stale": "public_preference",
    "old": "public_preference",
}
_SCOPES = {"private", "group"}


def _issue(store: Any, *, code: str, item: Any) -> None:
    issues = getattr(store, "quality_fixture_unsupported", None)
    if not isinstance(issues, list):
        issues = []
        setattr(store, "quality_fixture_unsupported", issues)
    issues.append({"code": code, "item": dict(item) if isinstance(item, dict) else {}})


def _case_dir(case: dict[str, Any], config: Any) -> Path:
    root = getattr(config, "isolated_data_dir", "") if not isinstance(config, dict) else config.get("isolated_data_dir", "")
    if not root:
        raise ValueError("isolated_data_dir is required for memory fixtures")
    case_id = str(case.get("id", "") or "").strip()
    if not case_id:
        raise ValueError("case id is required for memory fixtures")
    base = Path(str(root)).resolve()
    target = (base / case_id / "memory").resolve()
    if not target.is_relative_to(base):
        raise ValueError("case id escapes isolated directory")
    return target


def _config(case: dict[str, Any], config: Any) -> SimpleNamespace:
    source = vars(config) if hasattr(config, "__dict__") else dict(config or {})
    # The quality fixture has no remote embedding capability, even if a caller
    # accidentally supplies production-looking settings.
    source.update({
        "personification_data_dir": str(_case_dir(case, config)),
        "personification_memory_enabled": True,
        "personification_memory_palace_enabled": True,
        "personification_memory_retrieval_mode": "algorithm_llm",
        "personification_real_embedding_enabled": False,
        "personification_embedding_provider": "hash_bow",
        "personification_memory_recall_top_k": int(source.get("personification_memory_recall_top_k", 8) or 8),
        "personification_memory_retrieval_days": 0,
    })
    return SimpleNamespace(**source)


def _memory_id(case: dict[str, Any], ordinal: int) -> str:
    raw = f"quality-v1:{case['id']}:{ordinal}".encode("utf-8")
    return "quality-" + hashlib.sha256(raw).hexdigest()[:24]


def _seed_payload(case: dict[str, Any], seed: dict[str, Any], ordinal: int) -> dict[str, Any] | None:
    owner = str(seed.get("owner", "") or "").strip()
    fact = str(seed.get("fact", "") or "").strip()
    trust = str(seed.get("trust", "") or "").strip().lower()
    scope = str(seed.get("scope", "") or "").strip().lower()
    if not owner or not fact:
        return None
    if scope not in _SCOPES or trust not in _TRUST_TO_PERMISSION:
        return None
    # Scope, when explicitly declared, is the access boundary.  `confirmed`
    # describes freshness, not permission; a confirmed private fact remains
    # private rather than being widened to a public preference.
    default_permission = "private_fact" if scope == "private" else _TRUST_TO_PERMISSION[trust]
    permission = str(seed.get("permission_type", default_permission) or "")
    if scope == "private" and permission != "private_fact":
        return None
    if scope == "group" and permission not in {"public_preference", "group_meme", "conflict_memory"}:
        return None
    group_id = str(seed.get("group_id", "") or "").strip()
    if scope == "group" and not group_id:
        return None
    if scope == "private" and group_id:
        return None
    return {
        "memory_id": _memory_id(case, ordinal), "memory_type": str(seed.get("memory_type", "semantic") or "semantic"),
        "summary": fact, "user_id": owner, "group_id": group_id,
        "platform": str(seed.get("platform", "onebot") or "onebot"),
        "bot_id": str(seed.get("bot_id", "quality-bot") or "quality-bot"),
        "permission_type": permission, "group_scope": "isolated" if scope == "group" else "shared",
        "cross_group_allowed": False, "revision": int(seed.get("revision", 1) or 1),
        "source_kind": "quality_fixture", "supports_recall": True,
    }


def build_memory_store(case: dict[str, Any], config: Any, logger: Any = None):
    """Build and seed a real, case-scoped MemoryStore without external embeddings."""
    from plugin.personification.core.memory_store import MemoryStore

    store = MemoryStore(_config(case, config), logger=logger)
    store.initialize()
    setattr(store, "quality_fixture_unsupported", [])
    for ordinal, raw_seed in enumerate(list(case.get("seed_memory") or []), start=1):
        if not isinstance(raw_seed, dict):
            _issue(store, code="seed_not_object", item={})
            continue
        payload = _seed_payload(case, raw_seed, ordinal)
        if payload is None:
            _issue(store, code="seed_owner_scope_or_trust_unsupported", item=raw_seed)
            continue
        store.write_memory_item(payload)
    return store


def apply_memory_event(store: Any, event: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Apply only an explicit, scoped `memory_update` event via store CAS."""
    if not isinstance(event, dict) or event.get("kind") != "memory_update":
        return {"status": "unsupported", "code": "event_not_memory_update"}
    update = event.get("update")
    if not isinstance(update, dict):
        _issue(store, code="memory_update_missing_payload", item=event)
        return {"status": "unsupported", "code": "memory_update_missing_payload"}
    payload = _seed_payload(case, update, int(update.get("ordinal", 1) or 1))
    if payload is None or not update.get("memory_id") or "revision" not in update:
        _issue(store, code="memory_update_owner_scope_or_revision_unsupported", item=event)
        return {"status": "unsupported", "code": "memory_update_owner_scope_or_revision_unsupported"}
    payload["memory_id"] = str(update["memory_id"])
    payload["revision"] = int(update["revision"])
    if update.get("superseded_by") is not None:
        payload["superseded_by"] = str(update["superseded_by"])
    memory_id = store.write_memory_item(payload)
    return {"status": "applied", "memory_id": memory_id, "revision": payload["revision"]}
