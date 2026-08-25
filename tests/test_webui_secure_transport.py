from __future__ import annotations

from starlette.requests import Request

from ._loader import load_personification_module


deps = load_personification_module("plugin.personification.webui.deps")


def _request(*, scheme: str = "http", client: str = "158.94.173.121", forwarded: str = "") -> Request:
    headers = [(b"host", b"example.test")]
    if forwarded:
        headers.append((b"x-forwarded-proto", forwarded.encode("ascii")))
    return Request({
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": (client, 12345),
        "server": ("example.test", 80),
    })


def test_secure_transport_accepts_https_and_loopback() -> None:
    assert deps.is_https_or_loopback(_request(scheme="https")) is True
    assert deps.is_https_or_loopback(_request(client="127.0.0.1")) is True


def test_secure_transport_trusts_forwarded_proto_only_from_private_proxy() -> None:
    assert deps.is_https_or_loopback(_request(client="10.0.0.8", forwarded="https")) is True
    assert deps.is_https_or_loopback(_request(client="158.94.173.121", forwarded="https")) is False
    assert deps.is_https_or_loopback(_request(client="158.94.173.121")) is False
