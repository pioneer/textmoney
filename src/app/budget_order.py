"""Budget order revisions, isolated from older clients' shared defaults."""

from dataclasses import replace

from app.shared_defaults import POLICY, SharedDefaults

POLICY = replace(POLICY, directory="textmoney-budget-order", method="budget_order")


class BudgetOrderStore(SharedDefaults):
    groups = frozenset({"budget_order"})
