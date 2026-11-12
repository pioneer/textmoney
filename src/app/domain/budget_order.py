"""Presentation ordering for budget categories, preserving their hierarchy."""

from collections.abc import Iterable

from app.domain.ledger import LedgerError


def budget_parent(account: str, accounts: set[str]) -> str:
    parent = account.rpartition(":")[0]
    while parent and parent not in accounts:
        parent = parent.rpartition(":")[0]
    return parent


def ordered_budgets(accounts: Iterable[str], order: tuple[str, ...]) -> tuple[str, ...]:
    identifiers = set(accounts)
    ranks = {account: index for index, account in enumerate(order)}
    children: dict[str, list[str]] = {}
    for account in identifiers:
        children.setdefault(budget_parent(account, identifiers), []).append(account)

    def subtree(parent: str) -> Iterable[str]:
        for account in sorted(
            children.get(parent, ()), key=lambda item: (ranks.get(item, len(ranks)), item)
        ):
            yield account
            yield from subtree(account)

    return tuple(subtree(""))


def move_budget(order: tuple[str, ...], source: str, target: str) -> tuple[str, ...]:
    accounts = set(order)
    if source not in accounts or target not in accounts:
        raise LedgerError("budget_order_stale")
    if source == target or target.startswith(source + ":"):
        return order
    parent = budget_parent(source, accounts)
    while budget_parent(target, accounts) != parent:
        target = budget_parent(target, accounts)
        if not target:
            raise LedgerError("budget_order_siblings")
    if source == target:
        return order
    block = [account for account in order if account == source or account.startswith(source + ":")]
    remaining = [account for account in order if account not in block]
    index = remaining.index(target)
    if order.index(source) < order.index(target):
        while index < len(remaining) and (
            remaining[index] == target or remaining[index].startswith(target + ":")
        ):
            index += 1
    return tuple(remaining[:index] + block + remaining[index:])
