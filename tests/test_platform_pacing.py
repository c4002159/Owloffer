from bosshunter.collection.platforms.job51 import _ApiRateLimiter as Job51Limiter
from bosshunter.collection.platforms.zhilian import _ApiRateLimiter as ZhilianLimiter
from bosshunter.platform_safety import apply_pacing, pacing_multiplier


class _Collector:
    def __init__(self):
        self.delay_range = (8.0, 15.0)
        self.detail_delay_range = (4.0, 6.0)
        self.page_delay_range = (2.0, 3.0)


def test_defaults_slow_zhilian_only_and_settings_are_bounded():
    assert pacing_multiplier({}, "zhilian") == 1.5
    assert pacing_multiplier({}, "51job") == 1.0 and pacing_multiplier({}, "liepin") == 1.0 and pacing_multiplier({}, "zhilian_campus") == 1.0
    assert pacing_multiplier({"collection": {"platform_pacing": {"liepin": 2}}}, "liepin") == 2.0
    assert pacing_multiplier({"collection": {"platform_pacing": {"liepin": 99}}}, "liepin") == 5.0
    assert pacing_multiplier({"collection": {"platform_pacing": {"liepin": 0.01}}}, "liepin") == 0.5
    assert pacing_multiplier({"collection": {"platform_pacing": {"liepin": "x"}}}, "liepin") == 1.0


def test_the_waits_are_stretched_once_however_often_collect_runs():
    collector = _Collector()
    apply_pacing(collector, 1.5)
    apply_pacing(collector, 1.5)
    assert collector.delay_range == (12.0, 22.5) and collector.detail_delay_range == (6.0, 9.0) and collector.page_delay_range == (3.0, 4.5)


def test_a_factor_of_one_changes_nothing():
    collector = _Collector()
    apply_pacing(collector, 1.0)
    assert collector.delay_range == (8.0, 15.0)


def test_the_api_limiters_stretch_gaps_and_breaks_and_lower_the_per_minute_cap():
    for limiter_class in (Job51Limiter, ZhilianLimiter):
        base = limiter_class(total_requests=10)
        slow = limiter_class(total_requests=10, pace=2.0)
        assert slow._gap_range == tuple(v * 2 for v in base._gap_range)
        assert slow._break_range == tuple(v * 2 for v in base._break_range)
        assert slow._per_min_limit <= base._per_min_limit
        slow.set_tier(10)
        assert slow._gap_range == tuple(v * 2 for v in base._gap_range)       # a tier change keeps the pace
