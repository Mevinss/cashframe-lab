"""Local FFmpeg decoding; timestamps refer to the first original video frame."""
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
import base64
import json
import subprocess
import tempfile

import numpy as np


@dataclass(frozen=True)
class VideoInfo:
    duration_sec: float
    width: int
    height: int
    start_sec: float
    codec: str


def probe_video(path):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], capture_output=True, timeout=30, check=True)
        info = json.loads(result.stdout)
        stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
        start = float(stream.get('start_time', 0))
        duration = max(float(stream.get('duration', 0)), float(info['format']['duration']) - start)
        if not 0 < duration < 36000 or stream['width'] * stream['height'] > 3840 * 2160:
            raise ValueError('Unsupported video duration or dimensions')
        return VideoInfo(duration, stream['width'], stream['height'], start, stream['codec_name'])
    except (subprocess.SubprocessError, KeyError, StopIteration, json.JSONDecodeError) as exc:
        raise ValueError('Video container cannot be read') from exc


def validate_decode(path, timeout=180):
    probe_video(path)
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-i', str(path), '-map', '0:v:0', '-f', 'null', '-'], capture_output=True, timeout=timeout)
    if result.returncode or result.stderr.strip():
        raise ValueError('Video is damaged or cannot be fully decoded')


def frame_times(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'csv=p=0', str(path)], capture_output=True, text=True, timeout=90, check=True)
    times = []
    for line in result.stdout.splitlines():
        first = line.split(',')[0].strip()
        if first:
            try:
                times.append(float(first))
            except ValueError as exc:
                raise ValueError('Video frame timestamps are missing') from exc
    if not times or any(a >= b for a, b in zip(times, times[1:])):
        raise ValueError('Video frame timestamps are not strictly increasing')
    return times


def iter_samples(path, sample_fps=4., size=224):
    if not 0 < sample_fps <= 30 or size < 8:
        raise ValueError('Invalid sampling configuration')
    times = frame_times(path)
    period = 1 / sample_fps - 1e-6
    selected, previous = [], -float('inf')
    for timestamp in times:
        if timestamp - previous >= period:
            selected.append(timestamp - times[0])
            previous = timestamp
    select = f"select=isnan(prev_selected_t)+gte(t-prev_selected_t\\,{period}),scale={size}:{size}"
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-copyts', '-threads', '1', '-i', str(path), '-an', '-vf', select,
               '-fps_mode', 'vfr', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
    with tempfile.TemporaryFile() as error_log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=error_log)
        try:
            for timestamp in selected:
                data = bytearray()
                while len(data) < size * size * 3:
                    chunk = process.stdout.read(size * size * 3 - len(data))
                    if not chunk:
                        raise ValueError('Sample decode ended before expected timestamp')
                    data.extend(chunk)
                yield timestamp, np.frombuffer(data, dtype=np.uint8).reshape(size, size, 3).copy()
            if process.stdout.read(1):
                raise ValueError('Decoded sample timestamps do not match frame count')
            process.wait(timeout=30)
            error_log.seek(0)
            if process.returncode or error_log.read().strip():
                raise ValueError('Video sample decoding failed')
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()


def evidence_frame(path, timestamp_sec):
    times = frame_times(path)
    index = min(len(times) - 1, bisect_left(times, times[0] + max(0, timestamp_sec)))
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-i', str(path), '-an',
               '-vf', f'select=eq(n\\,{index})', '-frames:v', '1', '-q:v', '2', '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1']
    result = subprocess.run(command, capture_output=True, timeout=45, check=True)
    if not result.stdout.startswith(b'\xff\xd8'):
        raise ValueError('Evidence extraction did not return JPEG')
    return {'timestamp_sec': round(times[index] - times[0], 6), 'image_base64': base64.b64encode(result.stdout).decode('ascii')}


def browser_copy(path, output):
    output = Path(output)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-i', str(path), '-map', '0:v:0', '-an',
                    '-vf', 'setpts=PTS-STARTPTS', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '24', '-threads', '1',
                    '-movflags', '+faststart', '-y', str(output)], capture_output=True, check=True, timeout=90)
    return output
