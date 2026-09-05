from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "script",
    [
        "import importlib; importlib.import_module('plugin.personification.core.response_review')",
        "import importlib; importlib.import_module('plugin.personification.core.ai_routes')",
        "import importlib; importlib.import_module('plugin.personification.agent.runtime.planner')",
        (
            "from plugin.personification.agent.runtime import AgentResult, run_agent; "
            "from plugin.personification.agent.runtime import runner; "
            "assert AgentResult is runner.AgentResult; assert run_agent is runner.run_agent"
        ),
    ],
    ids=["response-review", "ai-routes", "planner", "runtime-public-api"],
)
def test_runtime_modules_import_in_fresh_initialized_subprocess(script: str, tmp_path) -> None:
    """Exercise production imports without the in-process test namespace loader."""

    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPYCACHEPREFIX"] = str(tmp_path / "pycache")
    completed = subprocess.run(
        [sys.executable, "-c", f"import nonebot; nonebot.init(); {script}"],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr[-4000:]
