from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable


STEP_UP_ACTIONS = frozenset(
    {
        "export_secret",
        "import_secret",
        "apply_full_restore",
    }
)
STEP_UP_TTL_SECONDS = 300.0
STEP_UP_MAX_ATTEMPTS = 5


class StepUpError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        self.message = str(message)
        super().__init__(f"{self.message}（诊断码：{self.code}）")

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "code": self.code, "message": self.message}


def _digest(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _action(value: Any) -> str:
    action = str(value or "").strip()
    if action not in STEP_UP_ACTIONS:
        raise StepUpError("step_up_action_invalid", "二次验证操作类型无效")
    return action


@dataclass(frozen=True, slots=True)
class StepUpChallenge:
    challenge_id: str
    code: str = field(repr=False)
    action: str
    expires_at: float

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "action": self.action,
            "expires_at": self.expires_at,
        }


@dataclass(slots=True)
class AdminStepUpService:
    clock: Callable[[], float] = time.time
    token_factory: Callable[[int], str] = secrets.token_urlsafe
    code_factory: Callable[[], str] = lambda: f"{secrets.randbelow(1_000_000):06d}"
    ttl_seconds: float = STEP_UP_TTL_SECONDS
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False)
    _challenges: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _tokens: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        ttl = float(self.ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0 or ttl > STEP_UP_TTL_SECONDS:
            raise ValueError("ttl_seconds must be within 0..300")
        self.ttl_seconds = ttl

    def _now(self) -> float:
        value = float(self.clock())
        if not math.isfinite(value) or value < 0:
            raise StepUpError("step_up_clock_invalid", "二次验证服务时钟无效")
        return value

    def _prune_locked(self, now: float) -> None:
        self._challenges = {
            key: value
            for key, value in self._challenges.items()
            if float(value.get("expires_at") or 0) > now
        }
        self._tokens = {
            key: value
            for key, value in self._tokens.items()
            if float(value.get("expires_at") or 0) > now
        }

    @staticmethod
    def _binding(*, admin_qq: Any, device_id: Any, ip: Any, action: Any) -> dict[str, str]:
        qq = str(admin_qq or "").strip()
        device = str(device_id or "").strip()
        if not qq or not device:
            raise StepUpError("step_up_identity_invalid", "二次验证缺少管理员或设备身份")
        return {
            "admin_qq": qq,
            "device_hash": _digest(device),
            "ip_hash": _digest(ip)[:32],
            "action": _action(action),
        }

    def start(
        self,
        *,
        admin_qq: Any,
        device_id: Any,
        ip: Any,
        action: Any,
    ) -> StepUpChallenge:
        binding = self._binding(
            admin_qq=admin_qq,
            device_id=device_id,
            ip=ip,
            action=action,
        )
        now = self._now()
        challenge_id = str(self.token_factory(24) or "").strip()
        code = str(self.code_factory() or "").strip()
        if len(challenge_id) < 24 or not code.isdigit() or len(code) != 6:
            raise StepUpError("step_up_random_source_invalid", "无法生成二次验证参数")
        record = {
            **binding,
            "code_hash": _digest(f"{challenge_id}:{code}"),
            "attempts": 0,
            "expires_at": now + self.ttl_seconds,
        }
        with self._lock:
            self._prune_locked(now)
            self._challenges[_digest(challenge_id)] = record
        return StepUpChallenge(challenge_id, code, binding["action"], now + self.ttl_seconds)

    def verify(
        self,
        *,
        challenge_id: Any,
        code: Any,
        admin_qq: Any,
        device_id: Any,
        ip: Any,
        action: Any,
    ) -> str:
        challenge = str(challenge_id or "").strip()
        submitted_code = str(code or "").strip()
        binding = self._binding(
            admin_qq=admin_qq,
            device_id=device_id,
            ip=ip,
            action=action,
        )
        now = self._now()
        key = _digest(challenge)
        with self._lock:
            self._prune_locked(now)
            record = self._challenges.get(key)
            if not isinstance(record, dict):
                raise StepUpError("step_up_challenge_invalid", "二次验证请求不存在或已过期")
            expected_binding = {name: record.get(name) for name in binding}
            if expected_binding != binding:
                self._challenges.pop(key, None)
                raise StepUpError("step_up_binding_mismatch", "二次验证请求与当前操作环境不匹配")
            expected_code = str(record.get("code_hash") or "")
            actual_code = _digest(f"{challenge}:{submitted_code}")
            if not expected_code or not hmac.compare_digest(expected_code, actual_code):
                record["attempts"] = int(record.get("attempts") or 0) + 1
                if record["attempts"] >= STEP_UP_MAX_ATTEMPTS:
                    self._challenges.pop(key, None)
                else:
                    self._challenges[key] = record
                raise StepUpError("step_up_code_invalid", "二次验证码错误或已失效")
            self._challenges.pop(key, None)
            token = str(self.token_factory(32) or "").strip()
            if len(token) < 32:
                raise StepUpError("step_up_random_source_invalid", "无法签发二次验证令牌")
            self._tokens[_digest(token)] = {
                **binding,
                "expires_at": now + self.ttl_seconds,
            }
        return token

    def consume(
        self,
        token: Any,
        *,
        admin_qq: Any,
        device_id: Any,
        ip: Any,
        action: Any,
    ) -> None:
        raw_token = str(token or "").strip()
        binding = self._binding(
            admin_qq=admin_qq,
            device_id=device_id,
            ip=ip,
            action=action,
        )
        now = self._now()
        key = _digest(raw_token)
        with self._lock:
            self._prune_locked(now)
            record = self._tokens.pop(key, None)
            if not isinstance(record, dict):
                raise StepUpError("step_up_token_invalid", "二次验证令牌不存在、已过期或已使用")
            expected_binding = {name: record.get(name) for name in binding}
            if expected_binding != binding:
                raise StepUpError("step_up_token_binding_mismatch", "二次验证令牌与当前操作环境不匹配")

    def clear(self) -> None:
        with self._lock:
            self._challenges.clear()
            self._tokens.clear()


DEFAULT_ADMIN_STEP_UP_SERVICE = AdminStepUpService()


__all__ = [
    "AdminStepUpService",
    "DEFAULT_ADMIN_STEP_UP_SERVICE",
    "STEP_UP_ACTIONS",
    "STEP_UP_MAX_ATTEMPTS",
    "STEP_UP_TTL_SECONDS",
    "StepUpChallenge",
    "StepUpError",
]
