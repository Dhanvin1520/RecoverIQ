import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import degradation, models

def ev(idx, method, reason, epoch):
    import time
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
    return models.PaymentEvent(
        transaction_id=f"t{idx}", merchant_id="m1", customer_id="c1",
        amount=100.0, currency="INR", payment_method=method,
        status=models.STATUS_FAILED,
        failure_reason=reason, timestamp=ts, retry_count=0,
        true_label=models.LABEL_RECOVERABLE)

class TestDegradation(unittest.TestCase):
    def test_detects_tight_cluster(self):
        base = 1_700_000_000
        reason = "Acquiring bank did not respond within timeout window"
        events = [ev(i, "netbanking", reason, base + i * 30) for i in range(6)]
        incidents = degradation.detect(events)
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].payment_method, "netbanking")
        self.assertEqual(incidents[0].root_cause, models.CAUSE_BANK_TIMEOUT)
        self.assertGreaterEqual(incidents[0].count, 5)

    def test_no_incident_when_spread_out(self):
        base = 1_700_000_000
        reason = "Acquiring bank did not respond within timeout window"

        events = [ev(i, "netbanking", reason, base + i * 3600) for i in range(6)]
        self.assertEqual(degradation.detect(events), [])

    def test_no_incident_below_threshold(self):
        base = 1_700_000_000
        reason = "Acquiring bank did not respond within timeout window"
        events = [ev(i, "upi", reason, base + i * 30) for i in range(3)]
        self.assertEqual(degradation.detect(events), [])

if __name__ == "__main__":
    unittest.main()
