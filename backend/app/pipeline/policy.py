"""Reading and changing the policy dials.

The check ladder used to take these from environment variables, which made them
ours. They were never ours: the brief states no approval limit, no confidence
floor and no duplicate window, and there was nobody to ask. Holding them in the
database and exposing them to the person doing the reviewing puts the decision
where it belongs.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Policy

FIELDS = (
    "auto_post_enabled",
    "amount_review_threshold_jpy",
    "confidence_floor",
    "near_duplicate_window_days",
)


@dataclass(frozen=True)
class PolicyValues:
    auto_post_enabled: bool
    amount_review_threshold_jpy: int
    confidence_floor: float
    near_duplicate_window_days: int

    def as_dict(self) -> dict:
        return {f: getattr(self, f) for f in FIELDS}


async def get_policy(session: AsyncSession) -> Policy:
    """The single policy row, created from the environment defaults on first use.

    The environment still supplies the starting values, so a fresh deployment
    behaves as configured; from then on the row is what counts.
    """
    policy = await session.scalar(select(Policy).where(Policy.id == 1))
    if policy is None:
        policy = Policy(
            id=1,
            auto_post_enabled=settings.auto_post_enabled,
            amount_review_threshold_jpy=settings.amount_review_threshold_jpy,
            confidence_floor=settings.confidence_floor,
            near_duplicate_window_days=settings.near_duplicate_window_days,
            updated_by="environment defaults",
        )
        session.add(policy)
        await session.flush()
    return policy


async def read_policy(session: AsyncSession) -> PolicyValues:
    policy = await get_policy(session)
    return PolicyValues(**{f: getattr(policy, f) for f in FIELDS})


def snapshot(policy: Policy) -> dict:
    return {f: getattr(policy, f) for f in FIELDS}
