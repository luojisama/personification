"""Native and managed Telegram events enter the same buffered reply pipeline."""
from __future__ import annotations

import asyncio
import json
from dataclasses import is_dataclass, replace
from typing import Any

from nonebot import on, on_message
from nonebot.adapters import Bot, Event
from nonebot.rule import Rule
from nonebot.typing import T_State

from ..core.interaction_adapter import _attr
from ..core.telegram_connections import get_connections
from ..core.telegram_interaction import TelegramInteractionAdapter
from ..core.telegram_media import materialize
from .satori_reply_bridge import dispatch_satori_reply, _satori_outbound_parts, _pipeline_envelope, _SatoriPipelineEvent


class TelegramBotProxy:
    def __init__(self, bot: Any, event: Any, envelope: Any, adapter: Any) -> None:
        self._bot, self._event, self._envelope, self._adapter = bot, event, envelope, adapter
        self.self_id = envelope.bot_namespace

    async def send(self, event: Any, content: Any, **kwargs: Any) -> Any:
        parts = _satori_outbound_parts(content, allow_media=True)
        if not parts:
            from ..core.telegram_connections import ConnectionFault
            raise ConnectionFault("unsupported_content")
        return await self._send_parts(parts)

    async def _personification_send_audio_file(self, event: Any, path: Any) -> Any:
        from pathlib import Path
        from ..core.telegram_connections import ConnectionFault
        service = getattr(self._bot.adapter, "personification_connections", None)
        if service is None:
            raise ConnectionFault("media_service_unavailable")
        root = (service.root.parent / "tts_cache").resolve()
        file = Path(path)
        if file.is_symlink() or not file.resolve().is_relative_to(root) or file.stat().st_size > 20 * 1024 * 1024:
            raise ConnectionFault("media_path_forbidden")
        return await self._send_parts([{"type": "audio", "data": file.read_bytes()}])

    async def _send_parts(self, parts: list[dict[str, Any]]) -> Any:
        from ..core.telegram_actions import send_parts, _plan_parts
        async def operation():
            return await send_parts(self._bot, str(_attr(_attr(self._event, "chat", {}), "id", "")), parts,
                topic_id=str(_attr(self._event, "message_thread_id", "") or ""),
                reply_to_message_id=str(_attr(self._event, "message_id", "")))
        service = getattr(self._bot.adapter, "personification_connections", None)
        result = await service.send_guarded(self._bot, operation) if service else await operation()
        if result.state == "sent":
            service = getattr(self._bot.adapter, "personification_connections", None)
            if service:
                service.observed(str(self._bot.self_id), delivery="verified")
            message_id = result.message_ids[-1]
            return {"message_id": f"{self._envelope.conversation_id}:m{len(message_id)}:{message_id}"}
        if result.partial:
            # The outer pipeline only sees an unknown aggregate. Preserve each
            # confirmed subpart now, without marking the unconfirmed tail sent.
            from ..core.session_store import append_session_message, build_group_session_id, build_private_session_id
            from ..utils import record_group_msg
            group_id = self._envelope.conversation_id if self._envelope.conversation_kind != "private" else ""
            session_id = build_group_session_id(group_id) if group_id else build_private_session_id(self._envelope.sender_id)
            for (method, payload), receipt in zip(_plan_parts(parts), result.receipts):
                if receipt.state != "sent":
                    break
                text = payload.get("text") if method == "send_message" else {"send_photo": "[图片]", "send_voice": "[语音]", "send_video": "[视频]", "send_document": "[文件]", "send_sticker": "[贴纸]", "send_animation": "[动画]"}.get(method, "[媒体]")
                message_id = f"{self._envelope.conversation_id}:m{len(receipt.message_id)}:{receipt.message_id}"
                append_session_message(session_id, "assistant", text, user_id=self.self_id, message_id=message_id,
                    bot_id=self.self_id, platform="telegram", source_kind="bot_reply", sender_role="bot", delivery_status="confirmed")
                if group_id:
                    record_group_msg(group_id, self.self_id, text, is_bot=True, user_id=self.self_id, message_id=message_id,
                        bot_id=self.self_id, platform="telegram", source_kind="bot_reply", sender_role="bot")
            from ..core.reply_turn_trace import record_stage
            record_stage(key="telegram_partial_delivery", label="Telegram 部分送达", status="warn",
                detail=f"confirmed_parts={len(result.message_ids)} unconfirmed_tail=true")
        from ..core.telegram_connections import ConnectionFault
        raise ConnectionFault(result.code)


def _directed(bot: Any, event: Any) -> bool:
    if str(_attr(_attr(event, "chat", {}), "type", "")) == "private":
        return True
    reply = _attr(event, "reply_to_message", {})
    if str(_attr(_attr(reply, "from_", {}), "id", "")) == str(bot.self_id):
        return True
    for segment in list(_attr(event, "original_message", ()) or ()):
        data = _attr(segment, "data", {})
        if _attr(segment, "type", "") == "mention" and str(_attr(data, "text", "")).lower() == "@" + str(getattr(bot, "username", "")).lower():
            return True
        if _attr(segment, "type", "") == "text_mention" and str(_attr(_attr(data, "user", {}), "id", "")) == str(bot.self_id):
            return True
    # Entity offsets use UTF-16 code units, not Python character offsets.
    text = str(_attr(event, "text", "") or _attr(event, "caption", "") or "")
    encoded = text.encode("utf-16-le")
    for entity in list(_attr(event, "entities", ()) or ()) + list(_attr(event, "caption_entities", ()) or ()):
        kind = _attr(entity, "type", "")
        if kind == "text_mention" and str(_attr(_attr(entity, "user", {}), "id", "")) == str(bot.self_id):
            return True
        offset, length = int(_attr(entity, "offset", 0)), int(_attr(entity, "length", 0))
        if kind == "mention" and encoded[offset * 2:(offset + length) * 2].decode("utf-16-le", errors="replace").lower() == "@" + str(getattr(bot, "username", "")).lower():
            return True
    return False


_ALBUMS: dict[tuple[str, str, str, str], list[tuple[Any, Any]]] = {}


async def _archive_source(event: Any, envelope: Any, *, deps: Any, direct: bool, album_member: bool = False) -> bool:
    projected = _pipeline_envelope(envelope)
    gate_event = _SatoriPipelineEvent(source=event, envelope=projected, message=[])
    gate = getattr(deps.runtime, "user_policy_gate", None)
    if gate is not None and not await gate.allows_current(gate_event):
        return False
    from ..core import db as db_module
    if projected.conversation_kind != "private":
        if envelope.revision:
            with db_module.connect_sync() as conn:
                conn.execute("UPDATE group_messages SET content=? WHERE group_id=? AND platform=? AND bot_id=? AND message_id=? AND user_id=?", (envelope.text, projected.conversation_id, "telegram", projected.bot_namespace, projected.message_id, projected.sender_id))
        else:
            from ..utils import record_group_msg
            record_group_msg(projected.conversation_id, projected.sender_id, envelope.text or "[媒体消息]", user_id=projected.sender_id,
                platform="telegram", bot_id=projected.bot_namespace, message_id=projected.message_id, source_kind="user",
                reply_to_msg_id=projected.reply_to_message_id, is_at_bot=direct)
    if envelope.revision:
        from ..core.session_store import build_group_session_id, build_private_session_id
        session_id = build_private_session_id(projected.sender_id) if projected.conversation_kind == "private" else build_group_session_id(projected.conversation_id)
        with db_module.connect_sync() as conn:
            conn.execute("UPDATE session_messages SET content=? WHERE session_id=? AND role='user' AND json_extract(metadata,'$.platform')='telegram' AND json_extract(metadata,'$.bot_id')=? AND json_extract(metadata,'$.message_id')=? AND json_extract(metadata,'$.user_id')=?", (json.dumps(envelope.text, ensure_ascii=False), session_id, projected.bot_namespace, projected.message_id, projected.sender_id))
    elif album_member:
        from ..core.session_store import append_session_message, build_private_session_id, build_group_session_id
        session_id = build_private_session_id(projected.sender_id) if projected.conversation_kind == "private" else build_group_session_id(projected.conversation_id)
        append_session_message(session_id, "user", envelope.text or "[媒体消息]", user_id=projected.sender_id,
            platform="telegram", bot_id=projected.bot_namespace, message_id=projected.message_id, source_kind="user")
    return True


def _migration(event: Any) -> tuple[str, str]:
    model = _attr(_attr(event, "telegram_model", {}), "message", None)
    source = model or event
    chat = str(_attr(_attr(source, "chat", {}), "id", "") or "")
    old = str(_attr(source, "migrate_from_chat_id", "") or "")
    new = str(_attr(source, "migrate_to_chat_id", "") or "")
    return (old, chat) if old else (chat, new) if new else ("", "")


async def dispatch_telegram_migration(bot: Bot, event: Event, *, plugin_config: Any) -> None:
    old, new = _migration(event)
    if not old or not new:
        return
    service = get_connections(plugin_config)
    update_id = str(_attr(_attr(event, "telegram_model", {}), "update_id", "") or "")
    if service.claim(str(bot.self_id), update_id):
        service.canonical_chat(str(bot.self_id), old, new)


async def dispatch_telegram_reply(bot: Bot, event: Event, state: T_State, **kwargs: Any) -> None:
    service = get_connections(kwargs["plugin_config"])
    bot.adapter.personification_connections = service
    adapter = TelegramInteractionAdapter()
    envelope = adapter.normalize(event, bot_id=str(bot.self_id))
    if not envelope.sender_id or not envelope.conversation_id or envelope.sender_id == str(bot.self_id):
        return
    if str(_attr(_attr(event, "chat", {}), "type", "")) == "channel":
        return  # no TG channel/space feature in this release
    if not bool(getattr(kwargs["plugin_config"], "personification_global_enabled", True)):
        return
    if not service.claim(str(bot.self_id), envelope.update_id):
        return
    service.observed(str(bot.self_id), reception="verified")
    migrated_to = str(_attr(event, "migrate_to_chat_id", "") or "")
    migrated_from = str(_attr(event, "migrate_from_chat_id", "") or "")
    if migrated_from:
        service.canonical_chat(str(bot.self_id), migrated_from, envelope.conversation_id)
    canonical = service.canonical_chat(str(bot.self_id), envelope.conversation_id, migrated_to)
    envelope = replace(envelope, conversation_id=canonical)
    if migrated_to or migrated_from:
        return
    settings = service.settings_for(str(bot.self_id))
    if envelope.conversation_kind != "private" and settings["scope"] != "all":
        return
    direct = _directed(bot, event)
    room = service.room(str(bot.self_id), str(event.chat.id), envelope.topic_id, scope_key=envelope.conversation_namespace, title=str(_attr(event.chat, "title", "") or "")) if envelope.conversation_kind != "private" else None
    # Do not trust nickname substring matches in upstream _check_tome.
    object.__setattr__(event, "to_me", direct)
    if envelope.conversation_kind != "private" and not direct:
        service.observed(str(bot.self_id), visibility="verified")
    if envelope.conversation_kind == "private" and service.consume_test(str(bot.self_id), envelope.text):
        receipt = await adapter.send(bot, event, envelope, [{"type": "text", "text": "连接验证成功。可以开始聊天了。"}])
        service.observed(str(bot.self_id), delivery="verified" if receipt.state == "sent" else "unverified")
        return
    source_envelope = envelope
    album = []
    album_id = str(_attr(event, "media_group_id", "") or "")
    album_key = (str(bot.self_id), envelope.conversation_id, envelope.topic_id, album_id)
    if not await _archive_source(event, envelope, deps=kwargs["reply_processor_deps"], direct=direct,
            album_member=bool(album_id and album_key in _ALBUMS)):
        return
    if envelope.revision:
        return  # Editing updates archives only, with no downloads/model/send work.
    if album_id:
        key = album_key
        if key in _ALBUMS:
            _ALBUMS[key].append((event, envelope))
            return
        _ALBUMS[key] = [(event, envelope)]
        try:
            await asyncio.sleep(0.8)
            album = _ALBUMS.pop(key)
            envelope = replace(envelope, text="\n".join(e.text for _, e in album if e.text), ordered_media=tuple(m for _, e in album for m in e.ordered_media))
            direct = any(_directed(bot, ev) for ev, _ in album)
            object.__setattr__(event, "to_me", direct)
        finally:
            _ALBUMS.pop(key, None)
    envelope = await materialize(bot, envelope, service.root / "media") if envelope.ordered_media else envelope
    original_rule = kwargs.pop("personification_rule")

    async def admitted(projected: Any, projected_state: dict[str, Any]) -> bool:
        # Admission scope is server-owned, not supplied by incoming messages.
        projected_state["telegram_scope_admitted"] = True
        if envelope.revision:
            return False  # update archive only; edited messages never auto-repeat actions
        if envelope.conversation_kind != "private" and not direct:
            from ..core.group_mute import is_group_muted
            if is_group_muted(projected.group_id):
                return False
            blacklist = getattr(kwargs["reply_processor_deps"].runtime, "user_blacklist", {}) or {}
            import time
            if float(blacklist.get(projected.user_id, 0) or 0) > time.time():
                return False
            gate = getattr(kwargs["reply_processor_deps"].runtime, "user_policy_gate", None)
            if gate is not None and not await gate.allows_current(projected):
                return False
            if room and room["participation"] != "off":
                from ..core.attention_service import AttentionParticipationService
                from ..core.runtime_events import publish_runtime_event
                from ..utils import get_recent_group_msgs
                key = (str(bot.self_id), room["participation"])
                if key not in service.attention:
                    service.attention[key] = AttentionParticipationService(call_ai_api=getattr(kwargs["reply_processor_deps"].runtime, "call_ai_api", None), logger=kwargs["logger"], mode=room["participation"])
                recent = get_recent_group_msgs(projected.group_id, limit=8, bot_id=projected.self_id, platform="telegram")
                if gate is not None:
                    recent, _ = await gate.filter_context_messages(recent, bot_self_id=projected.self_id)
                evaluation = await service.attention[key].evaluate(session_key=projected.group_id, user_text=projected.get_plaintext(), legacy_should_reply=False, is_private=False, is_at_bot=False, is_reply_to_bot=False, is_continuation=False, recent_context=recent)
                projected_state["telegram_attention"] = evaluation.to_metrics()
                publish_runtime_event("telegram.attention", payload={"bot_id": str(bot.self_id), "scope_key": projected.group_id, **evaluation.to_metrics()})
                if evaluation.actual_should_reply:
                    projected_state.update(attention_admitted=True, is_random_chat=True)
                    return True
            return False
        return await original_rule(projected, projected_state)

    # Legacy private SessionDeps omits protocol message_id. Bind it here so a
    # later Telegram edit can update the exact original without guessing text.
    deps = kwargs["reply_processor_deps"]
    session = getattr(deps, "session", None)
    if is_dataclass(deps) and is_dataclass(session):
        original_append = session.append_session_message
        original_get = session.get_session_messages
        projected_identity = _pipeline_envelope(source_envelope)
        album_ids = {_pipeline_envelope(item).message_id for _, item in album}
        current_content = None
        def append_with_identity(session_id: str, role: str, content: Any, **metadata: Any):
            nonlocal current_content
            if role == "user":
                if album:
                    current_content = content
                    content = source_envelope.text or "[媒体消息]"
                metadata.setdefault("message_id", projected_identity.message_id)
                metadata.setdefault("user_id", projected_identity.sender_id)
            metadata.setdefault("platform", "telegram")
            metadata.setdefault("bot_id", projected_identity.bot_namespace)
            return original_append(session_id, role, content, **metadata)
        def get_for_current_turn(*args: Any, **query: Any):
            rows = original_get(*args, **query)
            if not album or current_content is None:
                return rows
            # Merge only this turn's model view. Durable rows remain editable
            # source messages, so an edited caption cannot survive in a copy.
            result = []
            for row in rows:
                if row.get("role") == "user" and row.get("message_id") in album_ids:
                    if row.get("message_id") == projected_identity.message_id:
                        result.append({**row, "content": current_content})
                else:
                    result.append(row)
            return result
        kwargs["reply_processor_deps"] = replace(deps, session=replace(session, append_session_message=append_with_identity, get_session_messages=get_for_current_turn))
    if settings.get("persona"):
        deps = kwargs["reply_processor_deps"]
        kwargs["reply_processor_deps"] = replace(deps, persona=replace(deps.persona, load_prompt=lambda _scope: settings["persona"]))
    await dispatch_satori_reply(bot, event, state, personification_rule=admitted, interaction_adapter=adapter, source_envelope=envelope, proxy_factory=TelegramBotProxy, **kwargs)


def register_telegram_reply_bridge(**kwargs: Any) -> dict[str, Any]:
    try:
        from nonebot.adapters.telegram.event import EditedMessageEvent, MessageEvent, NoticeEvent
    except ImportError:
        return {}
    from ..core.telegram_media import protect_transport_logs
    protect_transport_logs()

    async def is_telegram(event: Event) -> bool:
        return isinstance(event, (MessageEvent, EditedMessageEvent))

    matcher = on_message(rule=Rule(is_telegram), priority=100, block=False)

    @matcher.handle()
    async def handle(bot: Bot, event: Event, state: T_State) -> None:
        await dispatch_telegram_reply(bot, event, state, **kwargs)

    edited = on(type="edit_message", rule=Rule(is_telegram), priority=100, block=False)
    edited.handle()(handle)
    async def is_migration(event: Event) -> bool:
        return isinstance(event, NoticeEvent) and all(_migration(event))

    migration = on(type="notice", rule=Rule(is_migration), priority=100, block=False)

    @migration.handle()
    async def migrate(bot: Bot, event: Event) -> None:
        await dispatch_telegram_migration(bot, event, plugin_config=kwargs["plugin_config"])

    return {"telegram_reply_matcher": matcher, "telegram_edit_matcher": edited, "telegram_migration_matcher": migration}
