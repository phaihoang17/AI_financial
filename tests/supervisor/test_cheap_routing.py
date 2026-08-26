from __future__ import annotations

from src.evaluation.e2e_fixtures import e2e_fixture_cases
from src.supervisor.cheap_routing import (
    CHEAP_ROUTING_ACTIVATED,
    activated_model_tier,
    cheap_routing_eligible,
)
from src.supervisor.schemas import ModelTier, VerifyProfile


def _plans_by_case():
    return {
        case.case_id: case.expected_plan
        for case in e2e_fixture_cases()
        if case.expected_plan is not None
    }


def test_cheap_activation_is_on_after_baseline_approved():
    assert CHEAP_ROUTING_ACTIVATED is True


def test_only_cheap_routed_plans_are_eligible():
    plans = _plans_by_case()

    # DIRECT lookups/comparisons are routed CHEAP -> eligible.
    assert cheap_routing_eligible(plans["lookup"]) is True
    assert cheap_routing_eligible(plans["compare"]) is True
    # PROGRAM plans are routed STRONG -> never eligible for CHEAP.
    assert cheap_routing_eligible(plans["growth-rate"]) is False
    assert cheap_routing_eligible(plans["average"]) is False


def test_eligibility_delegates_to_the_plan_routed_tier():
    for plan in _plans_by_case().values():
        expected = plan.model_tier is ModelTier.CHEAP and not plan.abstain
        assert cheap_routing_eligible(plan) is expected


def test_activated_tier_never_downgrades_strong():
    plans = _plans_by_case()
    for case_id in ("growth-rate", "average"):
        assert plans[case_id].model_tier is ModelTier.STRONG
        assert activated_model_tier(plans[case_id]) is ModelTier.STRONG


def test_activated_tier_matches_plan_tier_while_activated():
    # With activation on, serving tier equals the approved plan tier for every
    # plan, so M9 (which consumes plan.model_tier) is unaffected.
    for plan in _plans_by_case().values():
        assert activated_model_tier(plan) is plan.model_tier


def test_cheap_activation_does_not_touch_verification_profile():
    plans = _plans_by_case()
    # compare is CHEAP + STRICT: activating CHEAP must not weaken STRICT.
    assert plans["compare"].model_tier is ModelTier.CHEAP
    assert plans["compare"].verify_profile is VerifyProfile.STRICT
    assert activated_model_tier(plans["compare"]) is ModelTier.CHEAP
    assert plans["compare"].verify_profile is VerifyProfile.STRICT
