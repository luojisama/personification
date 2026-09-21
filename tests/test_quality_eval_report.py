from __future__ import annotations

import hashlib
import json

from scripts.quality_eval.run import project_behavior_for_report


def test_behavior_report_projection_hashes_prompt_bodies_without_changing_execution_value() -> None:
    secret_fixture = "fixture secret must not enter report"
    behavior = {
        "personification_system_prompt": secret_fixture,
        "personification_core_values_prompt": "core value text",
        "personification_response_timeout": 301,
    }

    projected = project_behavior_for_report(behavior, {
        "personification_system_prompt": "env.json",
        "personification_core_values_prompt": "defaults",
    })

    assert behavior["personification_system_prompt"] == secret_fixture
    assert secret_fixture not in json.dumps(projected, ensure_ascii=False)
    assert projected["personification_system_prompt"] == {
        "sha256": hashlib.sha256(secret_fixture.encode("utf-8")).hexdigest(),
        "length": len(secret_fixture),
        "source": "env.json",
    }
    assert projected["personification_response_timeout"] == 301
