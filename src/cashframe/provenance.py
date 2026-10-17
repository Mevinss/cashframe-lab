"""Capture artifact identities at analysis time, never from a later installation."""
import hashlib
import json
from pathlib import Path

from .config import ROOT
from .data import sha256_file


def capture_versions(model_dir, encoder_dir, config, *, root=ROOT, video_threshold=None):
    model_dir, encoder_dir, root = Path(model_dir), Path(encoder_dir), Path(root)
    variant = config['selected_variant']
    if variant not in ('trained', 'baseline'):
        raise ValueError('Unsupported model variant')
    sources = {p.relative_to(root).as_posix(): sha256_file(p)
               for p in sorted((root/'src/cashframe').rglob('*.py'))}
    digest = hashlib.sha256(json.dumps(sources,sort_keys=True,separators=(',', ':')).encode()).hexdigest()
    snapshot = {
        'model': {'id': config.get('model_version', f'window-{variant}'),
                  'sha256': sha256_file(model_dir/f'{variant}.npz'),
                  'encoder_sha256': sha256_file(encoder_dir/'weights.pth')},
        'configuration': {'id':'inference-config', 'sha256':sha256_file(model_dir/'config.json')},
        'code': {'version':'0.2.0', 'sha256':digest},
        'parameters': {'sample_fps':config['encoder'].get('sample_fps'), 'refinement_fps':8.,
                       'window_radius_sec':config.get('window_radius_sec',1.),
                       'window_stride_sec':config.get('window_stride_sec',.5),
                       'event_threshold':config.get('event_threshold'),
                       'video_threshold':config['thresholds'][variant]},
    }
    if config.get('video_classifier'):
        if config['video_classifier']!='selected.npz':raise ValueError('Unsupported video classifier artifact')
        snapshot['model']['video_classifier_sha256']=sha256_file(model_dir/'selected.npz')
        snapshot['model']['video_classifier_metadata_sha256']=sha256_file(model_dir/'selection-frozen.json')
        snapshot['model']['id']+='+kernel-video-v2'
        if video_threshold is None:raise ValueError('Effective video classifier threshold is required')
        snapshot['parameters']['video_threshold']=float(video_threshold)
    if (model_dir/'types.npz').exists():snapshot['model']['type_classifier_sha256']=sha256_file(model_dir/'types.npz')
    return snapshot
