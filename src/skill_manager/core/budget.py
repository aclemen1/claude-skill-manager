"""Context budget estimation for skills."""

from __future__ import annotations

from pathlib import Path

from skill_manager.models import BudgetEntry, DiscoveredItem


# Claude Code allocates ~2% of context window for skill descriptions.
# Fallback budget is 16,000 characters.
DEFAULT_BUDGET_CHARS = 16_000
CHARS_PER_TOKEN_ESTIMATE = 4

_cache: dict[str, BudgetEntry] = {}


def estimate_item_budget(item: DiscoveredItem) -> BudgetEntry:
    """Estimate the context budget consumed by a single item."""
    desc_chars = len(item.description)

    # Read the full content for content estimation
    content_chars = 0
    if item.path.is_dir():
        skill_md = item.path / "SKILL.md"
        if skill_md.exists():
            content_chars = len(skill_md.read_text(encoding="utf-8"))
    elif item.path.is_file():
        content_chars = len(item.path.read_text(encoding="utf-8"))

    # Description is always loaded; content only on invocation.
    # Budget impact = description + frontmatter overhead.
    budget_chars = desc_chars + 50  # ~50 chars overhead for name/type metadata

    return BudgetEntry(
        qualified_name=item.qualified_name,
        description_chars=desc_chars,
        content_chars=content_chars,
        estimated_tokens=budget_chars // CHARS_PER_TOKEN_ESTIMATE,
    )


def get_token_estimate(item: DiscoveredItem) -> int:
    """Get cached token estimate for a single item."""
    qn = item.qualified_name
    if qn not in _cache:
        _cache[qn] = estimate_item_budget(item)
    return _cache[qn].estimated_tokens


def estimate_total_budget(items: list[DiscoveredItem]) -> tuple[list[BudgetEntry], int, int]:
    """Estimate budget for all items.

    Returns (entries, total_chars_used, budget_limit).
    """
    entries = [estimate_item_budget(item) for item in items]
    total = sum(e.description_chars + 50 for e in entries)
    return entries, total, DEFAULT_BUDGET_CHARS
