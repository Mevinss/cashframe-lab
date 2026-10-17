"""Read source labels without modifying the workbook or original videos."""
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile
import hashlib
import json
import random
import re
import subprocess
import unicodedata
import xml.etree.ElementTree as ET


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def canonical_id(name):
    return unicodedata.normalize('NFKC', Path(name).stem).strip().upper().replace('Х', 'X')


def parse_timecode(raw):
    if raw is None or str(raw).strip() == '':
        return None
    text = str(raw).strip().lower()
    if ':' in text:
        parts = [float(x) for x in text.split(':')]
        if len(parts) not in (2, 3) or any(x < 0 for x in parts):
            raise ValueError(f'Invalid timecode: {raw}')
        value = 0
        for part in parts:
            value = value * 60 + part
        return value
    nums = re.findall(r'\d+(?:[.,]\d+)?', text)
    if not nums:
        raise ValueError(f'Invalid timecode: {raw}')
    nums = [float(n.replace(',', '.')) for n in nums]
    if len(nums) == 1:
        return nums[0] * (60 if 'мин' in text else 1)
    if len(nums) == 2 and 'мин' in text:
        return nums[0] * 60 + nums[1]
    raise ValueError(f'Ambiguous timecode: {raw}')


def read_labels(path):
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(n.itertext()) for n in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', ns)]
        rows = []
        for row in ET.fromstring(archive.read('xl/worksheets/sheet1.xml')).findall('.//s:row', ns):
            cells = {}
            for cell in row.findall('s:c', ns):
                value = cell.find('s:v', ns)
                value = value.text if value is not None else ''.join(n.text or '' for n in cell.findall('.//s:t', ns))
                if cell.get('t') == 's':
                    value = strings[int(value)]
                cells[re.sub(r'\d', '', cell.get('r'))] = value
            rows.append(cells)
    labels = {}
    for row in rows[1:]:
        if not row.get('A'):
            continue
        raw = row.get('C', '').strip().lower()
        if raw not in ('нарушение', 'без нарушения'):
            raise ValueError(f'Unknown label: {raw}')
        key = canonical_id(row['A'])
        if key in labels:
            raise ValueError(f'Label ID collision: {key}')
        labels[key] = {'label': int(raw == 'нарушение'), 'point_sec': parse_timecode(row.get('B')), 'raw_label': row}
    return labels


def group_records(records):
    grouped = defaultdict(list)
    for record in records:
        grouped[record['sha256']].append(record)
    result = []
    for digest, members in sorted(grouped.items()):
        labels = {m['label'] for m in members}
        if len(labels) != 1:
            raise ValueError('Conflicting labels on an exact duplicate')
        base = dict(members[0])
        base.update(group_id=digest, files=[m['file'] for m in members],
                    points_sec=sorted({m['point_sec'] for m in members if m.get('point_sec') is not None}))
        result.append(base)
    return result


def build_split(manifest, seed=42):
    if any(g.get('label') not in (0, 1) for g in manifest):
        raise ValueError('All source groups need confirmed binary labels')
    result = {'train': [], 'validation': [], 'test': []}
    rng = random.Random(seed)
    for label in (0, 1):
        ids = sorted(g['group_id'] for g in manifest if g['label'] == label)
        if len(ids) < 5:
            raise ValueError('At least five independent groups per class are required')
        rng.shuffle(ids)
        nval = max(1, round(len(ids) * .2))
        ntest = max(1, round(len(ids) * .2))
        result['validation'].extend(ids[:nval])
        result['test'].extend(ids[nval:nval+ntest])
        result['train'].extend(ids[nval+ntest:])
    return result


def audit_dataset(video_dir, labels_path, output_dir):
    video_dir, output_dir = Path(video_dir).resolve(), Path(output_dir)
    labels = read_labels(labels_path)
    records = []
    for path in sorted(video_dir.glob('*.mov')):
        key = canonical_id(path.name)
        if key not in labels:
            raise ValueError(f'No annotation for {path.name}')
        info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=30).stdout)
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        duration = float(info['format']['duration'])
        label = labels[key]
        if label['point_sec'] is not None and not 0 <= label['point_sec'] < duration:
            raise ValueError(f'Timecode out of bounds: {path.name}')
        decode = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-threads', '1', '-i', str(path), '-map', '0:v:0', '-f', 'null', '-'], capture_output=True, timeout=180)
        if decode.returncode or decode.stderr.strip():
            raise ValueError(f'Full decode failed: {path.name}')
        records.append(dict(file=path.name, path=str(path), sha256=sha256_file(path), duration_sec=duration,
                            codec=video['codec_name'], width=video['width'], height=video['height'], **label))
    missing = set(labels) - {canonical_id(r['file']) for r in records}
    if missing:
        raise ValueError(f'Missing source files: {sorted(missing)}')
    groups = group_records(records)
    split = build_split(groups)
    write_json(output_dir / 'manifest.json', groups)
    write_json(output_dir / 'split.json', split)
    summary = dict(files=len(records), unique_groups=len(groups), split_counts={k: len(v) for k, v in split.items()},
                   classes={str(c): sum(g['label'] == c for g in groups) for c in (0, 1)}, seed=42,
                   scene_disjoint_verified=False, types_available=False)
    write_json(output_dir / 'summary.json', summary)
    return summary
