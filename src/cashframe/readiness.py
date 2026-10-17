"""Load and exercise the actual offline artifacts in an isolated process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import numpy as np


def validate_runtime(model_dir, encoder_dir):
    from .features import Encoder
    from .learning import load_model, model_scores, window_features, inference_window_parameters, video_score
    from .numerics import finite_scalar
    from .pipeline import block_outgoing_network
    block_outgoing_network()
    for executable in ('ffmpeg', 'ffprobe'):
        subprocess.run([executable, '-version'], capture_output=True, check=True, timeout=10)
    model, config, threshold = load_model(model_dir)
    encoder = Encoder(encoder_dir)
    if encoder.config != config['encoder']:
        raise ValueError('Classifier and encoder metadata mismatch')
    vector = encoder.encode(np.zeros((encoder.config['input_size'], encoder.config['input_size'], 3), dtype=np.uint8))
    _, windows = window_features(np.array([0., .5]), np.stack([vector, vector]), 1., **inference_window_parameters(config))
    model_scores(model, windows)
    finite_scalar(video_score(model, windows), 'readiness video score')
    if config.get('video_classifier'):
        if config['video_classifier'] != 'selected.npz':
            raise ValueError('Unsupported video classifier artifact')
        from .research import load_frozen, score_videos
        candidate, candidate_threshold, _ = load_frozen(model_dir, expected_encoder=encoder.config)
        finite_scalar(candidate_threshold, 'video threshold')
        finite_scalar(score_videos(candidate, [windows])[0], 'video score')
    if (Path(model_dir) / 'types.npz').exists():
        from .taxonomy import classify_interval
        classify_interval(Path(model_dir) / 'types.npz', np.array([0., .5]), np.stack([vector, vector]), 0., .5, encoder_config=encoder.config)
    return {'ready': True, 'encoder': encoder.config['model'], 'feature_dimensions': len(vector)}


class RuntimeReadiness:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.fingerprint = None
        self.valid = False
        self.checked_at = 0.

    def check(self):
        with self.lock:
            paths = [self.settings.model_dir, self.settings.local / 'encoder']
            signature = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns)
                              for directory in paths for p in sorted(directory.rglob('*'))
                              if p.is_file() and p.suffix in ('.json', '.npz', '.pth', '.py'))
            if signature == self.fingerprint and (self.valid or time.monotonic() - self.checked_at < 5):
                return self.valid
            self.valid = False
            log_dir = self.settings.local
            log_dir.mkdir(parents=True, exist_ok=True)
            try:
                environment = dict(os.environ, PYTHONPATH=str(self.settings.root / 'src'), PYTHONIOENCODING='utf-8')
                options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
                with (log_dir / 'readiness.log').open('wb') as log:
                    process = subprocess.run([sys.executable, '-m', 'cashframe.readiness',
                                              str(self.settings.model_dir), str(self.settings.local / 'encoder')],
                                             stdout=log, stderr=log, env=environment, timeout=60, **options)
                self.valid = process.returncode == 0
            except (OSError, subprocess.SubprocessError):
                self.valid = False
            self.fingerprint, self.checked_at = signature, time.monotonic()
            return self.valid


if __name__ == '__main__':
    print(json.dumps(validate_runtime(*sys.argv[1:3])))
