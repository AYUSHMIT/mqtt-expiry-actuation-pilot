import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_v3 import (PULSE_S, QUEUE_DEPTHS, TTL_GRID_S, PER_JOB_SERVICE_BOUND_MS,
                   DISPATCH_MARGIN_MS, CLOCK_BOUND_MS, REQUEST_CLASSIFICATION_MARGIN_MS,
                   admission_admits, validate_candidate_endpoint_state,
                   assert_candidate_endpoint_is_safe, fail_closed_candidate_off,
                   is_candidate_mode, CANDIDATE_MODES, predicted_wait_bound_ms)


class RunV3MathTests(unittest.TestCase):
    def test_formula_and_examples(self):
        self.assertEqual(PULSE_S, 5)
        self.assertEqual(QUEUE_DEPTHS, [0, 1, 2])
        self.assertEqual(TTL_GRID_S, [3, 6, 9, 12, 20])
        self.assertEqual(PER_JOB_SERVICE_BOUND_MS, 8000)
        self.assertEqual(DISPATCH_MARGIN_MS, 500)
        self.assertEqual(CLOCK_BOUND_MS, 250)
        self.assertEqual(REQUEST_CLASSIFICATION_MARGIN_MS, 1000)
        self.assertEqual(CANDIDATE_MODES, {'boundary', 'policy'})
        self.assertEqual(predicted_wait_bound_ms(0), 500)
        self.assertEqual(predicted_wait_bound_ms(2), 16500)

        self.assertTrue(admission_admits(now_ms=10000, deadline_ms=15000, predicted_wait_ms=500))
        self.assertFalse(admission_admits(now_ms=10000, deadline_ms=15000, predicted_wait_ms=8500))
        self.assertTrue(admission_admits(now_ms=10000, deadline_ms=30000, predicted_wait_ms=16500))
        self.assertFalse(admission_admits(now_ms=10000, deadline_ms=15000, predicted_wait_ms=5000))

    def test_equality_rejects(self):
        self.assertFalse(admission_admits(now_ms=10000, deadline_ms=15000, predicted_wait_ms=5000))

    def test_candidate_mode_guard(self):
        self.assertTrue(is_candidate_mode('boundary'))
        self.assertTrue(is_candidate_mode('policy'))
        self.assertFalse(is_candidate_mode('characterize-power'))

    def test_endpoint_state_abort_rules(self):
        with self.assertRaises(ValueError):
            validate_candidate_endpoint_state('on', phase='begin')
        with self.assertRaises(ValueError):
            validate_candidate_endpoint_state('unavailable', phase='end')
        with self.assertRaises(ValueError):
            assert_candidate_endpoint_is_safe('on', 'off')
        with self.assertRaises(ValueError):
            assert_candidate_endpoint_is_safe('off', 'on')

    def test_sensitive_turn_off_guard(self):
        with self.assertRaises(RuntimeError):
            fail_closed_candidate_off()


if __name__ == '__main__':
    unittest.main()
