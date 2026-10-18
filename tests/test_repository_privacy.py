"""Repository boundaries, checked without creating or reading private artifacts."""
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RepositoryPrivacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('git'):
            raise unittest.SkipTest('Git is required to verify repository ignore rules')
        cls.directory = TemporaryDirectory()
        cls.repository = Path(cls.directory.name)
        (cls.repository / '.gitignore').write_bytes((ROOT / '.gitignore').read_bytes())
        subprocess.run(['git', 'init', '--quiet'], cwd=cls.repository, check=True,
                       capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def ignored(self, paths):
        result = subprocess.run(
            ['git', '-c', 'core.excludesFile=', '-c', 'core.ignoreCase=false',
             'check-ignore', '--no-index', '-z', '--stdin'],
            input=('\0'.join(paths) + '\0').encode('utf-8'), capture_output=True,
            cwd=self.repository,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return {path for path in result.stdout.decode('utf-8').split('\0') if path}

    def test_private_data_and_credentials_remain_local(self):
        paths = [
            'local/api-key.txt', 'local/jury-access.md', 'local/dataset/manifest.json',
            'data/events.csv', 'models/profile/config.json', 'uploads/input.MOV',
            'jobs/result.json', 'runs/output.json', 'private/team.md',
            'secrets/access.txt', 'artifacts/passport.json', 'evidence/frame.png',
            'exports/annotations.json', 'screenshots/capture.png',
            'recordings/capture.webm', '.env', '.env.local', '.env.production',
            'docs/jury-access.md', 'docs/api-key-jury.txt', 'docs/access.private.md',
            'docs/access.local.md', 'dataset/events-review-v2.csv',
            'dataset/review-coverage.csv',
        ]
        self.assertEqual(self.ignored(paths), set(paths))

    def test_media_bundles_and_diagnostic_captures_are_ignored(self):
        paths = [
            'sample.MOV', 'sample.MP4', 'sample.avi', 'sample.mkv',
            'sample.webm', 'sample.wav', 'source.xlsx', 'source.xlsm',
            'weights.pth', 'weights.pt', 'weights.onnx', 'features.npz',
            'model.pkl', 'model.joblib', 'jobs.sqlite-wal', 'session.db',
            'handoff.zip', 'evidence.7z', 'archive.tar.gz', 'service.log',
            'session.har', 'trace.zip', 'server.key', 'server.pem',
            'client.p12', '.venv-detector/package.py', '.cache/probe.json',
        ]
        self.assertEqual(self.ignored(paths), set(paths))

    def test_reproducible_code_and_public_assets_remain_visible(self):
        paths = [
            '.env.example', '.github/workflows/checks.yml', 'README.md',
            'docs/architecture.md', 'docs/deployment.md',
            'docs/jury-access-and-classification.md',
            'docs/event-annotation-template.csv', 'config/rules.json',
            'deploy/cashframe.service', 'src/cashframe/api.py',
            'tests/test_api.py', 'web/index.html', 'web/brand/favicon.svg',
            'web/brand/hero.png', 'src/cashframe/assets/fonts/DejaVuSans.ttf',
        ]
        self.assertEqual(self.ignored(paths), set())

    def test_already_tracked_files_do_not_cross_ignore_boundary(self):
        if not (ROOT / '.git').exists():
            self.skipTest('Source archive has no Git index')
        result = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT,
                                capture_output=True, check=True)
        paths = result.stdout.decode('utf-8').rstrip('\0').split('\0')
        self.assertEqual(self.ignored(paths), set(),
                         'An ignored private artifact is already tracked; untrack it while preserving local data')


if __name__ == '__main__':
    unittest.main()
