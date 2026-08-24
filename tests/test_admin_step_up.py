from __future__ import annotations

import pytest

from personification.core.admin_step_up import AdminStepUpService, StepUpError


class _Tokens:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self, _length: int) -> str:
        self.index += 1
        return f"token-{self.index}-" + "x" * 40


def _service(clock: list[float]) -> AdminStepUpService:
    return AdminStepUpService(
        clock=lambda: clock[0],
        token_factory=_Tokens(),
        code_factory=lambda: "123456",
    )


def test_token_is_bound_to_admin_device_ip_and_action_and_single_use() -> None:
    clock = [100.0]
    service = _service(clock)
    challenge = service.start(
        admin_qq="1",
        device_id="device",
        ip="127.0.0.1",
        action="export_secret",
    )
    token = service.verify(
        challenge_id=challenge.challenge_id,
        code="123456",
        admin_qq="1",
        device_id="device",
        ip="127.0.0.1",
        action="export_secret",
    )

    service.consume(
        token,
        admin_qq="1",
        device_id="device",
        ip="127.0.0.1",
        action="export_secret",
    )
    with pytest.raises(StepUpError, match="已使用"):
        service.consume(
            token,
            admin_qq="1",
            device_id="device",
            ip="127.0.0.1",
            action="export_secret",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("admin_qq", "2"),
        ("device_id", "other-device"),
        ("ip", "127.0.0.2"),
        ("action", "import_secret"),
    ],
)
def test_challenge_binding_mismatch_fails_closed(field: str, value: str) -> None:
    service = _service([100.0])
    challenge = service.start(
        admin_qq="1", device_id="device", ip="127.0.0.1", action="export_secret"
    )
    arguments = {
        "challenge_id": challenge.challenge_id,
        "code": "123456",
        "admin_qq": "1",
        "device_id": "device",
        "ip": "127.0.0.1",
        "action": "export_secret",
    }
    arguments[field] = value

    with pytest.raises(StepUpError, match="不匹配"):
        service.verify(**arguments)


def test_expired_challenge_and_token_are_rejected() -> None:
    clock = [100.0]
    service = _service(clock)
    challenge = service.start(
        admin_qq="1", device_id="device", ip="127.0.0.1", action="apply_full_restore"
    )
    clock[0] = 401.0
    with pytest.raises(StepUpError, match="已过期"):
        service.verify(
            challenge_id=challenge.challenge_id,
            code="123456",
            admin_qq="1",
            device_id="device",
            ip="127.0.0.1",
            action="apply_full_restore",
        )


def test_five_bad_codes_destroy_challenge_without_storing_plain_code() -> None:
    service = _service([100.0])
    challenge = service.start(
        admin_qq="1", device_id="device", ip="127.0.0.1", action="import_secret"
    )
    for _ in range(5):
        with pytest.raises(StepUpError):
            service.verify(
                challenge_id=challenge.challenge_id,
                code="000000",
                admin_qq="1",
                device_id="device",
                ip="127.0.0.1",
                action="import_secret",
            )

    assert "123456" not in repr(service._challenges)
    assert not service._challenges


def test_invalid_action_is_rejected() -> None:
    with pytest.raises(StepUpError, match="操作类型无效"):
        _service([100.0]).start(
            admin_qq="1", device_id="device", ip="127.0.0.1", action="publish_qzone"
        )
