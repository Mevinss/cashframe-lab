from pathlib import Path
from tempfile import TemporaryDirectory
import base64
import io
import subprocess
import unittest

from PIL import Image
from cashframe.video import probe_video, iter_samples, evidence_frame, validate_decode


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'sample.mov'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x128:rate=12:duration=2', '-c:v', 'libx264', '-y', str(self.path)], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_sampling_uses_source_timestamps(self):
        samples = list(iter_samples(self.path, 4, 32))
        self.assertEqual(len(samples), 8)
        self.assertEqual([round(t, 2) for t, _ in samples], [0, .25, .5, .75, 1, 1.25, 1.5, 1.75])
        self.assertTrue(all(frame.shape == (32, 32, 3) for _, frame in samples))

    def test_original_evidence_is_jpeg_and_time_in_bounds(self):
        result = evidence_frame(self.path, 1.1)
        self.assertGreaterEqual(result['timestamp_sec'], 1.1)
        self.assertLess(result['timestamp_sec'], 1.2)
        image = Image.open(io.BytesIO(base64.b64decode(result['image_base64'])))
        self.assertEqual(image.format, 'JPEG')
        self.assertEqual(image.size, (96, 128))

    def test_truncated_video_rejected(self):
        broken = self.path.with_name('broken.mov')
        broken.write_bytes(self.path.read_bytes()[:200])
        with self.assertRaises(ValueError):
            validate_decode(broken)

    def test_vfr_and_nonzero_start(self):
        other = self.path.with_name('offset.mov')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(self.path), '-vf', "select='not(mod(n,3))+eq(mod(n,5),1)',setpts=PTS+2/TB", '-fps_mode', 'vfr', '-c:v', 'libx264', '-y', str(other)], check=True)
        info = probe_video(other)
        self.assertGreaterEqual(info.start_sec, 1.9)
        samples = list(iter_samples(other, 4, 24))
        self.assertAlmostEqual(samples[0][0], 0, places=3)
        self.assertTrue(all(a[0] < b[0] for a, b in zip(samples, samples[1:])))
        self.assertLess(samples[-1][0], info.duration_sec)
