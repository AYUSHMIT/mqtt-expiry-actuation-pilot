"""Synthetic evidence only. No actual experiment/HA/camera access."""
import copy
from pathlib import Path
import tempfile
import unittest
from v3_analysis_optical.boundary import DEFAULT_PLAN, analyze_rows, inspect_row, classify_time, run
from v3_analysis_optical.common import read_json, write_csv, DataError, sha256
from v3_analysis_optical.demo import fixture_rows

class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.plan = read_json(DEFAULT_PLAN)
        self.row = fixture_rows()[0]

    def test_raw_timestamp_arithmetic_not_minus_5000(self):
        row = inspect_row(self.row, self.plan)
        self.assertEqual(row['recomputed_lateness_ms'], -2940)
        self.assertNotEqual(row['recomputed_lateness_ms'], -5000)

    def test_committed_minus_5000_bug_is_rejected(self):
        self.row['pre_service_lateness_ms'] = -5000
        row = inspect_row(self.row, self.plan)
        self.assertEqual(row['analysis_status'], 'INVALID_INPUT_CONSISTENCY')
        self.assertIn('DERIVED_FIELD_MISMATCH', row['analysis_issue'])
        self.assertEqual(row['pre_service_lateness_ms'], -5000)
        self.assertEqual(row['recomputed_lateness_ms'], -2940)

    def test_margin_equalities_are_boundary(self):
        for value in (-1000, 0, 999.9, 1000):
            self.assertEqual(classify_time(value, 1000), 'BOUNDARY_EXCLUDE_FROM_HEADLINE')
        self.assertEqual(classify_time(1000.01,1000), 'LATE_PRE_SERVICE')
        self.assertEqual(classify_time(-1000.01,1000), 'ON_TIME_PRE_SERVICE')

    def test_near_deadline_is_not_promoted(self):
        self.row['pre_service_at_ms'] = self.row['expires_at_ms'] + 500
        self.row['pre_service_lateness_ms'] = 500
        self.row['outcome'] = 'ON_TIME_PRE_SERVICE'
        r = inspect_row(self.row, self.plan)
        self.assertEqual(r['analysis_status'], 'INVALID_INPUT_CONSISTENCY')
        self.assertEqual(r['recomputed_time_class'], 'BOUNDARY_EXCLUDE_FROM_HEADLINE')

    def test_complete_fixture_has_full_plan(self):
        rows, cells, summary = analyze_rows(fixture_rows(), self.plan)
        self.assertEqual(summary['rows'],75)
        self.assertEqual(summary['complete_cells'],15)
        self.assertEqual(summary['csv_consistency_errors'],0)

    def test_missing_row_is_visible_and_cell_masked(self):
        _, cells, summary = analyze_rows(fixture_rows()[1:], self.plan)
        c = cells[0]
        self.assertEqual(c['n_missing'],1)
        self.assertIsNone(c['late_fraction_headline'])
        self.assertEqual(summary['complete_cells'],14)

    def test_empty_input_not_zero_violation_success(self):
        _, cells, summary = analyze_rows([], self.plan)
        self.assertEqual(summary['missing_rows'],75)
        self.assertTrue(all(c['late_fraction_headline'] is None for c in cells))

    def test_duplicate_cell_rep_rejects_both(self):
        rows = [copy.deepcopy(self.row),copy.deepcopy(self.row)]
        rows[1]['command_id'] = 'other'
        audited, _, _ = analyze_rows(rows, self.plan)
        self.assertTrue(all(r['analysis_status'] == 'INVALID_DUPLICATE_INPUT_ROW' for r in audited))

    def test_duplicate_command_id_rejects_both(self):
        rows = fixture_rows()[:2]
        rows[1]['command_id'] = rows[0]['command_id']
        audited, _, _ = analyze_rows(rows,self.plan)
        self.assertTrue(all(r['analysis_status'] == 'INVALID_DUPLICATE_INPUT_ROW' for r in audited))

    def test_bad_values_are_not_silently_dropped(self):
        for key, val in [('receipt_count','NaN'),('expires_at_ms','inf'),('queue_depth',-1),('rep',1.5)]:
            with self.subTest(key=key):
                row=copy.deepcopy(self.row); row[key]=val
                self.assertEqual(inspect_row(row,self.plan)['analysis_status'],'INVALID_INPUT_CONSISTENCY')

    def test_late_receipt_is_invalid(self):
        self.row['received_at_ms']=self.row['expires_at_ms']
        self.row['pre_service_at_ms']=self.row['expires_at_ms']+1500
        self.row['pre_service_lateness_ms']=1500
        self.assertEqual(inspect_row(self.row,self.plan)['analysis_status'],'INVALID_RECEIPT_NOT_PROVEN_BEFORE_DEADLINE')

    def test_missing_receipt_is_invalid(self):
        self.row['receipt_count']=0
        self.assertEqual(inspect_row(self.row,self.plan)['analysis_status'],'INVALID_NO_HA_RECEIPT')

    def test_missing_endpoint_is_unresolved(self):
        self.row.update(endpoint_on_transition_count=0,endpoint_on_at_ms='')
        self.assertEqual(inspect_row(self.row,self.plan)['analysis_status'],'UNRESOLVED_ENDPOINT_ATTRIBUTION')

    def test_unmatched_activity_is_unresolved(self):
        self.row['unmatched_endpoint_on_count']=1
        self.assertEqual(inspect_row(self.row,self.plan)['analysis_status'],'UNRESOLVED_UNEXPLAINED_ENDPOINT_ACTIVITY')

    def test_zero_pre_service_requires_null_not_zero_lateness(self):
        self.row.update(pre_service_count=0,pre_service_at_ms='',pre_service_lateness_ms=0)
        self.assertEqual(inspect_row(self.row,self.plan)['analysis_status'],'INVALID_INPUT_CONSISTENCY')

    def test_reject_plus_action_is_invalid(self):
        self.row['rejected_count']=1
        self.assertEqual(inspect_row(self.row,self.plan)['analysis_status'],'INVALID_REJECT_AND_EXECUTE')

    def test_upstream_unresolved_not_overridden(self):
        self.row['outcome']='UNRESOLVED_ENDPOINT_ATTRIBUTION'
        self.assertEqual(inspect_row(self.row,self.plan)['analysis_status'],'UNRESOLVED_ENDPOINT_ATTRIBUTION')

    def test_off_plan_policy_rejected(self):
        self.row['policy']='physical_v3_predictive_admission'
        self.assertEqual(inspect_row(self.row,self.plan)['analysis_status'],'INVALID_INPUT_CONSISTENCY')

    def test_rejection_separate_from_timing(self):
        plan=copy.deepcopy(self.plan);plan['policies']=['physical_v3_execution_check']
        self.row.update(policy='physical_v3_execution_check',pre_service_count=0,pre_service_at_ms='',
                        pre_service_lateness_ms='',endpoint_on_transition_count=0,endpoint_on_at_ms='',
                        rejected_count=1,outcome='REJECTED_EXECUTION_CHECK')
        r=inspect_row(self.row,plan)
        self.assertEqual(r['analysis_status'],'REJECTED_EXECUTION_CHECK')
        self.assertIsNone(r['recomputed_lateness_ms'])

    def test_unknown_rejection_reason_rejected(self):
        plan=copy.deepcopy(self.plan);plan['policies']=['physical_v3_execution_check']
        self.row.update(policy='physical_v3_execution_check',pre_service_count=0,pre_service_at_ms='',
                        pre_service_lateness_ms='',endpoint_on_transition_count=0,endpoint_on_at_ms='',
                        rejected_count=1,outcome='REJECTED_TRIGGER_CHECK')
        self.assertEqual(inspect_row(self.row,plan)['analysis_status'],'INVALID_INPUT_CONSISTENCY')

    def test_input_hash_unchanged_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);csv=root/'input.csv';write_csv(csv,fixture_rows());before=sha256(csv)
            run(csv,root/'output',synthetic=True,plots=False)
            self.assertEqual(before,sha256(csv))
            with self.assertRaises(DataError):run(csv,root/'output',synthetic=True,plots=False)

    def test_synthetic_cannot_be_published_as_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'in.csv';write_csv(p,fixture_rows())
            with self.assertRaises(DataError):run(p,root/'out',plots=False)

    def test_missing_columns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'in.csv';write_csv(p,[{'test':'x'}])
            with self.assertRaises(DataError):run(p,root/'out',plots=False)
