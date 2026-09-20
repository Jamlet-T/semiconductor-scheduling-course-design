"""派工策略。"""

from fab_scheduler.policies.base import (
    DispatchAction,
    DispatchPolicy,
    DispatchState,
)
from fab_scheduler.policies.fifo import FIFOPolicy

__all__ = [
    "DispatchAction",
    "DispatchPolicy",
    "DispatchState",
    "FIFOPolicy",
]
