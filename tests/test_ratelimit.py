"""C7 — the web-free rate limiter and X-Forwarded-For resolution.

These tests pin the two things that are easy to get subtly wrong:

* the window is **sliding**, not a fixed bucket, and a denied request does not
  extend the penalty;
* ``X-Forwarded-For`` is ignored unless the immediate peer is a configured
  proxy, and then only the **rightmost** entry is used — the one Caddy appended.

No HTTP and no sleeping: the clock is injected.
"""

from __future__ import annotations

import pytest

from server.ratelimit import (
    DEFAULT_RATE_LIMIT,
    RateLimiter,
    client_ip,
    parse_trusted_proxies,
    rate_limit_per_hour_from_env,
    trusted_proxies_from_env,
)


class Clock:
    """A monotonic clock the test can advance by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _trusted(*entries: str):
    return parse_trusted_proxies(",".join(entries))


# --- the limiter ----------------------------------------------------------


def test_the_limit_is_enforced_and_remaining_counts_down():
    clock = Clock()
    limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)

    first = limiter.check("203.0.113.7")
    second = limiter.check("203.0.113.7")
    third = limiter.check("203.0.113.7")

    assert first.allowed and first.remaining == 1
    assert second.allowed and second.remaining == 0
    assert not third.allowed and third.remaining == 0
    assert third.retry_after == 60


def test_the_window_slides_instead_of_resetting_on_a_boundary():
    clock = Clock()
    limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)

    assert limiter.check("ip").allowed  # t=0
    clock.advance(30)
    assert limiter.check("ip").allowed  # t=30
    clock.advance(30.1)  # t=60.1: the t=0 request has expired, t=30 has not
    decision = limiter.check("ip")

    assert decision.allowed
    assert decision.remaining == 0


def test_a_denied_request_does_not_extend_the_penalty():
    clock = Clock()
    limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)

    assert limiter.check("ip").allowed  # t=0
    clock.advance(10)
    assert not limiter.check("ip").allowed  # denied, not recorded
    clock.advance(50)  # t=60: the t=0 request has expired
    assert limiter.check("ip").allowed


def test_retry_after_is_never_zero():
    clock = Clock()
    limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)

    assert limiter.check("ip").allowed
    clock.advance(59.9)
    decision = limiter.check("ip")

    assert not decision.allowed
    assert decision.retry_after >= 1


def test_different_clients_have_independent_buckets():
    clock = Clock()
    limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)

    assert limiter.check("198.51.100.1").allowed
    assert not limiter.check("198.51.100.1").allowed
    assert limiter.check("198.51.100.2").allowed


def test_zero_disables_the_limiter():
    clock = Clock()
    limiter = RateLimiter(limit=0, window_seconds=60, clock=clock)

    assert not limiter.enabled
    for _ in range(20):
        decision = limiter.check("ip")
        assert decision.allowed
        assert decision.limit == 0


def test_max_keys_evicts_the_least_recently_used_client():
    clock = Clock()
    limiter = RateLimiter(
        limit=1, window_seconds=60, max_keys=2, clock=clock
    )

    assert limiter.check("a").allowed
    assert limiter.check("b").allowed
    assert limiter.check("c").allowed  # evicts "a"
    # "a" was evicted, so it gets a fresh bucket instead of a 429.
    assert limiter.check("a").allowed
    # "c" is still remembered and still at its limit.
    assert not limiter.check("c").allowed


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": -1},
        {"limit": 1, "window_seconds": 0},
        {"limit": 1, "max_keys": 0},
    ],
)
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        RateLimiter(**kwargs)


# --- X-Forwarded-For ------------------------------------------------------


def test_an_untrusted_peer_cannot_spoof_xff():
    assert (
        client_ip("203.0.113.5", "198.51.100.9", ())
        == "203.0.113.5"
    )


def test_a_trusted_proxy_uses_the_rightmost_xff_entry():
    trusted = _trusted("10.0.0.0/8")
    assert (
        client_ip("10.0.0.2", "198.51.100.9, 203.0.113.7", trusted)
        == "203.0.113.7"
    )


def test_a_spoofed_left_entry_does_not_win():
    trusted = _trusted("10.0.0.0/8")
    assert (
        client_ip("10.0.0.2", "1.2.3.4, 203.0.113.7", trusted)
        == "203.0.113.7"
    )


def test_a_trusted_proxy_without_xff_uses_its_own_address():
    trusted = _trusted("10.0.0.0/8")
    assert client_ip("10.0.0.2", None, trusted) == "10.0.0.2"


def test_a_malformed_rightmost_entry_falls_back_to_the_peer():
    trusted = _trusted("10.0.0.0/8")
    assert (
        client_ip("10.0.0.2", "203.0.113.7, not-an-ip", trusted)
        == "10.0.0.2"
    )


def test_an_ipv4_mapped_peer_is_normalised():
    trusted = _trusted("127.0.0.1")
    assert client_ip("::ffff:127.0.0.1", None, trusted) == "127.0.0.1"


def test_an_ipv6_client_is_kept_as_ipv6():
    trusted = _trusted("2001:db8::/32")
    assert (
        client_ip("2001:db8::1", "2001:db8::2", trusted) == "2001:db8::2"
    )


def test_no_peer_still_produces_a_key():
    assert client_ip(None, "203.0.113.7", ()) == "unknown"


# --- configuration --------------------------------------------------------


def test_trusted_proxies_accept_bare_ips_and_cidrs():
    networks = parse_trusted_proxies("127.0.0.1, ::1, 10.0.0.0/8")
    assert [str(network) for network in networks] == [
        "127.0.0.1/32",
        "::1/128",
        "10.0.0.0/8",
    ]


def test_an_empty_trust_list_trusts_nobody():
    assert parse_trusted_proxies(" , ") == ()


@pytest.mark.parametrize(
    "raw", ["nope", "10.0.0.0/33", "::1/129", "0.0.0.0/0", "::/0"]
)
def test_an_invalid_trusted_proxy_is_rejected(raw):
    with pytest.raises(ValueError, match="invalid trusted proxy"):
        parse_trusted_proxies(raw)


def test_the_rate_limit_defaults_to_five(monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_ANALYSES_PER_HOUR", raising=False)
    assert rate_limit_per_hour_from_env() == DEFAULT_RATE_LIMIT == 5


def test_the_rate_limit_can_be_configured(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ANALYSES_PER_HOUR", "2")
    assert rate_limit_per_hour_from_env() == 2


def test_zero_disables_the_configured_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ANALYSES_PER_HOUR", "0")
    assert rate_limit_per_hour_from_env() == 0


@pytest.mark.parametrize("raw", ["five", "-1", ""])
def test_an_invalid_rate_limit_is_rejected(monkeypatch, raw):
    monkeypatch.setenv("RATE_LIMIT_ANALYSES_PER_HOUR", raw)
    with pytest.raises(ValueError, match="RATE_LIMIT_ANALYSES_PER_HOUR"):
        rate_limit_per_hour_from_env()


def test_trusted_proxies_default_to_loopback(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    assert [str(network) for network in trusted_proxies_from_env()] == [
        "127.0.0.1/32",
        "::1/128",
    ]


def test_trusted_proxies_can_be_configured(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "172.16.0.0/12")
    assert [str(network) for network in trusted_proxies_from_env()] == [
        "172.16.0.0/12"
    ]
