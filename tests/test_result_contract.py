"""The jury must receive usable evidence or an explicit processing failure."""
import base64
import copy
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from PIL import Image
from pydantic import ValidationError

from cashframe.config import Settings
from cashframe.schemas import CompletedResult
from cashframe.storage import JobStore
from cashframe.worker import Worker


def encoded_image(format='JPEG'):
    stream = BytesIO()
    Image.new('RGB', (16, 12), 'navy').save(stream, format=format)
    return base64.b64encode(stream.getvalue()).decode('ascii')


def completed_payload():
    return {'job_id': 'fixture', 'status': 'done', 'verdict': 'violation',
            'violations': [{'type': 'Synthetic event', 'timecode_sec': 1.,
                            'description': 'Synthetic visible action for contract testing.',
                            'evidence_frames': [{'timestamp_sec': 1., 'image_base64': encoded_image()}]}]}


class ResultContractTests(unittest.TestCase):
    def test_decodable_jpeg_and_exact_case_fields_are_preserved(self):
        payload = completed_payload()
        payload['violations'][0]['evidence_frames'] *= 3
        self.assertEqual(CompletedResult.model_validate(payload, context={'duration_sec': 2.}).model_dump(), payload)

    def test_blank_event_type_and_explanation_cannot_be_reported_as_done(self):
        for field in ('type', 'description'):
            for value in ('', ' \t\n'):
                payload = completed_payload()
                payload['violations'][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    CompletedResult.model_validate(payload)

    def test_base64_must_decode_to_a_complete_jpeg_instead_of_text_or_png(self):
        raw = base64.b64decode(encoded_image())
        for value in ('not base64', base64.b64encode(b'not an image').decode(),
                      encoded_image('PNG'), base64.b64encode(raw[:-20]).decode()):
            payload = completed_payload()
            payload['violations'][0]['evidence_frames'][0]['image_base64'] = value
            with self.subTest(value=value[:20]), self.assertRaises(ValidationError):
                CompletedResult.model_validate(payload)

    def test_event_and_evidence_times_are_bounded_by_accepted_source_duration(self):
        for event_time, frame_time in ((2.01, 1.), (1., 2.01)):
            payload = completed_payload()
            payload['violations'][0]['timecode_sec'] = event_time
            payload['violations'][0]['evidence_frames'][0]['timestamp_sec'] = frame_time
            with self.subTest(event_time=event_time, frame_time=frame_time), self.assertRaises(ValidationError):
                CompletedResult.model_validate(payload, context={'duration_sec': 2.})

    def test_exact_video_end_is_exclusive_but_last_visible_timestamps_are_valid(self):
        for event_time, frame_time in ((2., 1.), (1., 2.)):
            payload=completed_payload()
            payload['violations'][0]['timecode_sec']=event_time
            payload['violations'][0]['evidence_frames'][0]['timestamp_sec']=frame_time
            with self.subTest(event_time=event_time, frame_time=frame_time),self.assertRaises(ValidationError):
                CompletedResult.model_validate(payload,context={'duration_sec':2.})
        payload=completed_payload()
        payload['violations'][0]['timecode_sec']=1.999999
        payload['violations'][0]['evidence_frames'][0]['timestamp_sec']=1.999999
        self.assertEqual(CompletedResult.model_validate(payload,context={'duration_sec':2.}).model_dump(),payload)

    def test_nonfinite_times_and_inconsistent_verdict_are_rejected(self):
        for value in (float('nan'), float('inf'), -1.):
            payload = completed_payload()
            payload['violations'][0]['timecode_sec'] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                CompletedResult.model_validate(payload)
        payload = completed_payload()
        payload['verdict'] = 'no_violation'
        with self.assertRaises(ValidationError):
            CompletedResult.model_validate(payload)


class WorkerResultBoundaryTests(unittest.TestCase):
    def run_worker(self, result, source_duration=2.):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        store = JobStore(root / 'jobs.sqlite')
        jid = store.create()
        store.update(jid, duration_sec=source_duration)
        path = root / 'input.mov'
        path.write_bytes(b'not decoded: injected analyzer boundary test')
        # Analyzer metadata cannot expand bounds established when accepting the source.
        worker = Worker(Settings(root=root), store,
                        analyzer=lambda *args: (result, {'duration_sec': 999.}))
        self.assertTrue(worker.reserve())
        worker._run(jid, path, time.monotonic())
        self.assertTrue(worker.reserve(), 'Invalid analysis must release the worker slot')
        worker.release()
        return store.get(jid)

    def test_invalid_evidence_and_out_of_source_time_fail_without_negative_verdict(self):
        for defect in ('jpeg', 'event_time', 'frame_time'):
            result = completed_payload()
            result.pop('job_id')
            event = result['violations'][0]
            if defect == 'jpeg':
                event['evidence_frames'][0]['image_base64'] = base64.b64encode(b'invalid JPEG').decode()
            elif defect == 'event_time':
                event['timecode_sec'] = 3.
            else:
                event['evidence_frames'][0]['timestamp_sec'] = 3.
            with self.subTest(defect=defect):
                job = self.run_worker(copy.deepcopy(result))
                self.assertEqual(job['status'], 'failed')
                self.assertEqual(job['error'], 'analysis_failed')
                self.assertNotIn('result', job)
                self.assertNotIn('verdict', job)

    def test_valid_completed_result_and_negative_result_still_finish(self):
        result = completed_payload()
        result.pop('job_id')
        self.assertEqual(self.run_worker(result)['status'], 'done')
        negative = self.run_worker({'verdict': 'no_violation', 'violations': []})
        self.assertEqual(negative['status'], 'done')
        self.assertEqual(negative['result']['verdict'], 'no_violation')


if __name__ == '__main__':
    unittest.main()
