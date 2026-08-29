import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import economics, models

class TestEconomics(unittest.TestCase):
    def test_expected_value_positive_for_large_amount(self):
        ev = economics.expected_value(models.ACTION_RETRY_NOW, 1000.0)
        self.assertGreater(ev, 0)

    def test_expected_value_negative_for_tiny_amount(self):

        ev = economics.expected_value(models.ACTION_RETRY_NOW, 1.0)
        self.assertLess(ev, 0)
        self.assertFalse(economics.is_worth_attempting(models.ACTION_RETRY_NOW, 1.0))

    def test_worth_attempting_threshold(self):
        self.assertTrue(economics.is_worth_attempting(models.ACTION_RETRY_NOW, 500.0))

    def test_fraud_incident_cost_exceeds_amount(self):
        c = economics.fraud_incident_cost(100.0)
        self.assertGreater(c, 100.0)

    def test_escalation_is_costly(self):
        self.assertGreater(
            economics.action_cost(models.ACTION_ESCALATE_HUMAN),
            economics.action_cost(models.ACTION_RETRY_NOW))

if __name__ == "__main__":
    unittest.main()
