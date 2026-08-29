import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import models, policy
from src.models import Diagnosis

def diag(category, txn="t1", conf=0.9):
    return Diagnosis(txn, category, conf, "test")

class TestPolicy(unittest.TestCase):
    def test_bank_timeout_retry_now(self):
        d = policy.decide(diag(models.CAUSE_BANK_TIMEOUT), 0)
        self.assertEqual(d.action, models.ACTION_RETRY_NOW)
        self.assertIsNone(d.blocked_by_rule)

    def test_network_retry_now(self):
        d = policy.decide(diag(models.CAUSE_NETWORK_ERROR), 0)
        self.assertEqual(d.action, models.ACTION_RETRY_NOW)

    def test_insufficient_funds_retry_delayed(self):
        d = policy.decide(diag(models.CAUSE_INSUFFICIENT_FUNDS), 0)
        self.assertEqual(d.action, models.ACTION_RETRY_DELAYED)

    def test_card_expired_new_method(self):
        d = policy.decide(diag(models.CAUSE_CARD_EXPIRED), 0)
        self.assertEqual(d.action, models.ACTION_REQUEST_NEW_PAYMENT_METHOD)

    def test_fraud_never_retries(self):
        d = policy.decide(diag(models.CAUSE_RISK_BLOCK), 0)
        self.assertEqual(d.action, models.ACTION_ESCALATE_HUMAN)
        self.assertEqual(d.blocked_by_rule, policy.RULE_FRAUD_NEVER_RETRY)

    def test_retry_cap_escalates(self):
        d = policy.decide(diag(models.CAUSE_BANK_TIMEOUT), policy.MAX_RETRIES)
        self.assertEqual(d.action, models.ACTION_ESCALATE_HUMAN)
        self.assertEqual(d.blocked_by_rule, policy.RULE_RETRY_CAP)

    def test_below_cap_still_retries(self):
        d = policy.decide(diag(models.CAUSE_BANK_TIMEOUT), policy.MAX_RETRIES - 1)
        self.assertEqual(d.action, models.ACTION_RETRY_NOW)

    def test_unknown_escalates_with_rule(self):
        d = policy.decide(diag(models.CAUSE_UNKNOWN), 0)
        self.assertEqual(d.action, models.ACTION_ESCALATE_HUMAN)
        self.assertEqual(d.blocked_by_rule, policy.RULE_UNKNOWN_ESCALATE)

    def test_helpers(self):
        self.assertTrue(policy.is_fraud_adjacent(diag(models.CAUSE_RISK_BLOCK)))
        self.assertFalse(policy.is_fraud_adjacent(diag(models.CAUSE_BANK_TIMEOUT)))
        self.assertTrue(policy.retry_cap_reached(policy.MAX_RETRIES))
        self.assertFalse(policy.retry_cap_reached(0))

if __name__ == "__main__":
    unittest.main()
