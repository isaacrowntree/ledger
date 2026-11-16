"""What counts as income and what counts as spending, defined once.

Every category has a kind:

- income: money earned (salary, rent received, business income, interest).
- expense: money spent. A category's refunds and the repayments linked to its
  purchases (etl/reimburse.py) net against it, so a $300 dinner your friends
  paid $200 of is $100 of Eating Out, and a returned jacket cancels its purchase.
- neutral: money that is neither earned nor spent: moving your own money
  (Transfers), money from family that isn't income (Personal (non-assessable)),
  and an employer paying back what you spent on its behalf (Employer
  Reimbursements). It moves balances but never income or spending totals.

Set it per category in config/categories.yaml with `kind:`; without it, a
category with `is_income: true` is income, the names in DEFAULT_NEUTRAL are
neutral, and everything else is an expense. Every report uses the predicates
below, so the overview, trends, year in review and tax views agree.
"""

KINDS = ("income", "expense", "neutral")
DEFAULT_NEUTRAL = frozenset({"Transfers", "Personal (non-assessable)", "Employer Reimbursements"})


def default_kind(name: str, is_income: bool) -> str:
    if is_income:
        return "income"
    return "neutral" if name in DEFAULT_NEUTRAL else "expense"


# SQL over `transactions t`, with no join needed. Uncategorised rows are
# spending: the money left, and leaving it out would understate the total.
KIND = "COALESCE((SELECT kind FROM categories WHERE id = t.category_id), 'expense')"
REPAYMENT = "t.id IN (SELECT deposit_id FROM reimbursements)"
EARNED = f"({KIND} = 'income' AND NOT {REPAYMENT})"
SPENT = f"({KIND} = 'expense')"
