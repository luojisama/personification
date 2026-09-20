"""Offline Promptfoo adapter contract; explicitly not a model quality result."""
import asyncio
from runner import invoke_case


async def call_api(prompt, options, context):
    await asyncio.sleep(0)
    result = await invoke_case({"id": "offline-canary"}, {"execution_mode": "simulated"})
    return {"output": result.status, "metadata": {"execution_mode": result.execution_mode}}
