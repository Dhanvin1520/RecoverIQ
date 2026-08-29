import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.diagnoser import RuleBasedDiagnoser
from src import models

class TestDiagnoser(unittest.TestCase):
    def setUp(self):
        self.d = RuleBasedDiagnoser()

    def _view(self, reason, status="failed"):
        return {"transaction_id": "t1", "failure_reason": reason, "status": status}

    def test_bank_timeout(self):
        r = self.d.diagnose(self._view("Acquiring bank did not respond within timeout"))
        self.assertEqual(r.root_cause_category, models.CAUSE_BANK_TIMEOUT)

    def test_network(self):
        r = self.d.diagnose(self._view("Transient network error contacting gateway"))
        self.assertEqual(r.root_cause_category, models.CAUSE_NETWORK_ERROR)

    def test_insufficient_funds(self):
        r = self.d.diagnose(self._view("Insufficient funds in customer account"))
        self.assertEqual(r.root_cause_category, models.CAUSE_INSUFFICIENT_FUNDS)

    def test_card_expired(self):
        r = self.d.diagnose(self._view("Card expired"))
        self.assertEqual(r.root_cause_category, models.CAUSE_CARD_EXPIRED)

    def test_risk_block(self):
        r = self.d.diagnose(self._view("Blocked by risk engine: suspected fraud"))
        self.assertEqual(r.root_cause_category, models.CAUSE_RISK_BLOCK)

    def test_fraud_beats_other_patterns(self):

        r = self.d.diagnose(self._view("Blocked: customer on risk blacklist"))
        self.assertEqual(r.root_cause_category, models.CAUSE_RISK_BLOCK)

    def test_permanent_decline_is_unknown(self):
        r = self.d.diagnose(self._view("Issuer declined: do not honour"))
        self.assertEqual(r.root_cause_category, models.CAUSE_UNKNOWN)

    def test_empty_reason_unknown(self):
        r = self.d.diagnose(self._view(None))
        self.assertEqual(r.root_cause_category, models.CAUSE_UNKNOWN)

    def test_success_no_diagnosis(self):
        r = self.d.diagnose(self._view(None, status="success"))
        self.assertEqual(r.confidence, 1.0)

    def test_never_reads_true_label(self):

        view = self._view("Card expired")
        view["true_label"] = "fraud"
        r = self.d.diagnose(view)
        self.assertEqual(r.root_cause_category, models.CAUSE_CARD_EXPIRED)

if __name__ == "__main__":
    unittest.main()

class TestLLMDiagnoserFallback(unittest.TestCase):
    """The LLM path must never break the run — it falls back to rules."""

    def setUp(self):
        from src.diagnoser import LLMDiagnoser
        self.d = LLMDiagnoser()

    def test_fallback_matches_rules_category(self):
        view = {"transaction_id": "t1", "status": "failed",
                "failure_reason": "Card expired"}
        r = self.d.diagnose(view)
        self.assertEqual(r.root_cause_category, models.CAUSE_CARD_EXPIRED)

    def test_fallback_fraud(self):
        view = {"transaction_id": "t2", "status": "failed",
                "failure_reason": "Blocked by risk engine: suspected fraud"}
        r = self.d.diagnose(view)
        self.assertEqual(r.root_cause_category, models.CAUSE_RISK_BLOCK)

    def test_always_valid_category(self):
        view = {"transaction_id": "t3", "status": "failed",
                "failure_reason": "something weird"}
        r = self.d.diagnose(view)
        from src.diagnoser import _VALID_CATEGORIES
        self.assertIn(r.root_cause_category, _VALID_CATEGORIES)
