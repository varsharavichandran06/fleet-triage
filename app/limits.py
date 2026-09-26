"""
Abuse controls for a publicly reachable endpoint.

Every triage run spends money on two external APIs, so an open endpoint is a
billing risk rather than merely a load risk. Three controls apply, in order
of how much they protect:

  daily budget   a hard ceiling on runs per day across all callers. This is
                 the only control that bounds total spend regardless of how
                 the traffic is distributed.
  rate limit     per caller, sliding window over a minute and an hour. Stops
                 one source consuming the daily budget in a burst.
  api key        optional. When set, a caller presenting it bypasses the
                 rate limit but not the daily budget.

State is in memory, which is correct for a single instance and would need a
shared store if the service is ever scaled horizontally.

The key is deliberately not a secret in the browser sense: a static frontend
cannot hold one privately. It exists for programmatic callers and for the
operator, not to authenticate end users.
"""

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Optional

from app.config import (
    API_KEY,
    DAILY_RUN_BUDGET,
    RATE_LIMIT_PER_HOUR,
    RATE_LIMIT_PER_MINUTE,
    TRUST_PROXY_HEADER,
)

log = logging.getLogger(__name__)

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)
_budget_day: Optional[int] = None
_budget_used = 0


@dataclass
class Rejection:
    """Why a request was refused, and what to tell the caller."""

    status: int
    detail: str
    retry_after: Optional[int] = None


def client_key(request) -> str:
    """
    Identifies the caller for rate limiting.

    X-Forwarded-For is only consulted when TRUST_PROXY_HEADER is set, because
    the header is caller-supplied and trivially spoofed when requests can
    reach the application directly. Behind a proxy that always rewrites it,
    the left-most entry is the original client.
    """
    if TRUST_PROXY_HEADER:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune(stamps: deque, now: float) -> None:
    while stamps and now - stamps[0] > 3600:
        stamps.popleft()


def _today() -> int:
    return int(time.time() // 86400)


def check(request, *, api_key: Optional[str]) -> Optional[Rejection]:
    """
    Applies the budget and rate limits. Returns None to allow the request,
    or a Rejection describing why it was refused.

    Counts are incremented here rather than after the run completes, so a
    request that fails partway still consumes quota. That is deliberate: the
    money is spent whether or not the response is useful.
    """
    global _budget_day, _budget_used

    now = time.time()
    privileged = bool(API_KEY) and api_key == API_KEY

    with _lock:
        # Daily budget. Applies to everyone, including key holders, because
        # it is the control that bounds absolute spend.
        day = _today()
        if _budget_day != day:
            _budget_day, _budget_used = day, 0
        if _budget_used >= DAILY_RUN_BUDGET:
            log.warning("daily budget of %d runs exhausted", DAILY_RUN_BUDGET)
            return Rejection(
                429,
                "Daily request budget for this demo has been reached. "
                "It resets at midnight UTC.",
                retry_after=int((day + 1) * 86400 - now),
            )

        if not privileged:
            stamps = _hits[client_key(request)]
            _prune(stamps, now)
            in_minute = sum(1 for t in stamps if now - t <= 60)
            if in_minute >= RATE_LIMIT_PER_MINUTE:
                return Rejection(
                    429,
                    f"Rate limit: at most {RATE_LIMIT_PER_MINUTE} requests per minute.",
                    retry_after=60,
                )
            if len(stamps) >= RATE_LIMIT_PER_HOUR:
                return Rejection(
                    429,
                    f"Rate limit: at most {RATE_LIMIT_PER_HOUR} requests per hour.",
                    retry_after=int(3600 - (now - stamps[0])),
                )
            stamps.append(now)

        _budget_used += 1

    return None


def usage() -> dict:
    """Current budget consumption, for the health endpoint."""
    with _lock:
        used = _budget_used if _budget_day == _today() else 0
        return {
            "runs_today": used,
            "daily_budget": DAILY_RUN_BUDGET,
            "tracked_clients": len(_hits),
        }
