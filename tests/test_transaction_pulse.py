"""Offline transaction validation; embedded timestamps, no live device calls."""
import copy
from datetime import datetime, timedelta, timezone
import unittest

import boundary_v3 as b


MARKERS = ('pre_service', 'on_confirmed', 'off_request', 'off_confirmed', 'finished')
CID = '20260920T180634Z-3b25f535-r1-q2-t6-target0'
CONTEXT = '01M3003RRJKM7YDYA19CAH5XDW'
# Minimal projection of the preserved partial run's stage/endpoint events.
# No timestamps are normalized, and tests do not read or modify that run.
Q2_T6_STAGES = (
    '2026-09-20T18:09:18.448305+00:00',
    '2026-09-20T18:09:19.835819+00:00',
    '2026-09-20T18:09:24.834813+00:00',
    '2026-09-20T18:09:26.359340+00:00',
    '2026-09-20T18:09:26.359559+00:00',
)
Q2_T6_ENDPOINTS = ('2026-09-20T18:09:19.835239+00:00',
                   '2026-09-20T18:09:26.358741+00:00')


def transaction(stages, endpoints):
    events = [dict(event_type='expiry_v3_stage', time_fired=at,
                   data=dict(command_id=CID, stage=marker), context=dict(id=CONTEXT))
              for marker, at in zip(MARKERS, stages)]
    for at, old, new in zip(endpoints, ('off', 'on'), ('on', 'off')):
        events.append(dict(event_type='state_changed', time_fired=at,
                           data=dict(entity_id=b.ENDPOINT_ENTITY, old_state=dict(state=old),
                                     new_state=dict(state=new, context=dict(id=CONTEXT)))))
    return [dict(kind='ha_event', event=e) for e in events]


def synthetic(pulse_ms=5000):
    start = datetime(2026, 9, 20, tzinfo=timezone.utc)
    def at(ms):
        return (start + timedelta(milliseconds=ms)).isoformat()
    return transaction([at(ms) for ms in (0, 100, 100+pulse_ms, 200+pulse_ms, 201+pulse_ms)],
                       [at(50), at(150+pulse_ms)])


class TransactionPulseTests(unittest.TestCase):
    def test_nominal_and_frozen_tolerance(self):
        self.assertEqual(b.PULSE_S, 5)
        self.assertEqual(b.PULSE_VALIDATION_TOLERANCE_MS, b.CLOCK_BOUND_MS)
        self.assertEqual(b.PULSE_VALIDATION_TOLERANCE_MS, 250)
        b.verify_transaction(CID, synthetic(5000))

    def test_4998_994_ms_passes(self):
        b.verify_transaction(CID, synthetic(4998.994))

    def test_exact_lower_bound_passes(self):
        b.verify_transaction(CID, synthetic(4750))

    def test_one_microsecond_below_lower_bound_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'pulse is invalid'):
            b.verify_transaction(CID, synthetic(4749.999))

    def test_actual_q2_t6_fixture(self):
        pulse = (datetime.fromisoformat(Q2_T6_STAGES[2]) -
                 datetime.fromisoformat(Q2_T6_STAGES[1])).total_seconds()*1000
        self.assertAlmostEqual(pulse, 4998.994, places=6)
        self.assertLess(pulse, b.PULSE_S*1000-1)
        b.verify_transaction(CID, transaction(Q2_T6_STAGES, Q2_T6_ENDPOINTS))

    def test_out_of_order_stages_fail(self):
        for i in range(4):
            rows = synthetic()
            a, z = rows[i]['event'], rows[i+1]['event']
            a['time_fired'], z['time_fired'] = z['time_fired'], a['time_fired']
            with self.subTest(pair=i), self.assertRaisesRegex(RuntimeError, 'order is invalid'):
                b.verify_transaction(CID, rows)

    def test_missing_or_duplicate_stage_fails(self):
        for i in range(5):
            for duplicate in (False, True):
                rows = synthetic()
                if duplicate:
                    rows.append(copy.deepcopy(rows[i]))
                else:
                    rows.pop(i)
                with self.subTest(stage=i, duplicate=duplicate), self.assertRaisesRegex(RuntimeError, 'missing/duplicate'):
                    b.verify_transaction(CID, rows)

    def test_missing_or_duplicate_on_off_fails(self):
        for i in (5, 6):
            for duplicate in (False, True):
                rows = synthetic()
                if duplicate:
                    rows.append(copy.deepcopy(rows[i]))
                else:
                    rows.pop(i)
                with self.subTest(endpoint=i, duplicate=duplicate), self.assertRaisesRegex(RuntimeError, 'ON/OFF sequence'):
                    b.verify_transaction(CID, rows)

    def test_stage_context_invariant(self):
        for context in (None, 'different-context'):
            rows = synthetic()
            rows[2]['event']['context']['id'] = context
            with self.subTest(context=context), self.assertRaisesRegex(RuntimeError, 'contexts are ambiguous'):
                b.verify_transaction(CID, rows)

    def test_endpoint_context_invariant(self):
        rows = synthetic()
        rows[5]['event']['data']['new_state']['context']['id'] = 'different-context'
        with self.assertRaisesRegex(RuntimeError, 'ON/OFF sequence'):
            b.verify_transaction(CID, rows)

    def test_endpoint_stage_order_still_required(self):
        rows = synthetic()
        rows[5]['event']['time_fired'] = rows[2]['event']['time_fired']
        with self.assertRaisesRegex(RuntimeError, 'Endpoint transitions do not match'):
            b.verify_transaction(CID, rows)


if __name__ == '__main__':
    unittest.main()
