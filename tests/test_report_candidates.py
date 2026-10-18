"""Synthetic-only checks for non-confirmed catalog references in offline reports."""
import base64
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
from pypdf import PdfReader

from cashframe.reports import _manifest, generate_report
from cashframe.reporting import report_files


def fixture():
    image = BytesIO()
    Image.new('RGB', (320, 180), '#17646a').save(image, format='JPEG')
    raw = image.getvalue()
    event = {'id': 'event-1', 'start_sec': 1, 'end_sec': 3, 'timecode_sec': 2,
             'type': 'leave_before_service_end', 'localization_status': 'model_proposal',
             'type_prediction': {'type': 'leave_before_service_end', 'origin': 'automatic', 'confirmed': False}}
    job = {'job_id': 'synthetic-candidate', 'status': 'done', 'source_sha256': 'a'*64,
           'filename': 'synthetic.mov', 'duration_sec': 10,
           'review': {'events': [event], 'duration_sec': 10},
           'result': {'verdict': 'violation', 'violations': [
               {'type': event['type'], 'timecode_sec': 2, 'description': 'Synthetic model candidate only.',
                'evidence_frames': [{'timestamp_sec': 2, 'image_base64': base64.b64encode(raw).decode()}]}]}}
    rule = {'id': 'RK231-22', 'verified': True, 'url': 'https://adilet.zan.kz/rus/docs/V1900019680',
            'title': 'Synthetic rule reference', 'document': 'Synthetic NB RK source', 'section': '22',
            'applicability': 'Customer service context must be established',
            'limits': 'Leaving the frame is insufficient', 'visual_cues': ['Actual departure'],
            'verified_on': '2026-10-18'}
    catalog = {'classes': [{'id': 'leave_before_service_end', 'labels': {'en': 'Departure'},
                            'candidate_rule_ids': ['RK231-22'],
                            'requirements': {'ru': ['Синтетическое условие RU.'],
                                             'kk': ['Синтетикалық шарт KK.'], 'en': ['Synthetic condition EN.']},
                            'limits': {'ru': 'Ограничение RU.', 'kk': 'Шектеу KK.', 'en': 'Limitation EN.'}}]}
    return job, [rule], catalog, raw


class ReportCandidateTests(unittest.TestCase):
    def test_candidate_reference_is_not_confirmed_and_uses_each_report_language(self):
        job, rules, catalog, raw = fixture()
        headings = {'ru': 'Источник для проверки', 'kk': 'Тексеруге арналған дереккөз', 'en': 'Reference for review'}
        conditions = {'ru': 'Синтетическое условие RU.', 'kk': 'Синтетикалық шарт KK.', 'en': 'Synthetic condition EN.'}
        for language in ('ru', 'kk', 'en'):
            with self.subTest(language=language), TemporaryDirectory() as temporary:
                artifacts = generate_report(job, temporary, rules=rules, language=language,
                                            violation_catalog=catalog)
                manifest = json.loads(artifacts.passport_path.read_text(encoding='utf-8'))
                candidate, = manifest['events'][0]['candidate_rules']
                self.assertEqual(candidate['status'], 'candidate')
                self.assertEqual(candidate['applicability_status'], 'not_confirmed')
                self.assertEqual(candidate['rule']['url'], 'https://adilet.zan.kz/rus/docs/V1900019680')
                self.assertFalse(manifest['events'][0]['type_confirmed'])
                self.assertEqual(manifest['human_annotations'], [])
                self.assertEqual(manifest['events'][0]['evidence_frames'][0]['sha256'], hashlib.sha256(raw).hexdigest())
                reader = PdfReader(artifacts.pdf_path)
                text = '\n'.join(page.extract_text() for page in reader.pages)
                self.assertIn(headings[language], text)
                self.assertIn(conditions[language], text)
                self.assertIn('Synthetic NB RK source', text)
                self.assertIn('Departure', text)
                self.assertIn('https://adilet.zan.kz/rus/docs/V1900019680', text.replace('\n', ''))
                image_bytes = []
                for page in reader.pages:
                    for item in (page['/Resources'].get('/XObject') or {}).values():
                        obj = item.get_object()
                        if obj.get('/Subtype') == '/Image':
                            image_bytes.append(obj.get_data())
                self.assertIn(raw, image_bytes)

    def test_catalog_change_invalidates_report_cache_and_changes_passport(self):
        job, rules, catalog, _ = fixture()
        with TemporaryDirectory() as temporary:
            first, _ = report_files(job, Path(temporary), rules, [], violation_catalog=catalog)
            changed = copy.deepcopy(catalog)
            changed['classes'][0]['requirements']['en'] = ['Another independently reviewed condition.']
            second, _ = report_files(job, Path(temporary), rules, [], violation_catalog=changed)
            self.assertNotEqual(first, second)
            self.assertNotEqual((first/'passport.json').read_bytes(), (second/'passport.json').read_bytes())

    def test_unknown_signal_or_unverified_rule_cannot_leak_candidate_source(self):
        for mutation in ('unknown', 'unverified', 'unlocalized'):
            job, rules, catalog, _ = fixture()
            if mutation == 'unknown':
                job['review']['events'][0].pop('type_prediction')
            elif mutation == 'unverified':
                rules[0]['verified'] = False
            else:
                job['review']['events'][0]['localization_status'] = 'unresolved'
            with self.subTest(mutation=mutation), TemporaryDirectory() as temporary:
                artifacts = generate_report(job, temporary, rules=rules, violation_catalog=catalog)
                manifest = json.loads(artifacts.passport_path.read_text(encoding='utf-8'))
                self.assertEqual(manifest['events'][0]['candidate_rules'], [])
                text = '\n'.join(page.extract_text() for page in PdfReader(artifacts.pdf_path).pages)
                self.assertNotIn('Synthetic NB RK source', text)

    def test_existing_manifest_positional_language_and_no_catalog_remain_supported(self):
        job, rules, _, _ = fixture()
        manifest, _ = _manifest(job, None, rules, [], 'en')
        self.assertEqual(manifest['language'], 'en')
        self.assertEqual(manifest['events'][0]['candidate_rules'], [])


if __name__ == '__main__':
    unittest.main()
