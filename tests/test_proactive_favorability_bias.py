from ._loader import load_personification_module


flow = load_personification_module("plugin.personification.flows.proactive_flow")


def test_group_idle_probability_uses_signed_bias_with_bounds() -> None:
    assert flow.resolve_group_idle_probability(.15, -.20) == 0.0
    assert flow.resolve_group_idle_probability(.15, .20) == .35
    assert flow.resolve_group_idle_probability(1, .20) == .45
    assert flow.resolve_group_idle_probability(0, -.20) == 0.0
