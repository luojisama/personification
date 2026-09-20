"""Bounded, provenance-preserving replacement of a pre-send reply turn.

This module deliberately owns no global task or message storage.  The reply
buffer owns FIFO order and the generation fence owns the send boundary; this
controller only decides whether a newly received *local* message can join the
currently running generation.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Literal


SupplementRelation = Literal["related", "unrelated", "uncertain"]


@dataclass(frozen=True)
class SupplementSettings:
    enabled: bool = True
    window_seconds: float = 30.0
    quiet_seconds: float = 1.0
    max_quiet_seconds: float = 3.0
    group_batch_seconds: float = 0.5
    relation_timeout_seconds: float = 2.0

    def normalized(self) -> "SupplementSettings":
        return SupplementSettings(
            enabled=bool(self.enabled),
            window_seconds=min(120.0, max(1.0, float(self.window_seconds))),
            quiet_seconds=min(3.0, max(0.0, float(self.quiet_seconds))),
            max_quiet_seconds=min(10.0, max(0.0, float(self.max_quiet_seconds))),
            group_batch_seconds=min(2.0, max(0.0, float(self.group_batch_seconds))),
            relation_timeout_seconds=min(10.0, max(0.1, float(self.relation_timeout_seconds))),
        )


@dataclass(frozen=True)
class SupplementDecision:
    status: Literal["related", "unrelated", "uncertain", "expired", "unsafe", "disabled"]
    accepted_ids: tuple[str, ...] = ()
    reason: str = ""


RelationJudge = Callable[[list[dict[str, Any]], list[dict[str, Any]]], Awaitable[Any] | Any]


def _consume_detached_judge(entry: dict[str, Any], task: asyncio.Task[Any]) -> None:
    """Keep a cancellation-ignoring judge observable without blocking a turn."""
    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        pass
    finally:
        tasks = entry.get("supplement_detached_judge_tasks")
        if isinstance(tasks, set):
            tasks.discard(task)


def message_identity(item: dict[str, Any]) -> str:
    """Return only a locally supplied identifier; never derive one from text."""
    return str(item.get("message_id") or item.get("dedupe_key") or "").strip()


def _relation(value: Any) -> SupplementRelation:
    value = str(value or "").strip().lower()
    return value if value in {"related", "unrelated", "uncertain"} else "uncertain"


def _parse_judgement(raw: Any, candidate_ids: set[str]) -> dict[str, SupplementRelation]:
    """Fail closed and accept only candidate IDs that the host supplied."""
    result = {value: "uncertain" for value in candidate_ids}
    values = raw.get("results") if isinstance(raw, dict) and isinstance(raw.get("results"), list) else raw
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, dict):
            continue
        identifier = str(value.get("message_id") or value.get("id") or "").strip()
        if identifier in candidate_ids:
            result[identifier] = _relation(value.get("relation"))
    return result


class SupplementController:
    """State-machine helper stored under ``entry['supplement']``.

    Callers supply the original and candidate event projections.  The returned
    decision contains IDs only, so it can be safely put into diagnostics.
    """

    def __init__(self, settings: SupplementSettings | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = (settings or SupplementSettings()).normalized()
        self._clock = clock

    def begin(self, entry: dict[str, Any], *, generation: int, originals: Iterable[dict[str, Any]], now: float | None = None) -> dict[str, Any]:
        now = self._clock() if now is None else float(now)
        state = entry.get("supplement") if isinstance(entry.get("supplement"), dict) else {}
        # A replacement retains the first trigger's fixed window.  Only its
        # ownership token changes; otherwise a rapid sequence could extend a
        # nominal 30-second window indefinitely.
        if float(state.get("deadline", 0.0) or 0.0) > now:
            state["generation"] = int(generation)
            entry["supplement"] = state
            return state
        state.update({
            "generation": int(generation),
            "started_at": now,
            "deadline": now + self.settings.window_seconds,
            "quiet_until": now,
            "first_related_at": 0.0,
            "restart_deadline": 0.0,
            "original_ids": tuple(filter(None, (message_identity(item) for item in originals))),
            "accepted_ids": (),
        })
        entry["supplement"] = state
        return state

    def can_replace(self, entry: dict[str, Any], *, generation: int, safe_to_supersede: bool, now: float | None = None) -> SupplementDecision:
        if not self.settings.enabled:
            return SupplementDecision("disabled")
        state = entry.get("supplement") if isinstance(entry.get("supplement"), dict) else {}
        now = self._clock() if now is None else float(now)
        if int(state.get("generation", -1)) != int(generation):
            return SupplementDecision("expired", reason="generation_mismatch")
        if now > float(state.get("deadline", 0.0) or 0.0):
            return SupplementDecision("expired", reason="window_expired")
        if not safe_to_supersede:
            return SupplementDecision("unsafe", reason="delivery_or_action_started")
        return SupplementDecision("related")

    def replacement_pending(self, entry: dict[str, Any], *, generation: int, now: float | None = None) -> bool:
        """The old task is already fenced; more related arrivals may merge."""
        state = entry.get("supplement") if isinstance(entry.get("supplement"), dict) else {}
        now = self._clock() if now is None else float(now)
        return (
            int(entry.get("supplement_replacement_generation", -1) or -1) == int(generation)
            and int(entry.get("current_generation", generation) or generation) == int(generation)
            and not bool(entry.get("replacement_generation_started", False))
            and now <= float(state.get("deadline", 0.0) or 0.0)
        )

    def accept_private(self, entry: dict[str, Any], *, generation: int, candidate: dict[str, Any], safe_to_supersede: bool, now: float | None = None) -> SupplementDecision:
        verdict = self.can_replace(entry, generation=generation, safe_to_supersede=safe_to_supersede, now=now)
        identifier = message_identity(candidate)
        if verdict.status != "related" and not self.replacement_pending(entry, generation=generation, now=now):
            return verdict
        if not identifier:
            return verdict if verdict.status != "related" else SupplementDecision("uncertain", reason="missing_message_id")
        return self._accept(entry, (identifier,), now=now)

    async def judge_group(
        self,
        entry: dict[str, Any],
        *,
        generation: int,
        originals: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        safe_to_supersede: bool,
        judge: RelationJudge | None,
        now: float | None = None,
    ) -> SupplementDecision:
        verdict = self.can_replace(entry, generation=generation, safe_to_supersede=safe_to_supersede, now=now)
        if verdict.status != "related" and not self.replacement_pending(entry, generation=generation, now=now):
            return verdict
        # Preserve ingress order.  A set here made replay order depend on hash
        # randomization and could make two related messages swap positions.
        ids = tuple(dict.fromkeys(message_identity(item) for item in candidates if message_identity(item)))
        candidate_ids = set(ids)
        if not ids or judge is None:
            return SupplementDecision("uncertain", reason="relation_judge_unavailable")
        task: asyncio.Task[Any] | None = None
        try:
            response = judge(list(originals), list(candidates))
            if inspect.isawaitable(response):
                # ``wait_for`` waits for a coroutine that swallows its
                # cancellation.  The group ingress must instead have a hard
                # local deadline: detach such a task, consume its outcome,
                # and leave these messages in the normal FIFO lane.
                task = asyncio.ensure_future(response)
                done, _pending = await asyncio.wait(
                    {task}, timeout=self.settings.relation_timeout_seconds,
                )
                if task not in done:
                    self._detach_judge(entry, task)
                    task.cancel()
                    return SupplementDecision("uncertain", reason="relation_judge_timeout")
                response = task.result()
        except asyncio.CancelledError:
            # Cancelling the ingress task must not leave an untracked provider
            # coroutine behind.  It may ignore cancellation, so retain it in
            # the detached set until its eventual completion.
            if task is not None and not task.done():
                self._detach_judge(entry, task)
                task.cancel()
            raise
        except asyncio.TimeoutError:
            return SupplementDecision("uncertain", reason="relation_judge_timeout")
        except Exception:
            return SupplementDecision("uncertain", reason="relation_judge_failed")
        # The model may have returned after the user sent another message or
        # after a send/tool boundary began.  Never accept its stale answer.
        fresh = self.can_replace(entry, generation=generation, safe_to_supersede=safe_to_supersede)
        if fresh.status != "related" and not self.replacement_pending(entry, generation=generation):
            return fresh
        relations = _parse_judgement(response, candidate_ids)
        accepted = tuple(identifier for identifier in ids if relations.get(identifier) == "related")
        if not accepted:
            statuses = set(relations.values())
            return SupplementDecision("unrelated" if statuses == {"unrelated"} else "uncertain", reason="relation_not_related")
        return self._accept(entry, accepted, now=now)

    @staticmethod
    def _detach_judge(entry: dict[str, Any], task: asyncio.Task[Any]) -> None:
        entry.setdefault("supplement_detached_judge_tasks", set()).add(task)
        task.add_done_callback(lambda finished: _consume_detached_judge(entry, finished))

    def _accept(self, entry: dict[str, Any], identifiers: tuple[str, ...], *, now: float | None) -> SupplementDecision:
        now = self._clock() if now is None else float(now)
        state = entry.get("supplement") if isinstance(entry.get("supplement"), dict) else {}
        accepted = tuple(dict.fromkeys((*tuple(state.get("accepted_ids") or ()), *identifiers)))
        state["accepted_ids"] = accepted
        first_related = float(state.get("first_related_at", 0.0) or 0.0)
        if first_related <= 0:
            first_related = now
            state["first_related_at"] = first_related
            state["restart_deadline"] = first_related + self.settings.max_quiet_seconds
        # A burst gets one second from the last accepted message, but never
        # moves the fixed three-second restart cap.
        state["quiet_until"] = min(
            float(state.get("deadline", now) or now),
            float(state.get("restart_deadline", now) or now),
            now + self.settings.quiet_seconds,
        )
        entry["supplement"] = state
        return SupplementDecision("related", identifiers, reason="accepted")
