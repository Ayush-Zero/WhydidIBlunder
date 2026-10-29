"""C7: per-IP rate limiting, without a web framework.

The limiter knows about keys, clocks and a sliding window; it does not know
what HTTP is. :mod:`server.api` extracts the client IP and turns a denied
decision into a ``429``.

Two decisions worth stating:

* **A sliding window, not a fixed bucket.** A fixed window lets a caller send
  ``limit`` requests at 12:59 and another ``limit`` at 13:00. A deque expires
  each request individually, so the limit is true for *any* one-hour span.
* **In-memory and per process.** v1 runs one API process, so a dictionary is
  enough; a multi-process deployment would multiply the limit. Moving the
  counters into SQLite would add a write to every analysis request, which is a
  worse trade for this milestone. The limitation is documented in
  ``docs/api.md``.

The X-Forwarded-For rule is deliberately narrow: only a peer in
``TRUSTED_PROXY_IPS`` may speak for a client, and only the **rightmost** entry
is used. Caddy appends the peer it actually saw, so the rightmost entry is the
one Caddy vouches for; taking the leftmost would trust a client-supplied spoof.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)

__all__ = [
    "DEFAULT_RATE_LIMIT",
    "DEFAULT_TRUSTED_PROXIES",
    "RateLimitDecision",
    "RateLimiter",
    "client_ip",
    "parse_trusted_proxies",
    "rate_limit_per_hour_from_env",
    "trusted_proxies_from_env",
]

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network

#: Five analyses per hour is the plan's v1 default. ``0`` disables the limit.
DEFAULT_RATE_LIMIT = 5

#: Caddy on the same host connects over loopback. In Docker, set this to the
#: proxy's network; the default deliberately does **not** trust private ranges
#: wholesale, because that would also trust anything else on that network.
DEFAULT_TRUSTED_PROXIES = "127.0.0.1,::1"


@dataclass(frozen=True)
class RateLimitDecision:
    """The answer for one request, ready to become headers or a ``429``.

    ``retry_after`` is only meaningful when ``allowed`` is false; it is always
    at least one second so ``Retry-After: 0`` never appears.
    """

    allowed: bool
    limit: int
    remaining: int
    retry_after: int
    reset_after: int


class RateLimiter:
    """A thread-safe sliding-window limiter keyed by client IP.

    Parameters
    ----------
    limit:
        Requests allowed per ``window_seconds``. ``0`` disables the limiter.
    window_seconds:
        The sliding window, in seconds. Production uses one hour.
    max_keys:
        A hard cap on remembered clients. Without it, an attacker with many IPs
        could grow the dictionary forever. Evicting the oldest key can reset a
        legitimate client's count; that is the bounded-memory trade-off.
    clock:
        Injectable monotonic clock, so tests do not sleep.
    """

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float = 3600.0,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 0:
            raise ValueError("limit must be >= 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if max_keys < 1:
            raise ValueError("max_keys must be >= 1")
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def check(self, key: str) -> RateLimitDecision:
        """Record one request for ``key`` and say whether it is allowed.

        A denied request is **not** recorded: the caller already has to wait for
        the oldest allowed request to expire, and charging the denial would only
        extend the penalty.
        """
        if not self.enabled:
            return RateLimitDecision(
                allowed=True, limit=0, remaining=0, retry_after=0, reset_after=0
            )

        now = self._clock()
        cutoff = now - self.window_seconds

        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self.max_keys:
                    # Drop the least recently used client. A brand-new key is
                    # preferable to letting the map grow without bound.
                    self._buckets.popitem(last=False)
                bucket = deque()
                self._buckets[key] = bucket
            else:
                self._buckets.move_to_end(key)

            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= self.limit:
                retry_after = max(
                    1, math.ceil(bucket[0] + self.window_seconds - now)
                )
                return RateLimitDecision(
                    allowed=False,
                    limit=self.limit,
                    remaining=0,
                    retry_after=retry_after,
                    reset_after=retry_after,
                )

            bucket.append(now)
            reset_after = max(
                1, math.ceil(bucket[0] + self.window_seconds - now)
            )
            return RateLimitDecision(
                allowed=True,
                limit=self.limit,
                remaining=self.limit - len(bucket),
                retry_after=0,
                reset_after=reset_after,
            )


def _parse_ip(raw: str | None) -> IPAddress | None:
    """Parse one address, returning ``None`` for a non-IP or empty value.

    IPv4-mapped IPv6 (``::ffff:127.0.0.1``) is normalised to IPv4 so it matches
    a loopback trust rule instead of looking like a different client.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        parsed = ip_address(value)
    except ValueError:
        return None
    if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def _is_trusted(address: IPAddress, trusted: Sequence[IPNetwork]) -> bool:
    return any(address in network for network in trusted)


def client_ip(
    peer: str | None,
    forwarded_for: str | None,
    trusted: Sequence[IPNetwork],
) -> str:
    """Resolve the client IP, trusting ``X-Forwarded-For`` only from a proxy.

    * If the immediate peer is **not** trusted, its address is the key and the
      header is ignored entirely. A direct caller cannot spoof its identity.
    * If the peer is trusted, the **rightmost** valid XFF entry is used. Caddy
      appends the peer it saw, so that is the entry the proxy vouches for.
    * A malformed rightmost entry falls back to the peer rather than to an
      earlier, client-controlled entry.
    """
    peer_address = _parse_ip(peer)
    if peer_address is None:
        # Test clients and unusual transports may not have an IP. They still
        # need a stable key, but they must not be treated as a trusted proxy.
        return (peer or "unknown").strip() or "unknown"

    if not _is_trusted(peer_address, trusted):
        return str(peer_address)

    if not forwarded_for:
        return str(peer_address)

    parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    if not parts:
        return str(peer_address)

    forwarded = _parse_ip(parts[-1])
    if forwarded is None:
        return str(peer_address)
    return str(forwarded)


def parse_trusted_proxies(raw: str) -> tuple[IPNetwork, ...]:
    """Parse ``TRUSTED_PROXY_IPS`` (comma-separated IPs or CIDRs).

    A bare IP becomes a single-host network. Invalid entries raise rather than
    being ignored: a typo in a trust list should stop the process, not silently
    change who is trusted.
    """
    networks: list[IPNetwork] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            if "/" in token:
                network = ip_network(token, strict=False)
            else:
                address = ip_address(token)
                network = ip_network(
                    f"{address}/{address.max_prefixlen}", strict=False
                )
        except ValueError as exc:
            raise ValueError(f"invalid trusted proxy {token!r}") from exc
        if network.prefixlen == 0:
            # ``0.0.0.0/0`` / ``::/0`` would trust every peer, which makes XFF
            # spoofable by anyone. That is a misconfiguration, not a policy.
            raise ValueError(
                f"invalid trusted proxy {token!r}: trusting every address "
                "is not allowed"
            )
        networks.append(network)
    return tuple(networks)


def rate_limit_per_hour_from_env() -> int:
    """Read ``RATE_LIMIT_ANALYSES_PER_HOUR``; ``0`` means disabled."""
    raw = os.getenv(
        "RATE_LIMIT_ANALYSES_PER_HOUR", str(DEFAULT_RATE_LIMIT)
    ).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "RATE_LIMIT_ANALYSES_PER_HOUR must be an integer, "
            f"got {raw!r}"
        ) from exc
    if value < 0:
        raise ValueError("RATE_LIMIT_ANALYSES_PER_HOUR must be >= 0")
    return value


def trusted_proxies_from_env() -> tuple[IPNetwork, ...]:
    """Read ``TRUSTED_PROXY_IPS``; defaults to loopback-only."""
    return parse_trusted_proxies(
        os.getenv("TRUSTED_PROXY_IPS", DEFAULT_TRUSTED_PROXIES)
    )
