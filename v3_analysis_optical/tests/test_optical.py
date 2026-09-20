"""Synthetic image/brightness fixtures; no real camera is opened."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from v3_analysis_optical.optical_core import Detector,DetectorSettings,calibrate,analyze_capture,load_capture
from v3_analysis_optical.common import DataError,read_json,write_json,read_csv,write_csv
from v3_analysis_optical.demo import optical_fixture
from v3_analysis_optical.camera import capture,parse_roi,roi_pixels

def frame(i,v,ts=None):
    t = 1_000_000_000 + i*33_333_333 if ts is None else ts
    return dict(frame_index=i,read_start_monotonic_ns=t-1_000_000,
                read_end_monotonic_ns=t,read_end_wall_ns=1_700_000_000_000_000_000+t,roi_mean=v)

class OpticalDetectorTests(unittest.TestCase):
    def events(self, values):
        d=Detector(DetectorSettings(60,120));return [e for i,v in enumerate(values) for e in d.feed(frame(i,v))]

    def test_initial_state_not_transition(self):
        events=self.events([20]*6)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['kind'],'optical_initial_state')

    def test_two_clean_edges(self):
        events=self.events([20]*6+[180]*6+[20]*6)
        edges=[e for e in events if e['kind']=='optical_transition']
        self.assertEqual([e['to_state'] for e in edges],['light','dark'])

    def test_first_support_separate_from_confirmation(self):
        e=self.events([20]*6+[180]*6)[1]
        self.assertEqual(e['first_support_frame'],6)
        self.assertEqual(e['confirmation_frame'],8)
        self.assertLess(e['first_support_receipt_ns'],e['confirmation_receipt_ns'])

    def test_exact_thresholds(self):
        events=self.events([60]*3+[120]*3+[60]*3)
        self.assertEqual(len([e for e in events if e['kind']=='optical_transition']),2)

    def test_flicker_not_a_confirmed_edge(self):
        self.assertEqual(len(self.events([20]*4+[180,20,180,20])),1)

    def test_middle_band_resets_debounce(self):
        self.assertEqual(len(self.events([20]*4+[180,180,90,180,180,90])),1)

    def test_gap_resets_not_guessed_transition(self):
        d=Detector(DetectorSettings(60,120,max_gap_ms=250))
        es=[]
        for i in range(3):es+=d.feed(frame(i,20))
        for i in range(3,6):es+=d.feed(frame(i,180,3_000_000_000+i*33_333_333))
        self.assertTrue(any(e['kind']=='optical_observation_gap' for e in es))
        self.assertFalse(any(e['kind']=='optical_transition' for e in es))

    def test_no_frame_skips_hidden(self):
        d=Detector(DetectorSettings(60,120));d.feed(frame(0,20))
        self.assertEqual(d.feed(frame(3,180))[0]['kind'],'optical_observation_gap')

    def test_decreasing_clock_rejected(self):
        d=Detector(DetectorSettings(60,120));d.feed(frame(0,20))
        with self.assertRaises(DataError):d.feed(frame(1,180,999_999_999))

    def test_equal_clock_breaks_both_edge_directions_and_pending_confirmation(self):
        for old,new in ((20,180),(180,20)):
            for equal_index in (3,4,5):
                with self.subTest(old=old,equal_index=equal_index):
                    d=Detector(DetectorSettings(60,120));events=[]
                    for i,v in enumerate([old]*3+[new]*6):
                        row=frame(i,v)
                        if i==equal_index:
                            row=frame(i,v,frame(i-1,v)['read_end_monotonic_ns'])
                        events+=d.feed(row)
                    gaps=[e for e in events if e['kind']=='optical_observation_gap']
                    self.assertEqual(len(gaps),1)
                    self.assertEqual(gaps[0]['reason'],'equal_quantized_receipt_timestamp')
                    self.assertEqual(gaps[0]['gap_ms'],0)
                    self.assertEqual(gaps[0]['edge_across_gap'],'UNRESOLVED_NOT_INFERRED')
                    self.assertFalse(any(e['kind']=='optical_transition' for e in events))
                    initial=events[-1]
                    self.assertEqual(initial['first_support_frame'],equal_index)
                    self.assertEqual(initial['confirmation_frame'],equal_index+2)
                    self.assertEqual(initial['first_support_receipt_ns'],frame(equal_index-1,new)['read_end_monotonic_ns'])

    def test_duplicate_or_decreasing_frame_index_rejected(self):
        for index in (0,1):
            d=Detector(DetectorSettings(60,120));d.feed(frame(1,20))
            with self.assertRaises(DataError):d.feed(frame(index,180,2_000_000_000))

    def test_invalid_brightness(self):
        for v in (float('nan'),float('inf'),-1,256):
            with self.subTest(v=v), self.assertRaises(DataError):
                Detector(DetectorSettings(60,120)).feed(frame(0,v))

    def test_invalid_settings(self):
        for settings in (DetectorSettings(120,60),DetectorSettings(60,120,1),DetectorSettings(60,120,3,-1)):
            with self.assertRaises(DataError):Detector(settings)

    def test_no_physical_timestamp_or_command_claim(self):
        e=self.events([20]*3+[180]*3)[1]
        self.assertFalse(e['physical_event_time_bound_available'])
        self.assertIsNone(e['physical_event_time_ns'])
        self.assertEqual(e['command_attribution'],'NOT_PERFORMED')

class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.dark=optical_fixture(self.root/'dark','dark',[20]*60)
        self.light=optical_fixture(self.root/'light','light',[180]*60)
        self.measure=optical_fixture(self.root/'measure','measurement',[20]*30+[180]*30+[20]*30)
        self.cal=self.root/'cal.json'

    def edit_synthetic_row(self, directory, key, value):
        header,rows=read_csv(directory/'frames.csv')
        rows[10][key]=value(rows)
        if key=='read_end_monotonic_ns':
            rows[10]['read_start_monotonic_ns']=int(rows[10][key])-1_000_000
        write_csv(directory/'frames.csv',rows,header)

    def test_equal_receipts_retained_and_calibration_thresholds_unchanged(self):
        baseline=calibrate(self.dark,self.light,self.root/'baseline.json')
        for directory in (self.dark,self.light,self.measure):
            self.edit_synthetic_row(directory,'read_end_monotonic_ns',lambda rows: rows[9]['read_end_monotonic_ns'])
            before=(directory/'frames.csv').read_bytes()
            metadata_before=(directory/'capture.json').read_bytes()
            meta,rows=load_capture(directory)
            self.assertEqual(meta['capture_validation']['equal_adjacent_receipt_timestamp_count'],1)
            self.assertEqual(rows[10]['read_end_monotonic_ns'],rows[9]['read_end_monotonic_ns'])
            self.assertEqual((directory/'frames.csv').read_bytes(),before)
            self.assertEqual((directory/'capture.json').read_bytes(),metadata_before)
        result=calibrate(self.dark,self.light,self.cal)
        for key in ('settings','dark_p95','light_p05','minimum_contrast','threshold_rule'):
            self.assertEqual(result[key],baseline[key])
        self.assertEqual(result['settings'],dict(low=20+160/3,high=20+320/3,debounce_frames=3,max_gap_ms=250.0))
        self.assertEqual(result['capture_validation']['dark']['equal_adjacent_receipt_timestamp_count'],1)
        summary=analyze_capture(self.measure,self.cal,self.root/'analysis')
        self.assertEqual(summary['capture_validation']['equal_adjacent_receipt_timestamp_count'],1)
        self.assertEqual(summary['observation_gaps'],1)

    def test_capture_decreasing_receipt_rejected(self):
        self.edit_synthetic_row(self.dark,'read_end_monotonic_ns',lambda rows:int(rows[9]['read_end_monotonic_ns'])-1)
        with self.assertRaises(DataError):load_capture(self.dark)

    def test_capture_duplicate_or_decreasing_index_rejected(self):
        for index in (9,8):
            self.edit_synthetic_row(self.dark,'frame_index',lambda rows:index)
            with self.assertRaises(DataError):load_capture(self.dark)

    def test_calibrate_then_analyze(self):
        cal=calibrate(self.dark,self.light,self.cal)
        self.assertLess(cal['settings']['low'],cal['settings']['high'])
        result=analyze_capture(self.measure,self.cal,self.root/'analysis')
        self.assertEqual(result['observed_dark_to_light'],1)
        self.assertEqual(result['observed_light_to_dark'],1)
        self.assertFalse(result['independent_physical_effect_deadline_verified'])

    def test_insufficient_contrast_refused(self):
        other=optical_fixture(self.root/'other','light',[24]*60)
        with self.assertRaises(DataError):calibrate(self.dark,other,self.cal)
        self.assertFalse(self.cal.exists())

    def test_too_few_calibration_frames(self):
        other=optical_fixture(self.root/'other','light',[180]*8)
        with self.assertRaises(DataError):calibrate(self.dark,other,self.cal)

    def test_calibration_cannot_be_overwritten(self):
        calibrate(self.dark,self.light,self.cal)
        with self.assertRaises(DataError):calibrate(self.dark,self.light,self.cal)

    def test_wrong_session_refused(self):
        calibrate(self.dark,self.light,self.cal)
        meta=read_json(self.measure/'capture.json');meta['session_id']='other';write_json(self.measure/'capture.json',meta)
        with self.assertRaises(DataError):analyze_capture(self.measure,self.cal,self.root/'analysis')

    def test_wrong_roi_refused(self):
        meta=read_json(self.light/'capture.json');meta['roi']=[1,1,16,16];write_json(self.light/'capture.json',meta)
        with self.assertRaises(DataError):calibrate(self.dark,self.light,self.cal)

    def test_partial_capture_requires_explicit_flag(self):
        calibrate(self.dark,self.light,self.cal)
        meta=read_json(self.measure/'capture.json');meta['status']='INVALID';write_json(self.measure/'capture.json',meta)
        with self.assertRaises(DataError):analyze_capture(self.measure,self.cal,self.root/'analysis')
        res=analyze_capture(self.measure,self.cal,self.root/'qualified',allow_partial=True)
        self.assertTrue(res['partial_capture'])

    def test_mixed_synthetic_real_refused(self):
        meta=read_json(self.light/'capture.json');meta['synthetic_fixture_only']=False;write_json(self.light/'capture.json',meta)
        with self.assertRaises(DataError):calibrate(self.dark,self.light,self.cal)

    def test_calibration_segment_not_measurement(self):
        calibrate(self.dark,self.light,self.cal)
        with self.assertRaises(DataError):analyze_capture(self.dark,self.cal,self.root/'analysis')

class CameraSafetyTests(unittest.TestCase):
    def test_camera_requires_explicit_optin(self):
        with patch('multiprocessing.get_context') as process:
            with self.assertRaises(DataError):capture({},Path('SHOULD_NOT_EXIST'))
            process.assert_not_called()

    def test_roi_parse(self):
        self.assertEqual(parse_roi('10,20,30,40'),(10,20,30,40))
        for text in ('1,2,3','0,0,-1,2','-1,0,1,2'):
            with self.assertRaises(Exception):parse_roi(text)

    def test_roi_outside_frame_refused(self):
        try:import numpy as np
        except ImportError:self.skipTest('numpy not installed; pure tests remain available')
        image=np.zeros((40,60,3),dtype=np.uint8)
        self.assertEqual(roi_pixels(image,(0,0,10,10)).shape,(10,10,3))
        with self.assertRaises(DataError):roi_pixels(image,(50,30,20,20))

    def test_offline_png_roundtrip(self):
        try:import cv2;import numpy as np
        except ImportError:self.skipTest('optional image dependencies unavailable')
        pixels=np.full((16,16),125,dtype=np.uint8)
        ok,data=cv2.imencode('.png',pixels)
        self.assertTrue(ok)
        restored=cv2.imdecode(data,cv2.IMREAD_GRAYSCALE)
        self.assertTrue(np.array_equal(pixels,restored))

    def test_no_actuator_or_network_imports(self):
        import ast
        package=Path(__file__).resolve().parents[1]
        prohibited={'paho','websocket','requests','urllib','run','run_v2','run_v3','power_characterization'}
        for name in ('camera.py','optical_core.py','boundary.py'):
            tree=ast.parse((package/name).read_text())
            imports=[]
            for node in ast.walk(tree):
                if isinstance(node,ast.Import):imports += [a.name.split('.')[0] for a in node.names]
                elif isinstance(node,ast.ImportFrom):imports.append((node.module or '').split('.')[0])
            self.assertFalse(set(imports)&prohibited,name)

class MockedCaptureWorkerTests(unittest.TestCase):
    def exercise(self, failure=None):
        import queue
        import time
        import types
        from v3_analysis_optical.camera import _worker
        try:import numpy as np
        except ImportError:self.skipTest('numpy missing; mock-image capture checks skipped')
        fake=types.SimpleNamespace(__version__='synthetic-mock')
        for i,name in enumerate(('CAP_ANY','CAP_DSHOW','CAP_MSMF','CAP_PROP_FRAME_WIDTH','CAP_PROP_FRAME_HEIGHT',
                                 'CAP_PROP_FPS','CAP_PROP_BUFFERSIZE','CAP_PROP_EXPOSURE','CAP_PROP_AUTO_EXPOSURE',
                                 'CAP_PROP_GAIN','CAP_PROP_AUTO_WB','CAP_PROP_AUTOFOCUS','COLOR_BGR2GRAY')):
            setattr(fake,name,i)
        class FakeCapture:
            count=0
            released=False
            def isOpened(self):return failure!='open'
            def getBackendName(self):return 'MOCK_ONLY'
            def set(self,*args):return False
            def get(self,*args):return 0.0
            def read(self):
                self.count+=1
                time.sleep(.002)
                return (False,None) if failure=='read' else (True,np.full((32,32,3),100,dtype=np.uint8))
            def release(self):self.released=True
        cap=FakeCapture()
        fake.VideoCapture=lambda *args:cap
        fake.cvtColor=lambda image,*args:image[:,:,0]
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',{'cv2':fake}):
            output=Path(tmp)
            options=dict(camera=0,backend='auto',roi=(0,0,16,16),label='measurement',session_id='TEST',
                         fps=30,width=32,height=32,warmup_s=0,duration_s=.01,no_save_roi=True)
            _worker(options,str(output),queue.Queue())
            meta=read_json(output/'capture.json')
            self.assertTrue(cap.released)
            if meta['status']=='COMPLETE':
                self.assertGreater(meta['received_frames'],0)
                self.assertFalse(meta['full_frames_saved'])
                self.assertFalse(meta['actuator_control'])
                self.assertTrue((output/'frames.csv').exists())
            else:self.assertTrue((output/'INVALID.txt').exists())
            return meta
    def test_mock_capture_complete_with_metadata(self):
        self.assertEqual(self.exercise()['status'],'COMPLETE')
    def test_mock_capture_open_failure_retained(self):
        self.assertEqual(self.exercise('open')['status'],'INVALID')
    def test_mock_capture_read_failure_retained(self):
        self.assertEqual(self.exercise('read')['status'],'INVALID')
