from __future__ import annotations

import time
from collections import defaultdict, deque
from uuid import UUID

UNFURL_LIMIT = 30
UNFURL_WINDOW_SECONDS = 60


class RateLimitExceeded(Exception):
    pass


_buckets: dict[str, deque[float]] = defaultdict(deque)


def check_unfurl_rate_limit(user_id: UUID) -> None:
    key = str(user_id)
    now = time.monotonic()
    bucket = _buckets[key]
    while bucket and now - bucket[0] > UNFURL_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= UNFURL_LIMIT:
        raise RateLimitExceeded("Unfurl rate limit exceeded")
    bucket.append(now)
