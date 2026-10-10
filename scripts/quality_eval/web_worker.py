"""One candidate per dedicated process; credentials arrive only on stdin."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> None:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    root = Path.cwd()
    os.environ["XDG_DATA_HOME"] = str(root / "data")
    os.environ["XDG_CACHE_HOME"] = str(root / "cache")
    # All runtime output is discarded. Only the explicit result crosses IPC;
    # provider failures cannot accidentally expose URLs or secrets as logs.
    output = sys.stdout
    with open(os.devnull, "w", encoding="utf-8") as sink:
        sys.stdout = sink
        sys.stderr = sink
        try:
            from scripts.quality_eval.runner import _bootstrap_runtime, invoke_case
            _bootstrap_runtime()
            from plugin.personification.core.quality_variant import quality_variant
            from plugin.personification.core.sensitive_data import sanitize_object
            case = payload["case"]
            current = case["events"][-1]
            config = payload["config"]
            with quality_variant(payload["variant"], surface=case.get("surface", "group"), direct=current.get("kind") == "mention"):
                result = asdict(asyncio.run(invoke_case(case, config)))
            result = sanitize_object(result)
        except Exception:
            result = {"status": "failed", "reply": "", "error": "quality_worker_failed"}
        finally:
            sys.stdout = output
        output.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        output.buffer.flush()


if __name__ == "__main__":
    main()
