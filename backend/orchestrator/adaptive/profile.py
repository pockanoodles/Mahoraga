from __future__ import annotations
from typing import Optional

from .models import UserAdaptation

MIN_CONFIDENCE = 0.3


def _strength_label(confidence: float) -> str:
    if confidence >= 0.8:
        return "strong"
    if confidence >= 0.5:
        return "moderate"
    return "weak"


def build_profile_prompt(adaptations: list[UserAdaptation]) -> Optional[str]:
    relevant = [a for a in adaptations if a.confidence >= MIN_CONFIDENCE]
    if not relevant:
        return None

    lines = [
        f"- [{_strength_label(a.confidence)}] {a.category.value}: {a.key} = {a.value}"
        for a in relevant
    ]
    return "User profile:\n" + "\n".join(lines)
