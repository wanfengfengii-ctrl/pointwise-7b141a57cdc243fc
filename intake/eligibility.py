from __future__ import annotations

from dataclasses import dataclass

from capture.orders import CaptureOrder
from intake.context import ReceivedContext, match_context
from intake.files import ReceivedFile


@dataclass(frozen=True)
class EligibilityDecision:
    status: str
    code: str | None = None


def assess_eligibility(
    order: CaptureOrder, file: ReceivedFile | None, context: ReceivedContext | None
) -> EligibilityDecision:
    if file is None or context is None:
        return EligibilityDecision('pending', 'MATERIALS_INCOMPLETE')
    conflict = match_context(file, context)
    if conflict:
        return EligibilityDecision('rejected', conflict)
    if (context.order_id, context.portal_id) != (order.order_id, order.portal_id):
        return EligibilityDecision('rejected', 'MATERIAL_IDENTITY_MISMATCH')
    if (context.region, context.quarter) != (order.region, order.quarter):
        return EligibilityDecision('rejected', 'EXPORT_CONTEXT_MISMATCH')
    return EligibilityDecision('qualified')
