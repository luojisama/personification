"""Two-party Telegram/QQ links, separate from transport and admin identity.

The existing DataStore transaction serializes every state transition. Only a
hash of the one-time credential is persisted; transport commands never replace
event IDs, group scopes, session ownership or administrator identities.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

NAMESPACE = "account_links_v1"
TTL_SECONDS = 600
COOLDOWN_SECONDS = 60
_DIGITS = re.compile(r"[1-9][0-9]{0,19}\Z")
_CODE = re.compile(r"[A-Za-z0-9_-]{22,64}\Z")


class AccountLinkError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def numeric_id(value: Any) -> str:
    text = str(value or "").strip()
    if isinstance(value, bool) or not _DIGITS.fullmatch(text):
        raise AccountLinkError("invalid_identity")
    return text


def telegram_user_scope(bot_id: Any, user_id: Any) -> tuple[str, str]:
    """Same namespace encoding as InteractionEnvelope, without changing events."""
    bot, user = numeric_id(bot_id), numeric_id(user_id)
    namespace = f"p8:telegramb{len(bot)}:{bot}"
    return namespace, f"{namespace}:u{len(user)}:{user}"


@dataclass(frozen=True, slots=True)
class LinkedAccount:
    tg_bot_id: str
    tg_user_id: str
    qq_bot_id: str
    qq_user_id: str
    linked_at: float
    link_id: str

    @property
    def telegram_scope(self) -> tuple[str, str]:
        return telegram_user_scope(self.tg_bot_id, self.tg_user_id)


@dataclass(frozen=True, slots=True)
class LinkChallenge:
    request_id: str
    code: str = field(repr=False)
    expires_at: float
    qq_bot_id: str


class AccountLinkService:
    def __init__(self, store: Any, *, clock: Callable[[], float] = time.time,
                 code_factory: Callable[[], str] = lambda: secrets.token_urlsafe(18)) -> None:
        self.store, self.clock, self.code_factory = store, clock, code_factory

    def _now(self) -> float:
        now = float(self.clock())
        if not math.isfinite(now) or now < 0:
            raise AccountLinkError("clock_invalid")
        return now

    @staticmethod
    def _document(value: Any) -> dict[str, Any]:
        if value == {}:
            return {"version": 1, "links": [], "pending": [], "rates": {}}
        if not isinstance(value, dict) or value.get("version") != 1:
            raise AccountLinkError("store_invalid")
        if not all(isinstance(value.get(key), list) for key in ("links", "pending")) or not isinstance(value.get("rates"), dict):
            raise AccountLinkError("store_invalid")
        if not all(isinstance(item, dict) for key in ("links", "pending") for item in value[key]):
            raise AccountLinkError("store_invalid")
        return value

    @staticmethod
    def _prune(document: dict[str, Any], now: float) -> None:
        document["pending"] = [item for item in document["pending"] if float(item.get("expires_at", 0)) > now]
        document["rates"] = {key: [stamp for stamp in stamps if float(stamp) > now - 3600]
                             for key, stamps in document["rates"].items()
                             if isinstance(stamps, list) and any(float(stamp) > now - 3600 for stamp in stamps)}

    @staticmethod
    def _tg_matches(item: dict, bot: str, user: str) -> bool:
        return item.get("tg_bot_id") == bot and item.get("tg_user_id") == user

    @staticmethod
    def _qq_matches(item: dict, bot: str, user: str) -> bool:
        return item.get("qq_bot_id") == bot and item.get("qq_user_id") == user

    @staticmethod
    def _rate(document: dict, key: str, now: float, *, limit: int, window: int, cooldown: int = 0) -> None:
        stamps = document["rates"].get(key, [])
        recent = [stamp for stamp in stamps if float(stamp) > now - window]
        if len(recent) >= limit or (cooldown and recent and now - float(recent[-1]) < cooldown):
            raise AccountLinkError("rate_limited")
        document["rates"][key] = [*stamps, now]

    def _load(self) -> dict[str, Any]:
        return self._document(self.store.load_sync(NAMESPACE))

    def reserve_command_attempt(self, *, platform: str, bot_id: Any, user_id: Any, action: str) -> None:
        """Limit transport work too, before querying the QQ friend API."""
        bot, user, now = numeric_id(bot_id), numeric_id(user_id), self._now()
        if platform not in {"telegram", "qq"} or action not in {"begin", "confirm"}:
            raise AccountLinkError("invalid_identity")
        def mutate(value: Any) -> dict:
            doc = self._document(value)
            self._prune(doc, now)
            self._rate(doc, f"command:{platform}:{bot}:{user}:{action}", now, limit=5, window=60)
            return doc
        self.store.mutate_sync(NAMESPACE, mutate)

    def resolve_telegram(self, tg_bot_id: Any, tg_user_id: Any) -> LinkedAccount | None:
        bot, user = numeric_id(tg_bot_id), numeric_id(tg_user_id)
        matches = [item for item in self._load()["links"] if self._tg_matches(item, bot, user)]
        if len(matches) > 1:
            raise AccountLinkError("store_invalid")
        return LinkedAccount(**matches[0]) if matches else None

    def links_for_qq(self, qq_bot_id: Any, qq_user_id: Any) -> list[LinkedAccount]:
        bot, user = numeric_id(qq_bot_id), numeric_id(qq_user_id)
        return [LinkedAccount(**item) for item in self._load()["links"] if self._qq_matches(item, bot, user)]

    def pending_telegram(self, tg_bot_id: Any, tg_user_id: Any) -> dict[str, Any] | None:
        bot, user, now = numeric_id(tg_bot_id), numeric_id(tg_user_id), self._now()
        pending = next((item for item in self._load()["pending"]
                        if self._tg_matches(item, bot, user) and float(item.get("expires_at", 0)) > now), None)
        return self._public_pending(pending) if pending else None

    def pending_for_qq(self, qq_bot_id: Any, qq_user_id: Any) -> list[dict[str, Any]]:
        bot, user, now = numeric_id(qq_bot_id), numeric_id(qq_user_id), self._now()
        return [self._public_pending(item) for item in self._load()["pending"]
                if self._qq_matches(item, bot, user) and float(item.get("expires_at", 0)) > now]

    @staticmethod
    def _public_pending(item: dict[str, Any]) -> dict[str, Any]:
        return {key: item.get(key) for key in ("request_id", "tg_bot_id", "tg_user_id", "qq_bot_id", "qq_user_id", "expires_at", "state")}

    def begin(self, *, tg_bot_id: Any, tg_user_id: Any, qq_user_id: Any,
              qq_bot_id: Any = "", issue_code: bool = False) -> LinkChallenge:
        tg_bot, tg_user, qq_user = numeric_id(tg_bot_id), numeric_id(tg_user_id), numeric_id(qq_user_id)
        qq_bot = numeric_id(qq_bot_id) if qq_bot_id else ""
        if issue_code and not qq_bot:
            raise AccountLinkError("qq_bot_required")
        now = self._now()
        code = str(self.code_factory()) if issue_code else ""
        if issue_code and not _CODE.fullmatch(code):
            raise AccountLinkError("random_source_invalid")
        request_id = secrets.token_hex(16)

        def mutate(value: Any) -> dict:
            doc = self._document(value)
            self._prune(doc, now)
            if any(self._tg_matches(item, tg_bot, tg_user) for item in doc["links"]):
                raise AccountLinkError("already_linked")
            if qq_bot and any(item.get("tg_bot_id") == tg_bot and self._qq_matches(item, qq_bot, qq_user) for item in doc["links"]):
                raise AccountLinkError("target_already_linked")
            self._rate(doc, f"begin-tg:{tg_bot}:{tg_user}", now, limit=5, window=3600, cooldown=COOLDOWN_SECONDS)
            self._rate(doc, f"begin-qq:{tg_bot}:{qq_bot}:{qq_user}", now, limit=10, window=3600)
            doc["pending"] = [item for item in doc["pending"] if not self._tg_matches(item, tg_bot, tg_user)]
            doc["pending"].append({"request_id": request_id, "tg_bot_id": tg_bot, "tg_user_id": tg_user,
                                   "qq_bot_id": qq_bot, "qq_user_id": qq_user, "expires_at": now + TTL_SECONDS,
                                   "state": "sending" if code else "awaiting_qq", "code_hash": self._digest(code) if code else ""})
            return doc

        self.store.mutate_sync(NAMESPACE, mutate)
        return LinkChallenge(request_id, code, now + TTL_SECONDS, qq_bot)

    @staticmethod
    def _digest(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    def mark_delivery(self, request_id: str, *, confirmed: bool) -> bool:
        now, found = self._now(), False

        def mutate(value: Any) -> dict:
            nonlocal found
            doc = self._document(value)
            self._prune(doc, now)
            for item in doc["pending"]:
                if item.get("request_id") == request_id and item.get("state") == "sending":
                    found = True
                    item["state"] = "awaiting_confirmation" if confirmed else "delivery_unverified"
                    if not confirmed:
                        item["code_hash"] = ""
            return doc

        self.store.mutate_sync(NAMESPACE, mutate)
        return found

    def confirm(self, *, qq_bot_id: Any, qq_user_id: Any, code: Any) -> LinkedAccount:
        bot, user = numeric_id(qq_bot_id), numeric_id(qq_user_id)
        submitted = str(code or "").strip()
        now, result, error = self._now(), None, "invalid_or_expired_code"

        def mutate(value: Any) -> dict:
            nonlocal result, error
            doc = self._document(value)
            self._prune(doc, now)
            # Persist failed attempts too; raising inside the transaction would
            # roll this counter back. The submitted code never enters storage.
            self._rate(doc, f"verify:{bot}:{user}", now, limit=5, window=60)
            digest = self._digest(submitted) if _CODE.fullmatch(submitted) else ""
            item = next((entry for entry in doc["pending"] if entry.get("state") == "awaiting_confirmation"
                         and self._qq_matches(entry, bot, user) and digest
                         and hmac.compare_digest(str(entry.get("code_hash") or ""), digest)), None)
            if item is None:
                return doc
            tg_bot, tg_user = item["tg_bot_id"], item["tg_user_id"]
            if any(self._tg_matches(entry, tg_bot, tg_user) or
                   (entry.get("tg_bot_id") == tg_bot and self._qq_matches(entry, bot, user)) for entry in doc["links"]):
                error = "already_linked"
                doc["pending"].remove(item)
                return doc
            result = LinkedAccount(tg_bot, tg_user, bot, user, now, secrets.token_hex(16))
            doc["links"].append(asdict(result))
            doc["pending"] = [entry for entry in doc["pending"] if not self._tg_matches(entry, tg_bot, tg_user)
                              and not (entry.get("tg_bot_id") == tg_bot and self._qq_matches(entry, bot, user))]
            return doc

        self.store.mutate_sync(NAMESPACE, mutate)
        if result is None:
            raise AccountLinkError(error)
        return result

    def revoke_telegram(self, tg_bot_id: Any, tg_user_id: Any) -> int:
        bot, user = numeric_id(tg_bot_id), numeric_id(tg_user_id)
        return self._revoke(lambda item: self._tg_matches(item, bot, user))

    def revoke_qq(self, qq_bot_id: Any, qq_user_id: Any) -> int:
        bot, user = numeric_id(qq_bot_id), numeric_id(qq_user_id)
        return self._revoke(lambda item: self._qq_matches(item, bot, user))

    def cancel_pending_telegram(self, tg_bot_id: Any, tg_user_id: Any) -> int:
        bot, user = numeric_id(tg_bot_id), numeric_id(tg_user_id)
        return self._revoke(lambda item: self._tg_matches(item, bot, user), pending_only=True)

    def cancel_pending_qq(self, qq_bot_id: Any, qq_user_id: Any) -> int:
        bot, user = numeric_id(qq_bot_id), numeric_id(qq_user_id)
        return self._revoke(lambda item: self._qq_matches(item, bot, user), pending_only=True)

    def _revoke(self, matches: Callable[[dict], bool], *, pending_only: bool = False) -> int:
        removed = 0
        def mutate(value: Any) -> dict:
            nonlocal removed
            doc = self._document(value)
            for key in (("pending",) if pending_only else ("links", "pending")):
                old = doc[key]
                doc[key] = [item for item in old if not matches(item)]
                removed += len(old) - len(doc[key])
            return doc
        self.store.mutate_sync(NAMESPACE, mutate)
        return removed


def get_account_links() -> AccountLinkService:
    from .data_store import get_data_store
    return AccountLinkService(get_data_store())
