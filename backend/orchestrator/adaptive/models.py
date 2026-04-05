from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AdaptationCategory(str, Enum):
    style = "style"
    tool_affinity = "tool_affinity"
    preference = "preference"
    pattern = "pattern"
    correction = "correction"


@dataclass
class UserProfile:
    user_id: str
    created_at: float
    updated_at: float

    @staticmethod
    def new(user_id: str) -> "UserProfile":
        now = time.time()
        return UserProfile(user_id=user_id, created_at=now, updated_at=now)


@dataclass
class UserAdaptation:
    id: str
    user_id: str
    category: AdaptationCategory
    key: str
    value: str
    confidence: float
    last_reinforced: float
    created_at: float

    @staticmethod
    def new(
        user_id: str,
        category: AdaptationCategory,
        key: str,
        value: str,
        confidence: float = 0.8,
    ) -> "UserAdaptation":
        now = time.time()
        return UserAdaptation(
            id=str(uuid.uuid4()),
            user_id=user_id,
            category=category,
            key=key,
            value=value,
            confidence=confidence,
            last_reinforced=now,
            created_at=now,
        )
