import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import models
from src.executor import SimulatedAdapter, RecoveryExecutor
from src.models import ActionDecision

class TestExecutor(unittest.TestCase):
    def setUp(self):
        self.ex = RecoveryExecutor(SimulatedAdapter())

    def _dec(self, action, txn="t1"):
        return ActionDecision(txn, action, "test")

    def test_escalate_returns_escalated_no_money(self):
        o = self.ex.run(self._dec(models.ACTION_ESCALATE_HUMAN), 100.0)
        self.assertEqual(o.simulated_result, models.RESULT_ESCALATED)
        self.assertEqual(o.amount_recovered, 0.0)

    def test_notify_recovers_nothing(self):
        o = self.ex.run(self._dec(models.ACTION_NOTIFY_CUSTOMER), 100.0)
        self.assertEqual(o.amount_recovered, 0.0)

    def test_deterministic(self):
        a = self.ex.run(self._dec(models.ACTION_RETRY_NOW, "same"), 100.0)
        b = self.ex.run(self._dec(models.ACTION_RETRY_NOW, "same"), 100.0)
        self.assertEqual(a.simulated_result, b.simulated_result)
        self.assertEqual(a.amount_recovered, b.amount_recovered)

    def test_recovered_returns_full_amount(self):

        for i in range(50):
            o = self.ex.run(self._dec(models.ACTION_RETRY_NOW, f"txn{i}"), 250.0)
            if o.simulated_result == models.RESULT_RECOVERED:
                self.assertEqual(o.amount_recovered, 250.0)
                return
        self.fail("expected at least one recovery in sample")

    def test_no_action_still_failed(self):
        o = self.ex.run(self._dec(models.ACTION_NO_ACTION), 100.0)
        self.assertEqual(o.simulated_result, models.RESULT_STILL_FAILED)

if __name__ == "__main__":
    unittest.main()
