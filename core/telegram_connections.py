"""Managed Telegram lifecycle; official adapter remains the protocol implementation.

No adapter import, filesystem access or network activity occurs on module import.
Native bots are observable but never adopted or stopped by this service.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable
from contextlib import contextmanager
from urllib.parse import urlsplit

from .paths import get_data_dir


class ConnectionFault(Exception):
    def __init__(self, code: str, retry_after: float = 0) -> None:
        super().__init__(code)
        self.code, self.retry_after = code, min(300, max(0, retry_after))


def validate_settings(value: dict[str, Any]) -> dict[str, Any]:
    token = str(value.get("token", "")).strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
        raise ConnectionFault("invalid_token")
    api = str(value.get("api_server") or "https://api.telegram.org/").strip().rstrip("/") + "/"
    url = urlsplit(api)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ConnectionFault("invalid_api_server")
    proxy = str(value.get("proxy") or "").strip()
    if proxy and (urlsplit(proxy).scheme not in {"http", "https", "socks5", "socks5h"} or not urlsplit(proxy).hostname):
        raise ConnectionFault("invalid_proxy")
    scope = str(value.get("scope") or "private")
    if scope not in {"private", "all"}:
        raise ConnectionFault("invalid_scope")
    return {"token": token, "api_server": api, "proxy": proxy, "scope": scope,
            "name": str(value.get("name") or "Telegram")[:100],
            "persona": str(value.get("persona") or "")[:12000]}


def native_telegram(bot: Any) -> bool:
    return str(type(bot).__module__).startswith("nonebot.adapters.telegram")


def official_bot(settings: dict[str, Any], driver: Any) -> Any:
    """A small adapter subclass isolates per-connection proxy and lifecycle.

    We deliberately do not call upstream poll/setup: b20 deletes webhook and
    lacks durable offsets. API serialization and typed results stay upstream.
    """
    try:
        from nonebot.adapters.telegram import Adapter, Bot
        from nonebot.adapters.telegram.config import BotConfig
        from nonebot.drivers import HTTPClientMixin, Timeout
    except ImportError:
        raise ConnectionFault("adapter_missing") from None
    if not isinstance(driver, HTTPClientMixin):
        raise ConnectionFault("http_driver_missing")
    from .telegram_media import protect_transport_logs
    protect_transport_logs()

    class ManagedAdapter(Adapter):
        def setup(self) -> None:
            pass

        async def request(self, setup: Any) -> Any:
            setup.proxy = settings.get("proxy") or None
            setup.timeout = Timeout(45)
            try:
                response = await super().request(setup)
            except Exception:
                raise ConnectionFault("network_error") from None
            if response.status_code >= 400:
                retry = 0
                try:
                    retry = float(json.loads(response.content).get("parameters", {}).get("retry_after", 0))
                except (ValueError, TypeError, AttributeError):
                    pass
                code = {401: "invalid_token", 403: "forbidden", 409: "receiver_conflict", 429: "rate_limited"}.get(response.status_code, "api_error")
                # The upstream API wrapper catches request exceptions. Retain
                # the safe typed code outside that wrapper for our caller.
                fault = ConnectionFault(code, retry)
                self._fault.set(fault)
                raise fault
            return response

        async def _call_api(self, bot: Any, api: str, **data: Any) -> Any:
            # Context-local fault avoids cross-request races on the adapter.
            from contextvars import ContextVar
            marker = getattr(self, "_fault", None)
            if marker is None:
                marker = self._fault = ContextVar("telegram_api_fault", default=None)
            reset = marker.set(None)
            try:
                return await super()._call_api(bot, api, **data)
            except Exception as exc:
                fault = marker.get()
                if fault:
                    raise fault from None
                # Upstream chains our safe request exception.
                if isinstance(exc.__cause__, ConnectionFault):
                    raise exc.__cause__ from None
                raise ConnectionFault("api_error") from None
            finally:
                marker.reset(reset)

    adapter = ManagedAdapter(driver)
    adapter.personification_managed = True
    adapter.personification_proxy = settings.get("proxy") or None
    return Bot(adapter, settings["token"].split(":", 1)[0], config=BotConfig(token=settings["token"], api_server=settings["api_server"]))


class TelegramConnections:
    def __init__(self, root: Path, *, get_bots: Callable[[], dict[str, Any]], driver: Callable[[], Any], factory: Callable[..., Any] = official_bot) -> None:
        self.root, self.get_bots, self.driver, self.factory = Path(root), get_bots, driver, factory
        self.records: dict[str, dict[str, Any]] = {}
        self.bots: dict[str, Any] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.checks: dict[str, dict[str, str]] = {}
        self.errors: dict[str, str] = {}
        self.nonces: dict[str, tuple[str, float]] = {}
        self.lock = asyncio.Lock()
        self.stopping: set[str] = set()
        self.poll_requests: dict[str, asyncio.Task] = {}
        self.active_sends: dict[str, set[asyncio.Task]] = {}
        self.stop_events: dict[str, asyncio.Event] = {}
        self.loaded = False
        self.attention: dict[tuple[str, str], Any] = {}

    def load(self) -> None:
        if self.loaded:
            return
        path = self.root / "connections.secret.json"
        if path.exists():
            try:
                self.records = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                raise ConnectionFault("credentials_unreadable") from None
        self.loaded = True

    def _save(self) -> None:
        from .qzone_credentials import _restrict_secret_file_permissions
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.root / (secrets.token_hex(12) + ".secret.tmp")
        try:
            temp.touch(mode=0o600, exist_ok=False)
            _restrict_secret_file_permissions(temp)
            temp.write_text(json.dumps(self.records, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.root / "connections.secret.json")
        finally:
            temp.unlink(missing_ok=True)

    @contextmanager
    def db(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / "updates.sqlite3")
        db.execute("CREATE TABLE IF NOT EXISTS offsets (bot TEXT PRIMARY KEY, value INTEGER NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS seen (bot TEXT, update_id TEXT, created REAL, PRIMARY KEY(bot,update_id))")
        db.execute("CREATE TABLE IF NOT EXISTS aliases (bot TEXT, chat TEXT, canonical TEXT, PRIMARY KEY(bot,chat))")
        db.execute("CREATE TABLE IF NOT EXISTS rooms (bot TEXT, chat TEXT, topic TEXT, payload TEXT, PRIMARY KEY(bot,chat,topic))")
        db.commit()
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, bot: str, update_id: str) -> bool:
        if not update_id:
            return True
        with self.db() as db:
            result = db.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?)", (bot, update_id, time.time()))
            db.execute("DELETE FROM seen WHERE created < ?", (time.time() - 7 * 86400,))
            return bool(result.rowcount)

    def canonical_chat(self, bot: str, chat: str, migrated_to: str = "") -> str:
        with self.db() as db:
            row = db.execute("SELECT canonical FROM aliases WHERE bot=? AND chat=?", (bot, chat)).fetchone()
            canonical = str(row[0]) if row else chat
            if migrated_to:
                db.execute("INSERT OR REPLACE INTO aliases VALUES (?,?,?)", (bot, migrated_to, canonical))
                rows = db.execute("SELECT topic,payload FROM rooms WHERE bot=? AND chat=?", (bot, chat)).fetchall()
                for topic, payload in rows:
                    item = json.loads(payload)
                    item["chat_id"] = migrated_to
                    db.execute("INSERT OR REPLACE INTO rooms VALUES (?,?,?,?)", (bot, migrated_to, topic, json.dumps(item)))
                db.execute("DELETE FROM rooms WHERE bot=? AND chat=?", (bot, chat))
            return canonical

    def room(self, bot: str, chat: str, topic: str, *, scope_key: str = "", title: str = "") -> dict[str, Any]:
        with self.db() as db:
            row = db.execute("SELECT payload FROM rooms WHERE bot=? AND chat=? AND topic=?", (bot, chat, topic)).fetchone()
            if row:
                return json.loads(row[0])
            item = {"chat_id": chat, "topic_id": topic, "title": title[:160], "scope_key": scope_key, "participation": "shadow", "proactive_enabled": False}
            db.execute("INSERT INTO rooms VALUES (?,?,?,?)", (bot, chat, topic, json.dumps(item)))
            return item

    def rooms(self, bot: str) -> list[dict[str, Any]]:
        with self.db() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM rooms WHERE bot=?", (bot,))]

    def update_room(self, bot: str, values: dict[str, Any]) -> dict[str, Any]:
        chat, topic = str(values.get("chat_id", "")), str(values.get("topic_id", ""))
        with self.db() as db:
            row = db.execute("SELECT payload FROM rooms WHERE bot=? AND chat=? AND topic=?", (bot, chat, topic)).fetchone()
            if not row:
                raise ConnectionFault("room_not_observed")
            item = json.loads(row[0])
            mode = values.get("participation", item["participation"])
            if mode not in {"off", "shadow", "on"}:
                raise ConnectionFault("invalid_participation")
            item["participation"] = mode
            if "proactive_enabled" in values:
                if not isinstance(values["proactive_enabled"], bool):
                    raise ConnectionFault("invalid_proactive")
                item["proactive_enabled"] = values["proactive_enabled"]
            db.execute("UPDATE rooms SET payload=? WHERE bot=? AND chat=? AND topic=?", (json.dumps(item), bot, chat, topic))
            return item

    def native(self) -> dict[str, Any]:
        return {str(bot.self_id): bot for bot in self.get_bots().values() if native_telegram(bot) and bot not in self.bots.values()}

    def native_reserved_ids(self) -> set[str]:
        config = getattr(self.driver(), "config", None)
        values = getattr(config, "telegram_bots", []) or []
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except ValueError:
                raise ConnectionFault("native_config_invalid") from None
        return {str(row.get("token", "") if isinstance(row, dict) else getattr(row, "token", "")).split(":", 1)[0] for row in values}

    def status(self, ident: str) -> dict[str, Any]:
        self.load()
        if ident.startswith("native:"):
            bot = self.native().get(ident[7:])
            if bot is None:
                raise ConnectionFault("not_found")
            return {"id": ident, "bot_id": str(bot.self_id), "name": str(getattr(bot, "username", "") or "Telegram"), "source": "native", "platform": "telegram", "state": "running", "status": self.checks.get(str(bot.self_id), {})}
        row = self.records.get(ident)
        if row is None:
            raise ConnectionFault("not_found")
        bot_id = row["token"].split(":", 1)[0]
        nonce = self.nonces.get(ident)
        return {"id": ident, "bot_id": bot_id, "name": row["name"], "source": "managed", "platform": "telegram", "scope": row["scope"], "persona": row["persona"], "state": "running" if ident in self.bots else "error" if ident in self.errors else "stopped", "enabled": row.get("enabled", False), "status": self.checks.get(bot_id, {}), "last_error": self.errors.get(ident, ""), "test_url": f"https://t.me/{row['username']}?start=pf_{nonce[0]}" if nonce and nonce[1] > time.time() and row.get("username") else ""}

    def list(self) -> list[dict[str, Any]]:
        self.load()
        return [self.status(key) for key in self.records] + [self.status("native:" + key) for key in self.native()]

    async def probe(self, values: dict[str, Any]) -> dict[str, Any]:
        settings = validate_settings(values)
        bot = self.factory(settings, self.driver())
        me = await bot.get_me()
        webhook = await bot.get_webhook_info()
        return {"bot_id": str(me.id), "username": str(me.username or ""), "status": {"credentials": "verified", "reception": "unverified", "delivery": "unverified", "permissions": "unverified", "visibility": "unverified"}, "webhook_conflict": bool(webhook.url)}

    async def save(self, values: dict[str, Any], ident: str = "") -> dict[str, Any]:
        async with self.lock:
            self.load()
            if ident and ident not in self.records:
                raise ConnectionFault("native_read_only" if ident.startswith("native:") else "not_found")
            if ident in self.bots:
                raise ConnectionFault("stop_before_edit")
            settings = validate_settings({**self.records.get(ident, {}), **values})
            bot_id = settings["token"].split(":", 1)[0]
            if bot_id in self.native_reserved_ids() or bot_id in self.native() or any(k != ident and v["token"].split(":", 1)[0] == bot_id for k, v in self.records.items()):
                raise ConnectionFault("receiver_conflict")
            probe = await self.probe(settings)
            ident = ident or secrets.token_hex(12)
            settings.update(enabled=False, username=probe["username"])
            self.records[ident] = settings
            self._save()
            self.checks[bot_id] = probe["status"]
            return self.status(ident)

    async def start(self, ident: str, *, migrate_webhook: bool = False) -> dict[str, Any]:
        async with self.lock:
            self.load()
            if ident not in self.records:
                raise ConnectionFault("native_read_only" if ident.startswith("native:") else "not_found")
            if ident in self.bots:
                return self.status(ident)
            row = self.records[ident]
            bot = self.factory(row, self.driver())
            if str(bot.self_id) in self.get_bots() or str(bot.self_id) in self.native_reserved_ids():
                raise ConnectionFault("receiver_conflict")
            me = await bot.get_me()
            if str(me.id) != str(bot.self_id):
                raise ConnectionFault("identity_mismatch")
            webhook = await bot.get_webhook_info()
            if webhook.url:
                if not migrate_webhook:
                    self.errors[ident] = "webhook_conflict"
                    raise ConnectionFault("webhook_conflict")
                await bot.delete_webhook(drop_pending_updates=False)
            bot.username = me.username
            row.update(username=str(me.username or ""), enabled=True)
            self._save()
            bot.adapter.bot_connect(bot)
            self.bots[ident] = bot
            self.stopping.discard(ident)
            self.stop_events[ident] = asyncio.Event()
            self.errors.pop(ident, None)
            self.checks.setdefault(str(bot.self_id), {})["credentials"] = "verified"
            self.nonces[ident] = (secrets.token_urlsafe(18), time.time() + 600)
            self.tasks[ident] = asyncio.create_task(self._poll(ident, bot))
            self._emit(str(bot.self_id), "running")
            return self.status(ident)

    def _emit(self, bot: str, state: str) -> None:
        from .runtime_events import publish_runtime_event
        publish_runtime_event("telegram.connection", payload={"platform": "telegram", "bot_id": bot, "state": state})

    async def _backoff(self, ident: str, delay: float) -> None:
        try:
            await asyncio.wait_for(self.stop_events.setdefault(ident, asyncio.Event()).wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    async def send_guarded(self, bot: Any, operation: Callable[[], Any]) -> Any:
        ident = next((key for key, value in self.bots.items() if value is bot), "")
        if getattr(bot.adapter, "personification_managed", False) and (not ident or ident in self.stopping):
            raise ConnectionFault("connection_stopped")
        task = asyncio.create_task(operation())
        active = self.active_sends.setdefault(str(bot.self_id), set())
        active.add(task)
        task.add_done_callback(active.discard)
        return await asyncio.shield(task)

    async def _poll(self, ident: str, bot: Any) -> None:
        from nonebot.adapters.telegram.event import Event
        with self.db() as db:
            row = db.execute("SELECT value FROM offsets WHERE bot=?", (str(bot.self_id),)).fetchone()
        offset = int(row[0]) if row else None
        failures = 0
        pending: set[asyncio.Task] = set()

        async def dispatch(update: Any) -> None:
            try:
                event = Event.parse_event(update.model_dump(by_alias=True, exclude_none=True))
                await bot.handle_event(event)
            except Exception:
                if self.errors.get(ident) not in {"receiver_conflict", "invalid_token", "forbidden"}:
                    self.errors[ident] = "unsupported_or_failed_update"

        try:
            while ident not in self.stopping:
                try:
                    request = asyncio.create_task(bot.get_updates(offset=offset, timeout=25))
                    self.poll_requests[ident] = request
                    try:
                        updates = await request
                    except asyncio.CancelledError:
                        if ident in self.stopping:
                            break
                        raise
                    finally:
                        self.poll_requests.pop(ident, None)
                    failures = 0
                    self.errors.pop(ident, None)
                    accepted = False
                    for update in updates:
                        if len(pending) >= 100:
                            await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                        if ident in self.stopping:
                            break
                        # Continue receiving while handlers run: albums may span
                        # getUpdates batches. Bound pending work and let the
                        # shared conversation buffer serialize generation.
                        task = asyncio.create_task(dispatch(update))
                        pending.add(task)
                        task.add_done_callback(pending.discard)
                        offset = int(update.update_id) + 1
                        accepted = True
                    if accepted:
                        # At-most-once intake: a crash can miss work, but will
                        # never intentionally replay an unknown side effect.
                        with self.db() as db:
                            db.execute("INSERT OR REPLACE INTO offsets VALUES (?,?)", (str(bot.self_id), offset))
                except ConnectionFault as exc:
                    self.errors[ident] = exc.code
                    if exc.code in {"receiver_conflict", "invalid_token", "forbidden"}:
                        break
                    failures += 1
                    await self._backoff(ident, max(exc.retry_after, min(60, 2 ** min(failures, 6))))
                except Exception:
                    self.errors[ident] = "update_processing_failed"
                    failures += 1
                    await self._backoff(ident, min(60, 2 ** min(failures, 6)))
        finally:
            await asyncio.gather(*pending, return_exceptions=True)
            self.bots.pop(ident, None)
            bot.adapter.bot_disconnect(bot)

    async def stop(self, ident: str, *, persist: bool = True) -> None:
        async with self.lock:
            await self._stop_locked(ident, persist=persist)

    async def _stop_locked(self, ident: str, *, persist: bool = True) -> None:
        self.load()
        if ident not in self.records:
            raise ConnectionFault("native_read_only" if ident.startswith("native:") else "not_found")
        if persist:
            self.records[ident]["enabled"] = False
            self._save()
        task = self.tasks.pop(ident, None)
        if task:
            self.stopping.add(ident)
            self.stop_events.setdefault(ident, asyncio.Event()).set()
            request = self.poll_requests.get(ident)
            if request:
                request.cancel()
            # Never cancel processing or an in-flight send.
            await asyncio.gather(task, return_exceptions=True)
        bot_id = self.records[ident]["token"].split(":", 1)[0]
        await asyncio.gather(*self.active_sends.get(bot_id, set()), return_exceptions=True)
        self.nonces.pop(ident, None)
        self._emit(bot_id, "stopped")

    async def delete(self, ident: str) -> None:
        async with self.lock:
            # Keep stop and removal atomic with respect to queued start/save.
            await self._stop_locked(ident)
            del self.records[ident]
            self._save()

    async def restore(self) -> None:
        self.load()
        for ident, row in list(self.records.items()):
            if row.get("enabled"):
                try:
                    await self.start(ident)
                except Exception as exc:
                    self.errors[ident] = exc.code if isinstance(exc, ConnectionFault) else "restore_failed"

    async def close(self) -> None:
        for ident in list(self.tasks):
            await self.stop(ident, persist=False)

    def settings_for(self, bot_id: str) -> dict[str, Any]:
        self.load()
        return next((row for row in self.records.values() if row["token"].split(":", 1)[0] == bot_id), {"scope": "all", "persona": ""})

    def observed(self, bot_id: str, **checks: str) -> None:
        self.checks.setdefault(bot_id, {}).update(checks)
        self._emit(bot_id, "observed")

    def consume_test(self, bot_id: str, text: str) -> bool:
        for ident, (nonce, expires) in list(self.nonces.items()):
            row = self.records[ident]
            if row["token"].split(":", 1)[0] == bot_id and expires > time.time() and secrets.compare_digest(text.strip(), "/start pf_" + nonce):
                del self.nonces[ident]
                return True
        return False


_SERVICES: dict[str, TelegramConnections] = {}


def get_connections(config: Any) -> TelegramConnections:
    from nonebot import get_bots, get_driver
    path = (get_data_dir(config) / "telegram").resolve()
    key = str(path)
    if key not in _SERVICES:
        _SERVICES[key] = TelegramConnections(path, get_bots=get_bots, driver=get_driver)
    return _SERVICES[key]
