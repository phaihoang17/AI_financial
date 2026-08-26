"""TASK-101 CHEAP-tier routing activation.

M4 routing (:func:`src.supervisor.router.route_model_tier`) already assigns
``ModelTier.CHEAP`` to eligible ``DIRECT`` plans and ``ModelTier.STRONG`` to
``PROGRAM`` plans. Per ADR-024 and the M10 header in ``docs/TASKS.md``, treating
CHEAP as a cost optimization is permitted only after the
strong-tier/strict-verification correctness baseline (TASK-100) is established.

This module exposes that activation as an explicit, auditable decision. It does
not re-derive the routing rule: the approved ``Plan.model_tier`` remains the
single source of truth. It never downgrades a required ``STRONG`` tier and never
touches the verification profile — model tier and ``VerifyProfile`` are
orthogonal, so STRICT verification is unaffected by CHEAP activation.
"""

from __future__ import annotations

from src.supervisor.schemas import ModelTier, Plan


# The TASK-100 baseline is approved, so CHEAP routing may be activated for the
# already-approved eligible cases. A future toggle to ``False`` conservatively
# serves eligible plans at STRONG cost; it can never affect correctness.
CHEAP_ROUTING_ACTIVATED = True


def cheap_routing_eligible(plan: Plan) -> bool:
    """Return whether an approved plan is eligible for CHEAP routing.

    Eligibility delegates to the plan's already-routed tier: a non-abstaining
    plan the M4 policy routed to ``ModelTier.CHEAP``. This never re-classifies
    the plan and never makes a STRONG-routed plan eligible.
    """
    if not isinstance(plan, Plan):
        raise TypeError("plan must be a Plan")
    if plan.abstain:
        return False
    return plan.model_tier is ModelTier.CHEAP


def activated_model_tier(plan: Plan) -> ModelTier:
    """Resolve the serving model tier under CHEAP activation.

    A required ``STRONG`` tier is always preserved. An eligible CHEAP plan is
    served CHEAP only while activation is on; otherwise it falls back to STRONG,
    which is safe because it only raises cost, never weakens verification.
    """
    if not isinstance(plan, Plan):
        raise TypeError("plan must be a Plan")
    if plan.model_tier is ModelTier.STRONG:
        return ModelTier.STRONG
    if CHEAP_ROUTING_ACTIVATED and cheap_routing_eligible(plan):
        return ModelTier.CHEAP
    return ModelTier.STRONG
