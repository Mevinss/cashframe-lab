"""Localized report verification uses generated JPEGs, never bank evidence."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pypdf import PdfReader
from cashframe.reports import TITLE, _CATALOG, _manifest, generate_report
from cashframe.reporting import report_files
from test_reports import synthetic_job


class ReportLocaleTests(unittest.TestCase):
    def render(self, job, language):
        with TemporaryDirectory() as folder:
            artifacts = generate_report(job, folder, language=language)
            passport = json.loads(artifacts.passport_path.read_bytes())
            reader = PdfReader(artifacts.pdf_path)
            return passport, '\n'.join(page.extract_text() for page in reader.pages), reader.metadata.title

    def test_all_languages_have_labels_title_unknowns_limits_and_api_verdict(self):
        for lang in ('ru', 'kk', 'en'):
            for positive in (True, False):
                job = synthetic_job(positive)
                # Deliberately contradictory event counts must not change verdict.
                if positive:
                    job['result']['violations'] = []
                else:
                    job['result']['violations'] = synthetic_job()['result']['violations']
                passport, text, metadata_title = self.render(job, lang)
                verdict = job['result']['verdict']
                self.assertEqual(passport['language'], lang)
                self.assertEqual(passport['verdict'], verdict)
                for key in ('title', verdict, 'unset', 'limitations', 'metadata_missing', 'review_notice'):
                    self.assertIn(' '.join(_CATALOG[key][lang].split()), ' '.join(text.split()))
                self.assertEqual(metadata_title, _CATALOG['title'][lang])
                if lang != 'ru':
                    self.assertNotIn('Нормативное основание не установлено', text)
                    self.assertNotIn('Не установлено / не сохранено', text)
        self.assertEqual(_CATALOG['title']['ru'], TITLE)

    def test_unresolved_is_context_without_claiming_zero_as_event_time(self):
        job = synthetic_job()
        job['result']['violations'][0]['timecode_sec'] = 0
        job['review']['events'][0]['localization_status'] = 'unresolved'
        for language in ('ru', 'kk', 'en'):
            passport, text, _ = self.render(job, language)
            self.assertIn(_CATALOG['unresolved'][language], text)
            self.assertIn(_CATALOG['context'][language], text)
            self.assertNotIn(_CATALOG['timecode'][language] + ':', text)
            event = passport['events'][0]
            self.assertEqual(event['timecode_role'], 'context')
            self.assertEqual({f['role'] for f in event['evidence_frames']}, {'context'})

    def test_source_content_and_jpeg_hashes_unchanged_with_explicit_language_boundary(self):
        job = synthetic_job()
        ru, _ = _manifest(job, None, [], [], 'ru')
        for language in ('kk', 'en'):
            passport, text, _ = self.render(job, language)
            self.assertIn('Модель <сигнал> & проверка', text)
            self.assertIn(_CATALOG['source_boundary'][language], text)
            self.assertEqual(passport['events'][0]['model_description'], ru['events'][0]['model_description'])
            self.assertEqual([f['sha256'] for f in passport['events'][0]['evidence_frames']],
                             [f['sha256'] for f in ru['events'][0]['evidence_frames']])

    def test_locale_changes_immutable_revision_and_concurrent_calls_do_not_leak_language(self):
        with TemporaryDirectory() as folder:
            job = synthetic_job()
            def build(lang):
                path, integrity = report_files(copy.deepcopy(job), folder, [], [], lang)
                raw = (path / 'passport.json').read_bytes()
                return path, json.loads(raw), integrity, hashlib.sha256(raw).hexdigest()
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(build, ('ru', 'kk', 'en')))
            self.assertEqual(len({r[0] for r in results}), 3)
            self.assertEqual(len({r[1]['passport_id'] for r in results}), 3)
            for lang, (path, passport, integrity, digest) in zip(('ru', 'kk', 'en'), results):
                self.assertEqual(passport['language'], lang)
                self.assertEqual(passport['verdict_label'], _CATALOG['violation'][lang])
                self.assertEqual(integrity['passport.json'], digest)
                self.assertEqual(build(lang)[0], path)

    def test_unsupported_languages_rejected_before_writing(self):
        for language in ('de', '', None, 'RU', ['ru']):
            with TemporaryDirectory() as folder, self.assertRaises(ValueError):
                generate_report(synthetic_job(), folder, language=language)
            with TemporaryDirectory() as folder, self.assertRaises(ValueError):
                report_files(synthetic_job(), folder, [], [], language)

    def test_point_localizer_hashes_and_guard_are_preserved(self):
        job = synthetic_job()
        hashes = {'point_localizer_sha256': '4' * 64, 'point_localizer_metadata_sha256': '5' * 64}
        parameters = {'point_localizer_guard': 'synthetic safety guard',
                      'point_localizer_score_kind': 'uncalibrated temporal proposal score'}
        job['review']['version_snapshot']['model'].update(hashes)
        job['review']['version_snapshot']['parameters'].update(parameters)
        passport, text, _ = self.render(job, 'en')
        for key, value in hashes.items():
            self.assertEqual(passport['versions']['model'][key], value)
            self.assertIn(value, ''.join(text.split()))
        for key, value in parameters.items():
            self.assertEqual(passport['versions']['parameters'][key], value)
            self.assertIn(value, text)

    def test_numeric_compatibility_and_structured_guards_are_in_passport(self):
        job=synthetic_job()
        expected={'window_aggregation':'input-dtype-preserved','threshold_roundoff_epsilon_multiplier':32,
                  'localization_guard':{'flat_range':1e-6,'max_coverage_fraction':.8,'max_events':12}}
        job['review']['version_snapshot']['parameters'].update(expected)
        passport,_,_=self.render(job,'ru')
        for key,value in expected.items():self.assertEqual(passport['versions']['parameters'][key],value)


if __name__ == '__main__':
    unittest.main()
