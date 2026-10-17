from pathlib import Path
from tempfile import TemporaryDirectory
import base64
import io
import subprocess
import unittest
from unittest.mock import patch

from PIL import Image
from cashframe.video import probe_video, iter_samples, evidence_frame, validate_decode,browser_copy,frame_times


class VideoTests(unittest.TestCase):
    def test_batch_evidence_matches_single_frame_pts_and_decodes_once(self):
        from cashframe.video import evidence_frames,frame_times
        from unittest.mock import patch
        import cashframe.video as module
        requested=[0,.5,.9,.5]
        times=frame_times(self.path)
        expected=[evidence_frame(self.path,t,times=times) for t in requested]
        with patch('cashframe.video.subprocess.run',wraps=module.subprocess.run) as run:
            actual=evidence_frames(self.path,requested,times=times)
        self.assertEqual(run.call_count,1)
        self.assertEqual(actual,expected)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.path = Path(self.temp.name) / 'sample.mov'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x128:rate=12:duration=2', '-c:v', 'libx264', '-y', str(self.path)], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_sampling_uses_source_timestamps(self):
        info=probe_video(self.path)
        self.assertEqual(info.frame_rate_mode,'constant')
        self.assertAlmostEqual(info.average_fps,12)
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

    def test_browser_h264_copy_preserves_compressed_stream_and_timestamps(self):
        preview=self.path.with_suffix('.mp4');browser_copy(self.path,preview)
        def bitstream(path):
            return subprocess.check_output(['ffmpeg','-nostdin','-v','error','-i',str(path),'-map','0:v:0','-c:v','copy','-bsf:v','h264_mp4toannexb','-f','h264','pipe:1'])
        self.assertEqual(bitstream(self.path),bitstream(preview))
        self.assertEqual(frame_times(self.path),frame_times(preview))

    def test_browser_copy_normalizes_delayed_video_despite_earlier_audio(self):
        source=self.path.with_name('delayed.mov');preview=self.path.with_name('delayed.mp4')
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=64x64:r=12:d=2',
                        '-f','lavfi','-i','anullsrc=r=8000:cl=mono','-filter:v','setpts=PTS+2/TB','-c:v','libx264','-c:a','aac','-t','4',str(source)],check=True)
        self.assertGreaterEqual(probe_video(source).start_sec,1.9)
        browser_copy(source,preview)
        before,after=frame_times(source),frame_times(preview)
        self.assertAlmostEqual(after[0],0,places=4)
        self.assertEqual(len(before),len(after))
        for a,b in zip(before,after):self.assertAlmostEqual(a-before[0],b,places=4)

    def test_reuses_source_timestamps_without_redecoding_for_each_evidence_frame(self):
        stamps=frame_times(self.path)
        expected=evidence_frame(self.path,1.1)
        with patch('cashframe.video.frame_times',side_effect=AssertionError('redundant timestamp decode')):
            samples=list(iter_samples(self.path,4,32,timestamps=stamps))
            actual=evidence_frame(self.path,1.1,times=stamps)
        self.assertEqual(len(samples),8)
        self.assertEqual(actual,expected)

    def test_vfr_and_nonzero_start(self):
        other = self.path.with_name('offset.mov')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(self.path), '-vf', "select='not(mod(n,3))+eq(mod(n,5),1)',setpts=PTS+2/TB", '-fps_mode', 'vfr', '-c:v', 'libx264', '-y', str(other)], check=True)
        info = probe_video(other)
        self.assertGreaterEqual(info.start_sec, 1.9)
        self.assertEqual(info.frame_rate_mode,'variable')
        samples = list(iter_samples(other, 4, 24))
        self.assertAlmostEqual(samples[0][0], 0, places=3)
        self.assertTrue(all(a[0] < b[0] for a, b in zip(samples, samples[1:])))
        self.assertLess(samples[-1][0], info.duration_sec)
        preview=self.path.with_name('offset.mp4');browser_copy(other,preview)
        original,converted=frame_times(other),frame_times(preview)
        self.assertEqual(len(original),len(converted))
        for a,b in zip(original,converted):self.assertAlmostEqual(a-original[0],b,places=4)
