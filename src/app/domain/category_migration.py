"""A reviewed, source-bound category subtree migration."""

from dataclasses import dataclass


def migrated_category(identifier: str, original: str, target: str) -> str:
    if identifier == original or identifier.startswith(original + ":"):
        return target + identifier[len(original) :]
    return identifier


@dataclass(frozen=True)
class CategoryMigration:
    original: str
    target: str
    source: str
    candidate: str
    categories: int
    transactions: int
    budgets: int
    references: int
