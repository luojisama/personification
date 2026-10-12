"""Read-only references from a verified account link, never identity aliases."""
from __future__ import annotations

import asyncio
import inspect
import math
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any

from .account_links import telegram_user_scope

_TG_SCOPE = re.compile(r"p8:telegramb([0-9]+):([1-9][0-9]*):u([0-9]+):([1-9][0-9]*)\Z")
_PROFILE_KEYS = frozenset({
    "occupation", "age_group", "gender", "routine", "interests", "communication_style",
    "emotion_baseline", "social_mode", "knowledge", "nickname_pref", "taboos",
    "recent_focus", "content_pref", "portrait",
})
_QQ_META_KEYS = frozenset({"nickname", "sex", "age", "qid", "signature", "area"})


@dataclass(frozen=True)
class LinkedSource:
    service: Any = field(repr=False)
    link: Any = field(repr=False)
    platform: str
    bot_id: str
    user_id: str

    @property
    def scope(self) -> dict[str, str]:
        return {"platform": self.platform, "bot_id": self.bot_id, "user_id": self.user_id, "group_id": ""}

    def active(self) -> bool:
        try:
            current = self.service.resolve_telegram(self.link.tg_bot_id, self.link.tg_user_id)
            return bool(current and current == self.link and current.link_id)
        except Exception:
            return False

    def metadata(self, storage_scope: str = "private") -> dict[str, str]:
        return {**self.scope, "storage_scope": storage_scope, "authorization": "verified_account_link"}


def resolve_linked_sources(runtime: Any, scope: dict[str, str], surface: str = "chat") -> list[LinkedSource]:
    """Resolve only trusted private chat identities; no global service fallback."""
    service = getattr(runtime, "account_link_service", None)
    if service is None or scope.get("group_id") or surface != "chat":
        return []
    platform, bot, user = (str(scope.get(key) or "") for key in ("platform", "bot_id", "user_id"))
    try:
        if platform == "telegram":
            match = _TG_SCOPE.fullmatch(user)
            if not match:
                return []
            raw_bot, raw_user = match[2], match[4]
            if (bot, user) != telegram_user_scope(raw_bot, raw_user):
                return []
            link = service.resolve_telegram(raw_bot, raw_user)
            links = [link] if link else []
            return [LinkedSource(service, link, "onebot", link.qq_bot_id, link.qq_user_id)
                    for link in links if link.link_id]
        if platform == "onebot":
            links = service.links_for_qq(bot, user)
            return [LinkedSource(service, link, "telegram", *link.telegram_scope)
                    for link in links[:8] if link.link_id]
    except Exception:
        return []
    return []


async def source_authorized(runtime: Any, source: LinkedSource) -> bool:
    gate = getattr(runtime, "user_policy_gate", None) or getattr(runtime, "qq_user_policy_gate", None)
    authorize = getattr(gate, "current_authorization", None)
    if not source.active() or not callable(authorize):
        return False
    try:
        authorization = await authorize(source.user_id)
        return bool(getattr(authorization, "allow_context_read", False)) and source.active()
    except Exception:
        return False


def source_authorized_now(gate: Any, source: LinkedSource) -> bool:
    """Synchronous local policy check for the final prompt render boundary."""
    authorize = getattr(getattr(gate, "service", None), "authorize", None)
    legacy_checker = getattr(gate, "legacy_block_checker", None)
    if not source.active() or not callable(authorize) or inspect.iscoroutinefunction(authorize):
        return False
    try:
        if legacy_checker is not None:
            if not callable(legacy_checker) or inspect.iscoroutinefunction(legacy_checker):
                return False
            legacy_blocked = legacy_checker(source.user_id)
            if inspect.isawaitable(legacy_blocked):
                if inspect.iscoroutine(legacy_blocked):
                    legacy_blocked.close()
                return False
            if legacy_blocked:
                return False
        authorization = authorize(source.user_id)
        if inspect.isawaitable(authorization):
            if inspect.iscoroutine(authorization):
                authorization.close()
            return False
        return bool(getattr(authorization, "allow_context_read", False)) and source.active()
    except Exception:
        return False


def bind_linked_source_guard(gate: Any, source: LinkedSource) -> None:
    """Keep the source's authorization live until this turn's external use."""
    from .llm_context import register_llm_source_guard
    register_llm_source_guard(source.link.link_id, lambda: source_authorized_now(gate, source))


async def filter_authorized_contexts(runtime: Any, contexts: list[LinkedAccountContext]) -> list[LinkedAccountContext]:
    """Recheck every source together immediately before an exposure boundary."""
    allowed = await asyncio.gather(*(source_authorized(runtime, context.source) for context in contexts))
    return [context for context, authorized in zip(contexts, allowed)
            if authorized and source_authorized_now(context.policy_gate, context.source)]


def _exact_private(item: dict[str, Any], source: LinkedSource) -> bool:
    return (all(str(item.get(key) or "") == value for key, value in source.scope.items())
            and not item.get("topic_id") and not item.get("thread_id"))


def _reference_memory(item: dict[str, Any], source: LinkedSource) -> dict[str, Any]:
    keys = ("memory_id", "summary", "memory_type", "source_kind", "time_created", "time_hint",
            "confirmation", "kind", "expires_at", "confidence", "salience", "stability",
            "permission_type", "supports_recall", "tier", "score", "why_relevant",
            "platform", "bot_id", "group_id", "user_id", "topic_id", "palace_zone")
    result = {key: item.get(key) for key in keys if key in item}
    result["summary"] = str(result.get("summary") or "")[:800]
    # Original source text is not needed to establish the already verified card.
    refs = item.get("source_refs")
    result["source_refs"] = [{key: ref.get(key) for key in ("message_id", "user_id", "timestamp")}
                             for ref in (refs[:30] if isinstance(refs, list) else []) if isinstance(ref, dict)]
    result["source_scope"] = source.metadata()
    result["trust"], result["usage"] = "untrusted_data_only", "reference_only"
    return result


def _unexpired(item: dict[str, Any], *, require_expiry: bool = False) -> bool:
    try:
        expiry = float(item.get("expires_at") or 0)
        return math.isfinite(expiry) and (expiry > time.time() or expiry == 0 and not require_expiry)
    except (TypeError, ValueError):
        return False


async def recall_linked_memories(runtime: Any, source: LinkedSource, query: str, limit: int = 12,
                                 mode: str = "auto", scope: str = "auto") -> list[dict[str, Any]]:
    store = getattr(runtime, "memory_store", None)
    if store is None or not await source_authorized(runtime, source):
        return []
    kwargs = {**source.scope, "query": query, "scope": scope, "context_type": "private",
              "limit": max(1, min(32, limit)), "mode": mode}
    try:
        recall = getattr(store, "arecall_memories", None)
        items = await recall(**kwargs) if callable(recall) else await asyncio.to_thread(store.recall_memories, **kwargs)
    except Exception:
        return []
    if not await source_authorized(runtime, source):
        return []
    return [_reference_memory(item, source) for item in items if isinstance(item, dict)
            and _exact_private(item, source) and item.get("memory_type") != "experience_card"
            and bool(item.get("supports_recall", True))
            and _unexpired(item)][:max(1, min(32, limit))]


async def read_linked_experiences(runtime: Any, source: LinkedSource, limit: int = 20) -> list[dict[str, Any]]:
    """Expose source-backed private cards using the same verified-link boundary."""
    config = getattr(runtime, "plugin_config", None)
    store = getattr(runtime, "memory_store", None)
    if (store is None or not bool(getattr(config, "personification_experience_cards_enabled", False))
            or not await source_authorized(runtime, source)):
        return []
    from .experience_cards import ExperienceScope, ExperienceService
    try:
        cards = await asyncio.to_thread(ExperienceService(store).list, ExperienceScope(**source.scope),
                                       limit=max(1, min(32, limit)), active_only=False)
    except Exception:
        return []
    result = []
    for item in cards:
        if not isinstance(item, dict) or not _exact_private(item, source) or not _unexpired(item):
            continue
        refs = item.get("source_refs")
        if not isinstance(refs, list) or not refs:
            continue
        owners = {str(ref.get("user_id") or "") for ref in refs if isinstance(ref, dict)}
        if source.user_id not in owners or not owners <= {source.user_id, source.bot_id}:
            continue
        reference = _reference_memory(item, source)
        reference.update(memory_type="experience_card", source_kind="experience_card", permission_type="private_fact",
                         palace_zone="recent_episode")
        result.append(reference)
    return result if await source_authorized(runtime, source) else []


async def read_linked_history(runtime: Any, source: LinkedSource, limit: int = 12, days: float = 14,
                              query_terms: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    if not await source_authorized(runtime, source):
        return []
    from .session_store import build_private_session_id, get_recent_session_candidates
    from .context_policy import stringify_history_content
    from .message_provenance import is_human_chat_record
    limit = max(1, min(48, limit))
    try:
        rows = await asyncio.to_thread(get_recent_session_candidates, build_private_session_id(source.user_id),
            limit=limit, days=days, platform=source.platform, bot_id=source.bot_id,
            query_terms=query_terms)
    except Exception:
        return []
    result = []
    for row in rows:
        if (not isinstance(row, dict) or row.get("is_summary") or row.get("group_id")
                or row.get("topic_id") or row.get("thread_id")):
            continue
        if any(str(row.get(key) or "") != value for key, value in (("platform", source.platform), ("bot_id", source.bot_id))):
            continue
        role, owner = str(row.get("role") or ""), str(row.get("user_id") or "")
        if role == "user" and (owner != source.user_id or not is_human_chat_record(row, source.bot_id)):
            continue
        if role == "assistant" and (owner != source.bot_id or row.get("source_kind") != "bot_reply"
                                    or row.get("delivery_status") in {"unknown", "unconfirmed", "failed"}):
            continue
        if role not in {"user", "assistant"}:
            continue
        from .memory_context import timestamp_label
        result.append({"id": row.get("id"), "message_id": str(row.get("message_id") or ""),
                       "role": role, "speaker": owner, "user_id": owner,
                       "content": stringify_history_content(row.get("content", ""))[:600],
                       "timestamp": row.get("timestamp"), "time": timestamp_label(row.get("timestamp")),
                       "source_kind": row.get("source_kind"),
                       "delivery_status": row.get("delivery_status", "recorded_reply" if role == "assistant" else "user_message"),
                       "source_scope": source.metadata(), "trust": "untrusted_data_only", "usage": "reference_only"})
    return result[-limit:] if await source_authorized(runtime, source) else []


def _claims(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        return []
    document_scope = document.get("scope")
    if isinstance(document_scope, dict) and document_scope.get("kind") == "group":
        return []
    claims = document.get("claims")
    return [{"key": claim["key"], "value": str(claim.get("value") or "")[:240],
             "confidence": claim.get("confidence"), "source": claim.get("source")}
            for claim in (claims if isinstance(claims, list) else []) if isinstance(claim, dict)
            and claim.get("source") != "group_generated"
            and claim.get("key") in _PROFILE_KEYS and str(claim.get("value") or "").strip()][:16]


@dataclass
class LinkedAccountContext:
    source: LinkedSource
    profiles: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    experience_candidates: list[dict] = field(default_factory=list)
    memories: list[dict] = field(default_factory=list)
    policy_gate: Any = field(default=None, repr=False)

    def render_payload(self) -> dict[str, Any] | None:
        if not source_authorized_now(self.policy_gate, self.source):
            return None
        bind_linked_source_guard(self.policy_gate, self.source)
        return {"source_scope": self.source.metadata(), "profiles": self.profiles, "history": self.history,
                "recalled_evidence": self.memories, "trust": "untrusted_data_only", "usage": "reference_only"}


async def collect_linked_context(runtime: Any, scope: dict[str, str], surface: str = "chat") -> list[LinkedAccountContext]:
    sources = resolve_linked_sources(runtime, scope, surface)
    contexts = []
    for source in sources:
        if not await source_authorized(runtime, source):
            continue
        context = LinkedAccountContext(source, policy_gate=getattr(runtime, "user_policy_gate", None)
                                       or getattr(runtime, "qq_user_policy_gate", None))
        config = runtime.plugin_config
        profiles = getattr(runtime, "scoped_profile_service", None)
        persona_enabled = bool(getattr(config, "personification_persona_enabled", True))
        if profiles is not None and persona_enabled:
            try:
                snapshot = profiles.get_scoped_document_v3(**source.scope)
                claims = _claims(snapshot.get("document")) if snapshot else []
                if claims:
                    context.profiles.append({"claims": claims, "source_scope": source.metadata()})
            except Exception:
                pass
        # Legacy QQ user-global personal fields are explicitly shared by the
        # account owner. Group roles, local profiles and evidence are excluded.
        legacy_profiles = getattr(runtime, "profile_service", None)
        if source.platform == "onebot" and legacy_profiles is not None and persona_enabled:
            try:
                enabled = getattr(legacy_profiles, "is_enabled", None)
                snapshot = legacy_profiles.get_core_profile(source.user_id) if not callable(enabled) or enabled() else None
                if snapshot:
                    from .scoped_profile import build_global_profile_document
                    profile = snapshot.profile_json
                    document = profile.get("scoped_profile") or build_global_profile_document(profile)
                    claims = _claims(document)
                    meta = profile.get("qq_profile", {})
                    own_meta = {key: str(meta[key])[:240] if isinstance(meta[key], str) else meta[key]
                                for key in _QQ_META_KEYS if isinstance(meta, dict) and key in meta
                                and isinstance(meta[key], (str, int, float))}
                    if claims or own_meta:
                        context.profiles.append({"claims": claims, "personal_metadata": own_meta,
                            "source_scope": source.metadata("legacy_onebot_user_global")})
            except Exception:
                pass
        service = getattr(runtime, "favorability_service", None)
        if service is not None and bool(getattr(config, "personification_favorability_enabled", True)):
            try:
                relationship = service.peek_user_data(source.user_id) if getattr(service, "enabled", True) else None
                if relationship:
                    score = float(relationship.get("favorability", 0))
                    if math.isfinite(score):
                        context.profiles.append({"relationship": {"score": score, "level": service.get_level_name(score)},
                                                "source_scope": source.metadata("user_global_relationship")})
            except Exception:
                pass
        from .history_config import effective_history_days
        context.history = await read_linked_history(runtime, source, days=effective_history_days(config, private=True)[0] or 0)
        if context.history:
            from .context_budget import ContextBudget, fit_history_to_budget, primary_route_budget
            try:
                logger = getattr(runtime, "logger", None)
                budget = primary_route_budget(config, logger) if logger is not None else ContextBudget.from_route({
                    "context_input_ratio": getattr(config, "personification_context_input_ratio", .8),
                    "context_safety_margin_ratio": getattr(config, "personification_context_safety_margin_ratio", .05)})
                budget = replace(budget, history_tokens=min(2048 // max(1, len(sources)), budget.component_tokens // 4))
                context.history, _ = fit_history_to_budget(context.history, budget=budget)
            except Exception:
                context.history = []
        for reference in await read_linked_experiences(runtime, source):
            if reference.get("kind") == "stable_identity" and reference.get("confirmation") == "confirmed" and _unexpired(reference, require_expiry=True):
                context.profiles.append({"confirmed_identity_evidence": [reference], "source_scope": source.metadata()})
            elif reference.get("kind") == "experience":
                reference["supports_recall"] = True
                context.experience_candidates.append(reference)
        if await source_authorized(runtime, source):
            contexts.append(context)
    return contexts
