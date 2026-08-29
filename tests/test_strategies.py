import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import strategies, models
from data.generate_events import generate

class TestStrategies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = generate(150, seed=42)
        cls.cmp = strategies.compare(cls.events)

    def test_do_nothing_recovers_nothing(self):
        self.assertEqual(self.cmp["do_nothing"]["gross_recovered"], 0.0)
        self.assertEqual(self.cmp["do_nothing"]["net"], 0.0)

    def test_agent_never_retries_fraud(self):
        self.assertEqual(self.cmp["compliant_agent"]["fraud_retry_violations"], 0)

    def test_naive_commits_fraud_violations(self):
        self.assertGreater(self.cmp["naive_retry_all"]["fraud_retry_violations"], 0)

    def test_agent_net_positive(self):
        self.assertGreater(self.cmp["compliant_agent"]["net"], 0)

    def test_naive_is_inadmissible(self):

        self.assertGreater(self.cmp["naive_retry_all"]["fraud_retry_violations"], 0)
        self.assertEqual(self.cmp["compliant_agent"]["fraud_retry_violations"], 0)

    def test_naive_pays_fraud_fallout(self):
        self.assertGreater(self.cmp["naive_retry_all"]["fraud_cost"], 0)
        self.assertEqual(self.cmp["compliant_agent"]["fraud_cost"], 0)

if __name__ == "__main__":
    unittest.main()
