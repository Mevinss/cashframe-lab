from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import unittest

from cashframe.provenance import capture_versions


class ProvenanceTests(unittest.TestCase):
    def test_hashes_actual_artifacts_and_source_without_environment_secrets(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'src/cashframe').mkdir(parents=True)
            (root/'src/cashframe/app.py').write_text('version = 1')
            model = root/'local/experiment';model.mkdir(parents=True)
            encoder = root/'local/encoder';encoder.mkdir(parents=True)
            (model/'trained.npz').write_bytes(b'actual-model')
            (model/'config.json').write_text('{}')
            (encoder/'weights.pth').write_bytes(b'actual-encoder')
            config = {'selected_variant':'trained','thresholds':{'trained':.4},'event_threshold':.3,
                      'encoder':{'model':'test','sample_fps':4},'secret':'must-not-appear'}
            versions = capture_versions(model,encoder,config,root=root)
            self.assertEqual(versions['model']['sha256'],hashlib.sha256(b'actual-model').hexdigest())
            self.assertEqual(versions['model']['encoder_sha256'],hashlib.sha256(b'actual-encoder').hexdigest())
            self.assertEqual(versions['parameters']['video_threshold'],.4)
            self.assertNotIn('must-not-appear',json.dumps(versions))
            before=versions['code']['sha256']
            (root/'src/cashframe/app.py').write_text('version = 2')
            self.assertNotEqual(before,capture_versions(model,encoder,config,root=root)['code']['sha256'])

    def test_optional_classifier_artifacts_are_bound_to_passport(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);model=root/'model';model.mkdir();encoder=root/'encoder';encoder.mkdir()
            for name in ('trained.npz','selected.npz','selection-frozen.json','types.npz'):(model/name).write_bytes(name.encode())
            (model/'config.json').write_text('{}');(encoder/'weights.pth').write_bytes(b'encoder')
            config={'selected_variant':'trained','thresholds':{'trained':.5},'encoder':{},'video_classifier':'selected.npz'}
            versions=capture_versions(model,encoder,config,root=root,video_threshold=-.354)
            self.assertEqual(versions['model']['video_classifier_sha256'],hashlib.sha256(b'selected.npz').hexdigest())
            self.assertEqual(versions['model']['type_classifier_sha256'],hashlib.sha256(b'types.npz').hexdigest())
            self.assertEqual(versions['parameters']['video_threshold'],-.354)

    def test_point_localizer_is_bound_to_analysis_snapshot(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);model=root/'model';model.mkdir();encoder=root/'encoder';encoder.mkdir()
            for name in ('trained.npz','point-localizer.npz','point-localizer.json'):(model/name).write_bytes(name.encode())
            (model/'config.json').write_text('{}');(encoder/'weights.pth').write_bytes(b'encoder')
            config={'selected_variant':'trained','thresholds':{'trained':.5},'encoder':{},'point_localizer':'point-localizer.npz'}
            versions=capture_versions(model,encoder,config,root=root)
            self.assertEqual(versions['model']['point_localizer_sha256'],hashlib.sha256(b'point-localizer.npz').hexdigest())
            self.assertEqual(versions['model']['point_localizer_metadata_sha256'],hashlib.sha256(b'point-localizer.json').hexdigest())


if __name__ == '__main__':unittest.main()
