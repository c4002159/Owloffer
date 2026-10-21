"""Collector protocol and explicit platform failure categories."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from threading import Event
from typing import Callable, Protocol

from bosshunter.collection.models import (
    JobCandidate,
    PlatformCollectionRequest,
    PlatformCollectionResult,
)


class CollectionError(RuntimeError):
    """An expected, user-actionable collection failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CollectionBlockedError(CollectionError):
    """The platform requires user action or has blocked the session."""


def hours_since_midnight() -> int:
    """Hours since local midnight, rounded up (at least 1): how far back "today's" checkpoints reach."""
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, math.ceil((now - midnight).total_seconds() / 3600))


@dataclass
class CollectorHooks:
    """Callbacks supplied by the shared collection layer."""

    stop_event: Event | None
    on_list_candidate: Callable[[JobCandidate], bool]
    on_candidate: Callable[[JobCandidate], bool]
    on_parse_failed: Callable[[str], None]
    on_event: Callable[..., None]
    # Continuing a scan does not imply every candidate was saved reliably.
    can_checkpoint: Callable[[], bool] = lambda: True
    # Checkpoints belong to one explicitly selected run, never to global searches.
    completed_page: Callable[[str, str], int] = lambda city, keyword: 0
    on_page_complete: Callable[[str, str, int], None] = lambda city, keyword, page: None


class Collector(Protocol):
    platform: str

    def collect(
        self,
        request: PlatformCollectionRequest,
        hooks: CollectorHooks,
    ) -> PlatformCollectionResult:
        ...
