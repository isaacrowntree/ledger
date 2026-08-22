"""Rule-driven shared expenses.

Marking shared expenses by hand is what let ten weeks of household bills go
unshared. These rules mark them at ingest, on the same footing as categories and
tags -- with three properties that matter:

* A rule NEVER overrides a manual decision. If a transaction already has a shared
  entry (or was deliberately given one at a different split), the rule leaves it.
* A rule can start from a date, so turning one on does not reach back into
  periods that are already settled with the partner.
* Inclusion follows the merchant, not the category: Optus is a utility that is
  never shared, and fuel is shared on a road trip but not at the local servo.
"""
from etl.models import RawTransaction
from etl.shared import SharedRules

RULES = [
    {"pattern": "ORIGIN ENERGY|SUPERLOOP|DODO POWER|SYDNEY WATER", "split_pct": 50},
    {"pattern": "AAMI", "split_pct": 50, "from": "2026-08-01"},
    {"pattern": "MEDICARE", "split_pct": 100},
]


def _txn(description, date="2026-07-01", amount=-100.0):
    return RawTransaction(date=date, description=description, amount=amount)


class TestSharedRules:
    def setup_method(self):
        self.rules = SharedRules(rules=RULES)

    def test_household_bill_is_shared_fifty_fifty(self):
        assert self.rules.split_for(_txn("ORIGIN ENERGY BARANGAROO")) == 50

    def test_unmatched_merchant_is_not_shared(self):
        assert self.rules.split_for(_txn("Optus Billing MacquariePark NSW")) is None

    def test_matching_is_case_insensitive(self):
        assert self.rules.split_for(_txn("dodo power and gas melbourne")) == 50

    def test_a_rule_can_carry_its_own_split(self):
        assert self.rules.split_for(_txn("MEDICARE BENEFIT")) == 100

    def test_rule_with_a_start_date_ignores_earlier_transactions(self):
        """Turning a rule on must not reach into settled history."""
        assert self.rules.split_for(_txn("AAMI BRISBANE", date="2025-08-13")) is None

    def test_rule_with_a_start_date_applies_from_that_date(self):
        assert self.rules.split_for(_txn("AAMI INSURANCE", date="2026-08-11")) == 50

    def test_income_is_never_shared(self):
        """A refund or credit from a shared merchant is not an expense to split."""
        assert self.rules.split_for(_txn("ORIGIN ENERGY REFUND", amount=50.0)) is None

    def test_first_matching_rule_wins(self):
        rules = SharedRules(rules=[
            {"pattern": "ORIGIN", "split_pct": 60},
            {"pattern": "ORIGIN ENERGY", "split_pct": 50},
        ])
        assert rules.split_for(_txn("ORIGIN ENERGY")) == 60

    def test_failed_direct_debit_is_not_shared(self):
        """A "Force Post No Funds" that is reversed never took the money. Sharing
        it charges the partner for a payment that did not happen -- and the real
        payment, made days later, is shared separately."""
        rules = SharedRules(rules=[{"pattern": "SYDNEY WATER", "split_pct": 50,
                                    "exclude": "FORCE POST NO FUNDS|REVERSAL"}])
        failed = _txn("Force Post No Funds - Receipt 103024 Sydney Water")
        assert rules.split_for(failed) is None

    def test_the_real_payment_is_still_shared(self):
        rules = SharedRules(rules=[{"pattern": "SYDNEY WATER", "split_pct": 50,
                                    "exclude": "FORCE POST NO FUNDS|REVERSAL"}])
        real = _txn("Direct Debit - Receipt 101702 Sydney Water")
        assert rules.split_for(real) == 50

    def test_no_rules_means_nothing_is_shared(self):
        assert SharedRules(rules=[]).split_for(_txn("ORIGIN ENERGY")) is None
