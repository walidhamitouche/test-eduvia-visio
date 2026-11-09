from visio.api.ratelimit import WINDOW_SECONDS, RateLimiter


def test_blocks_after_limit_then_allows_after_window() -> None:
    limiter = RateLimiter()
    assert all(limiter.allow("1.2.3.4", 3, now=0.0) for _ in range(3))
    assert not limiter.allow("1.2.3.4", 3, now=1.0)
    assert limiter.allow("5.6.7.8", 3, now=1.0)  # une autre IP n'est pas touchée
    assert limiter.allow("1.2.3.4", 3, now=WINDOW_SECONDS + 1.0)
