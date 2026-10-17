"""All report evidence in these tests is synthetic; no bank frames are read."""
import base64
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader

from cashframe import reports


def jpeg(color='navy', size=(640, 360)):
    stream = BytesIO()
    Image.new('RGB', size, color).save(stream, format='JPEG')
    return stream.getvalue()


def synthetic_job(positive=True):
    frames = [{'timestamp_sec': t, 'image_base64': base64.b64encode(jpeg(c)).decode()}
              for t, c in [(1, 'navy'), (2, 'teal'), (3, 'gold')]]
    return {'job_id': 'synthetic-job', 'status': 'done', 'filename': 'Қазақша ӘҒҚҢӨҰҮҺІ.mov',
            'source_sha256': 'a' * 64, 'created_at': 1792238400, 'completed_at': 1792238412,
            'api_key': 'never-include-secret', 'source_path': 'D:/private/bank.mov',
            'result': {'verdict': 'violation' if positive else 'no_violation', 'violations': [
                {'type': 'unconfirmed_event', 'timecode_sec': 2, 'description': 'Модель <сигнал> & проверка',
                 'evidence_frames': frames}] if positive else []},
            'review': {'events': [{'start_sec': 1, 'end_sec': 3, 'timecode_sec': 2}],
                       'metadata': {'size_bytes': 8000, 'duration_sec': 10, 'width': 640, 'height': 360,
                                    'average_fps': 25, 'nominal_fps': 25, 'frame_rate_mode': 'constant'},
                       'limitations': ['Только синтетическая проверка.'],
                       'version_snapshot': {'model': {'id': 'local-binary', 'sha256': 'b' * 64,
                                                      'encoder_sha256': 'c' * 64},
                                            'configuration': {'id': 'config-v1', 'sha256': 'd' * 64},
                                            'code': {'version': 'v2', 'sha256': 'e' * 64},
                                            'parameters': {'sample_fps': 4, 'api_key': 'secret-parameter'}}}}


class ReportTests(unittest.TestCase):
    def render(self, job, **kwargs):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        artifacts = reports.generate_report(job, Path(temporary.name), **kwargs)
        manifest_bytes = artifacts.passport_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        text = '\n'.join(page.extract_text() for page in PdfReader(artifacts.pdf_path).pages)
        return artifacts, manifest_bytes, manifest, text

    def test_passport_binds_actual_jpegs_and_canonical_bytes(self):
        job = synthetic_job()
        artifacts, raw, manifest, text = self.render(job)
        self.assertEqual(raw, json.dumps(manifest, sort_keys=True, ensure_ascii=False,
                                         separators=(',', ':'), allow_nan=False).encode('utf-8'))
        self.assertEqual(artifacts.passport_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(manifest['source']['sha256'], 'a' * 64)
        self.assertEqual(manifest['versions']['model']['encoder_sha256'], 'c' * 64)
        frames = manifest['events'][0]['evidence_frames']
        self.assertEqual([f['timestamp_sec'] for f in frames], [1, 2, 3])
        self.assertEqual(frames[1]['sha256'], hashlib.sha256(jpeg('teal')).hexdigest())
        self.assertIn(artifacts.passport_id, text)
        self.assertIn(artifacts.passport_sha256, text.replace('\n', '').replace(' ', ''))
        embedded_hashes = set()
        for page in PdfReader(artifacts.pdf_path).pages:
            for obj in (page.get('/Resources').get('/XObject') or {}).values():
                image = obj.get_object()
                if image.get('/Subtype') == '/Image':
                    embedded_hashes.add(hashlib.sha256(image.get_data()).hexdigest())
        self.assertTrue({f['sha256'] for f in frames}.issubset(embedded_hashes))
        changed = copy.deepcopy(job)
        changed['result']['violations'][0]['evidence_frames'][1]['image_base64'] = base64.b64encode(jpeg('red')).decode()
        different, _, _, _ = self.render(changed)
        self.assertNotEqual(different.passport_sha256, artifacts.passport_sha256)

    def test_positive_negative_unicode_escaping_and_secret_exclusion(self):
        for positive in [True, False]:
            _, raw, _, text = self.render(synthetic_job(positive))
            self.assertIn('Предварительный акт видеоаудита', text)
            self.assertIn('уполномоченным сотрудником', text)
            self.assertIn('Қазақша ӘҒҚҢӨҰҮҺІ.mov', text)
            self.assertIn('UTC', text)
            self.assertIn('Нормативное основание не установлено', text)
            self.assertIn('Ограничения анализа', text)
            for secret in ['never-include-secret', 'D:/private', 'secret-parameter']:
                self.assertNotIn(secret, text + raw.decode())
            if positive:
                self.assertIn('Модель <сигнал> & проверка', text)
                self.assertIn('Тип не установлен', text)
            else:
                self.assertIn('События моделью не обнаружены', text)

    def test_unknown_provenance_and_technical_metadata_are_explicit(self):
        job = synthetic_job(False)
        job.pop('source_sha256')
        job['review'] = {}
        _, _, manifest, text = self.render(job)
        self.assertIsNone(manifest['source']['sha256'])
        self.assertIsNone(manifest['versions']['model']['sha256'])
        self.assertIn('не сохран', text.lower())
        self.assertIn('не установлен', text.lower())

    def test_only_confirmed_clear_human_applicability_can_show_verified_rule(self):
        annotation = {'event_index': 0, 'start_sec': 1, 'end_sec': 3, 'origin': 'human', 'confirmed': True,
                      'reviewer': 'Тест', 'type': 'fixture', 'observed_fact': 'Видно синтетическое действие.',
                      'visibility': 'clear', 'rule_status': 'confirmed', 'rule_id': 'R1',
                      'rule_applicability_confirmed': True}
        rule = {'id': 'R1', 'verified': True, 'document': 'Локальный тестовый документ', 'section': 'Пункт 20',
                'url': 'https://example.org/rule', 'applicability': 'Fixture only', 'visual_cues': ['fixture']}
        _, _, manifest, text = self.render(synthetic_job(), annotations=[annotation], rules=[rule])
        self.assertIn('Локальный тестовый документ', text)
        self.assertEqual(manifest['human_annotations'][0]['rule']['id'], 'R1')
        for mutation in [{'confirmed': False}, {'origin': 'model'}, {'visibility': 'partial'},
                         {'rule_applicability_confirmed': False}, {'rule_status': 'proposed'},
                         {'type': 'unknown'}, {'type': 'violation'}, {'type': 'unconfirmed_event'},
                         {'reviewer': None}, {'observed_fact': ''}]:
            _, _, _, text = self.render(synthetic_job(), annotations=[annotation | mutation], rules=[rule])
            self.assertNotIn('Локальный тестовый документ', text)
            self.assertIn('Нормативное основание не установлено', text)

    def test_incomplete_job_and_corrupt_evidence_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            for state in ['processing', 'failed']:
                with self.assertRaises(ValueError):
                    reports.generate_report(synthetic_job() | {'status': state}, temporary)
            job = synthetic_job()
            job['result']['violations'][0]['evidence_frames'][0]['image_base64'] = 'not jpeg'
            with self.assertRaises(ValueError):
                reports.generate_report(job, temporary)

    def test_many_events_long_descriptions_split_across_pages(self):
        job = synthetic_job()
        job['result']['violations'] *= 4
        job['review']['events'] *= 4
        job['result']['violations'][0]['description'] = ('Длинное описание синтетического события. ' * 100)
        artifacts, _, _, text = self.render(job)
        self.assertGreater(len(PdfReader(artifacts.pdf_path).pages), 3)
        self.assertIn('Событие 4', text)
        self.assertIn('Подпись', text)

    def test_network_unavailable_and_user_text_cannot_leak_local_paths_or_secrets(self):
        job = synthetic_job()
        job['filename'] = r'C:\private\Қазақша.mov'
        job['result']['violations'][0]['description'] = 'action <b> api_key=hidden-value D:/private/model.pt /home/user/secret.txt'
        with patch('socket.create_connection', side_effect=AssertionError('No network permitted')):
            _, raw, _, text = self.render(job)
        for secret in ['hidden-value', 'D:/private', '/home/user', 'C:\\private']:
            self.assertNotIn(secret, raw.decode() + text)
        self.assertIn('Қазақша.mov', text)
        self.assertIn('<b>', text)

    def test_pdf_retains_actual_submillisecond_evidence_timestamp(self):
        job = synthetic_job()
        frame = job['result']['violations'][0]['evidence_frames'][0]
        frame['timestamp_sec'] = 1.123456
        _, _, manifest, text = self.render(job)
        self.assertEqual(manifest['events'][0]['evidence_frames'][0]['timestamp_sec'], 1.123456)
        self.assertIn('1.123456 с', text)

    def test_optional_video_and_type_classifier_hashes_are_preserved(self):
        job = synthetic_job()
        optional = {'video_classifier_sha256': '1' * 64, 'video_classifier_metadata_sha256': '2' * 64,
                    'type_classifier_sha256': '3' * 64}
        job['review']['version_snapshot']['model'].update(optional)
        _, _, manifest, text = self.render(job)
        for key, value in optional.items():
            self.assertEqual(manifest['versions']['model'][key], value)
            self.assertIn(value, text.replace('\n', '').replace(' ', ''))


if __name__ == '__main__':
    unittest.main()
