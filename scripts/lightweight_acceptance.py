"""Repeatable offline runtime contracts; never a live QQ/LLM acceptance claim.

Run with the project's venv Python. Only the explicit local fixture suite below
is collected; plugin registration, dotenv and real providers are not started.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import types
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SUITE = (
    "tests/test_context_only_agent.py",
    "tests/test_context_only_memory.py",
    "tests/test_memory_context_projection.py",
    "tests/test_lightweight_acceptance.py",
    "tests/test_quality_eval_pipeline.py",
    "tests/test_reply_processor_debt_boundaries.py",
    "tests/test_yaml_processor_failure_boundaries.py",
    "tests/test_reply_buffer_failure_boundaries.py",
    "tests/test_generation_fence.py",
    "tests/test_background_learning_ownership.py",
    "tests/test_qq_outbound.py",
    "tests/test_runner_render.py::test_run_agent_max_steps_uses_earlier_usable_result_after_empty_result",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("D:/test_artifacts/personification/2026-10-10-lightweight/simulation"))
    args = parser.parse_args()
    output = args.output.resolve()
    allowed = Path("D:/test_artifacts/personification").resolve()
    if not output.is_relative_to(allowed):
        parser.error("output must be inside D:/test_artifacts/personification")
    output.mkdir(parents=True, exist_ok=True)
    temp = output / "temp"
    temp.mkdir(exist_ok=True)
    # An interrupted run must not leave a previous success report as evidence.
    (output / "report.json").unlink(missing_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()
    evidence_paths = {node.split("::", 1)[0] for node in SUITE} | {
        "agent/runtime/runner.py", "core/memory_context.py", "scripts/lightweight_acceptance.py",
    }
    source_hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                     for path in sorted(evidence_paths)}
    os.environ.update(TMP=str(temp), TEMP=str(temp), TMPDIR=str(temp), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    os.chdir(output)

    blocked = []
    def no_network(*args, **kwargs):
        blocked.append("socket network attempt")
        raise AssertionError("offline acceptance forbids network and DNS")
    # asyncio may create a socketpair for its own wakeup; outbound connection
    # and DNS are blocked instead of blocking construction of every socket.
    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    local = threading.local()
    def guarded_connect(sock, address):
        if getattr(local, "socketpair", False) and address[0] == "127.0.0.1":
            return original_connect(sock, address)
        return no_network(sock, address)
    def internal_socketpair(*args, **kwargs):
        # Windows stdlib emulates socketpair using a synchronous loopback
        # connect. Permit only that call on the current thread, never general
        # loopback requests to production services.
        local.socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            local.socketpair = False
    socket.socketpair = internal_socketpair
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = no_network
    socket.socket.sendto = no_network
    if hasattr(socket.socket, "sendmsg"):
        socket.socket.sendmsg = no_network
    socket.create_connection = no_network
    socket.getaddrinfo = no_network

    def audit(event, args):
        if event in {"subprocess.Popen", "os.system", "os.spawn", "os.posix_spawn"}:
            blocked.append("subprocess creation")
            raise AssertionError("offline acceptance forbids subprocess escape")
        if event == "sqlite3.connect":
            database = os.fsdecode(args[0])
            if database != ":memory:" and not Path(database).resolve().is_relative_to(output):
                blocked.append("database outside isolated output")
                raise AssertionError("offline acceptance forbids non-isolated SQLite")
        if event in {"socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo"}:
            if event == "socket.connect" and getattr(local, "socketpair", False) and args[1][0] == "127.0.0.1":
                return
            no_network()
        if event != "open" or not isinstance(args[0], (str, bytes, os.PathLike)):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        mode, flags = args[1], args[2]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)))
        if writing and not path.is_relative_to(output):
            blocked.append(f"write outside isolated output: {path.name}")
            raise AssertionError("offline acceptance writes only inside isolated output")
        if (path.name.startswith(".env") or path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}) and not path.is_relative_to(output):
            blocked.append(f"protected local data access: {path.name}")
            raise AssertionError("offline acceptance forbids dotenv and databases outside isolated output")
    sys.addaudithook(audit)

    # Reuse the repository's namespace loader: avoid plugin registration and
    # redirect its explicit parent-project dotenv discovery before fixtures
    # create their isolated runtime configurations.
    from tests._loader import load_personification_module
    runtime_config = load_personification_module("plugin.personification.core.runtime_config")
    runtime_config._iter_env_file_candidates = lambda: []
    # Pytest package discovery also imports the checkout by its bare directory
    # name. Give that name the same inert namespace instead of its entrypoint.
    bare = types.ModuleType(ROOT.name)
    bare.__path__ = [str(ROOT)]
    bare.__file__ = str(ROOT / "__init__.py")
    sys.modules[ROOT.name] = bare

    import pytest
    class Isolation:
        def pytest_runtest_setup(self, item):
            # Some legacy test modules replace namespace-loaded modules at
            # collection; bind the guard to that current instance as well.
            current = load_personification_module("plugin.personification.core.runtime_config")
            current._iter_env_file_candidates = lambda: []
    junit = output / "junit.xml"
    junit.unlink(missing_ok=True)
    code = int(pytest.main([*(str(ROOT / node) for node in SUITE), "-q", "-o", "junit_family=xunit1", "-o", f"log_file={output / 'pytest.log'}", "-p", "no:cacheprovider", "--basetemp", str(output / "pytest-temp"), "--junitxml", str(junit)], plugins=[Isolation()]))
    cases = []
    if junit.exists():
        for case in ET.parse(junit).iter("testcase"):
            cases.append({"name": case.get("name"), "class": case.get("classname"), "seconds": float(case.get("time", "0")),
                          "outcome": "failed" if case.find("failure") is not None or case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed",
                          "observations": {p.get("name"): p.get("value") for p in case.findall("properties/property")}})
    if blocked and code == 0:
        code = 1
    report = {"schema": "personification-lightweight-offline/v1", "exit_code": code, "blocked_accesses": blocked,
              "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
              "python_version": sys.version.split()[0], "source_sha256": source_hashes,
              "suite_kind": "focused", "suite": list(SUITE), "cases": cases,
              "scope": "Actual local runtime with isolated fixtures, scripted providers and synthetic delivery receipts.",
              "unverified": ["real LLM quality and latency", "real QQ/OneBot delivery", "production database and server resource usage"],
              "research": {"sources": ["https://docs.pytest.org/en/stable/how-to/monkeypatch.html", "https://docs.python.org/3/library/contextvars.html", "https://nonebug.nonebot.dev/"], "access": "Parent agent read official pytest monkeypatch and contextvars documentation via web connector. NoneBug page unavailable. Existing pytest/MockTransport fixtures reused; no NoneBug dependency added."}}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Offline evidence: {output / 'report.json'}; real LLM/QQ acceptance remains unverified.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
