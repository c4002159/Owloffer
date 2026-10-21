"""Server-side capability boundaries for platform-specific workflows."""

PLATFORM_CAPABILITIES: dict[str, frozenset[str]] = {
    "boss": frozenset({"collect", "score", "greet", "deliver", "monitor"}),
    # New platforms start read-only. Delivery and monitoring stay locked until
    # an authorized real-account acceptance test has verified the live DOM and
    # the maintainer explicitly enables those capabilities in a later change.
    "zhilian": frozenset({"collect", "score", "greet"}),
    "zhilian_campus": frozenset({"collect", "score", "greet"}),
    # 51job applies from the job page; a send counts only after the page shows 已投递 (executor/job51_apply.py).
    "51job": frozenset({"collect", "score", "greet", "deliver"}),
    # Liepin applies from the job page too (executor/liepin_apply.py); verified with a real application, which
    # the user confirmed in their 投递记录. A send counts only on the 投递成功 page.
    "liepin": frozenset({"collect", "score", "greet", "deliver"}),
}


def platform_supports(platform: str, capability: str) -> bool:
    return capability in PLATFORM_CAPABILITIES.get(str(platform), frozenset())
