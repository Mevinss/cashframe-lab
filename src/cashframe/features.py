"""Frozen DINOv2 features. Network access is allowed only in prepare_encoder."""
from dataclasses import dataclass
from pathlib import Path
import json
import shutil
import time
import warnings

import numpy as np

from .data import read_json, sha256_file, write_json
from .video import iter_samples,frame_times,sample_timestamps
from .numerics import finite_array

REVISION = '7764ea0f912e53c92e82eb78a2a1631e92725fc8'
MODEL_NAME = 'dinov2_vits14'


@dataclass
class FeatureSequence:
    times: np.ndarray
    values: np.ndarray
    wall_sec: float
    source_times: list | None = None
    observations: list | None = None


def prepare_encoder(output_dir):
    """Explicit preparation/download step, never called from analysis."""
    import torch
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        model = torch.hub.load(f'facebookresearch/dinov2:{REVISION}', MODEL_NAME, pretrained=True, trust_repo=True, verbose=False)
    del model
    hub = Path(torch.hub.get_dir())
    source = hub / f'facebookresearch_dinov2_{REVISION}'
    shutil.copytree(source, output_dir / 'source', dirs_exist_ok=True)
    weights = hub / 'checkpoints' / 'dinov2_vits14_pretrain.pth'
    shutil.copy2(weights, output_dir / 'weights.pth')
    metadata = {'model': MODEL_NAME, 'revision': REVISION, 'weights_sha256': sha256_file(output_dir / 'weights.pth'),
                'license': 'Apache-2.0', 'input_size': 168, 'sample_fps': 4., 'threads': 2,
                'source_files': {str(p.relative_to(output_dir / 'source')): sha256_file(p) for p in sorted((output_dir / 'source').rglob('*.py'))}}
    write_json(output_dir / 'encoder.json', metadata)
    return metadata


class Encoder:
    def __init__(self, encoder_dir):
        import torch
        self.torch = torch
        self.path = Path(encoder_dir)
        self.config = read_json(self.path / 'encoder.json')
        if sha256_file(self.path / 'weights.pth') != self.config['weights_sha256']:
            raise ValueError('Encoder weights hash mismatch')
        for relative, digest in self.config['source_files'].items():
            source_root=(self.path / 'source').resolve()
            source_file=(source_root / relative.replace('\\','/')).resolve()
            if source_root not in source_file.parents or sha256_file(source_file) != digest:
                raise ValueError('Encoder source hash mismatch')
        torch.set_num_threads(self.config['threads'])
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            self.model = torch.hub.load(str(self.path / 'source'), MODEL_NAME, source='local', pretrained=False).eval()
        state = torch.load(self.path / 'weights.pth', map_location='cpu', weights_only=True)
        self.model.load_state_dict(state)
        self.mean = torch.tensor([.485, .456, .406]).view(1, 3, 1, 1)
        self.std = torch.tensor([.229, .224, .225]).view(1, 3, 1, 1)

    def encode(self, image):
        torch = self.torch
        x = torch.from_numpy(image).permute(2, 0, 1).unsqueeze(0).float().div_(255)
        with torch.inference_mode():
            vector = self.model((x - self.mean) / self.std).numpy()[0]
        return finite_array(vector, 'DINOv2 embedding', 1).astype(np.float32)


def extract_features(path, encoder, sample_fps=None, progress_callback=None):
    from .visual_quality import inspect_sample
    start = time.perf_counter()
    times, vectors = [], []
    fps = sample_fps or encoder.config['sample_fps']
    source_times=frame_times(path)
    total=len(sample_timestamps(source_times,fps))
    if progress_callback:progress_callback(0,total,'samples')
    observations=[];previous=None;previous_time=None
    for timestamp, image in iter_samples(path, fps, encoder.config['input_size'],timestamps=source_times):
        previous,quality=inspect_sample(image,previous,timestamp-previous_time if previous_time is not None else None)
        observations.append({'timestamp_sec':float(timestamp),**quality});previous_time=timestamp
        times.append(timestamp)
        vectors.append(encoder.encode(image))
        if progress_callback:progress_callback(len(vectors),total,'samples')
    if not vectors:
        raise ValueError('No visual samples could be extracted')
    return FeatureSequence(np.asarray(times, dtype=np.float64), np.stack(vectors), time.perf_counter() - start,source_times,observations)


def extract_dataset(manifest_path, encoder_dir, output_dir):
    import psutil
    encoder = Encoder(encoder_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {'encoder': encoder.config, 'videos': []}
    meta_path = output_dir / 'metadata.json'
    if meta_path.exists():
        old = read_json(meta_path)
        if old['encoder'] != encoder.config:
            raise ValueError('Feature cache encoder/config mismatch')
        metadata = old
    existing = {v['group_id'] for v in metadata['videos']}
    for group in read_json(manifest_path):
        target = output_dir / (group['group_id'] + '.npz')
        if target.exists() and group['group_id'] in existing:
            continue
        if sha256_file(group['path']) != group['sha256']:
            raise ValueError('Original video hash changed')
        sequence = extract_features(group['path'], encoder)
        np.savez_compressed(target, times=sequence.times, values=sequence.values)
        row = {'group_id': group['group_id'], 'samples': len(sequence.times), 'wall_sec': round(sequence.wall_sec, 3),
               'rss_mb': round(psutil.Process().memory_info().rss / 1e6, 1), 'duration_sec': group['duration_sec']}
        metadata['videos'].append(row)
        write_json(meta_path, metadata)
        print(json.dumps({'completed': len(metadata['videos']), 'total': len(read_json(manifest_path)), **row}), flush=True)
    return metadata
