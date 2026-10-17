"""Create an offline report using only generated geometric evidence.

Run: python scripts/example_report.py
All generated files stay in git-ignored local/previews. No private videos are read.
"""
import argparse
import base64
from io import BytesIO
from pathlib import Path
import shutil

from PIL import Image, ImageDraw

from cashframe.reports import generate_report


def synthetic_frame(timestamp, position, size=(960, 540)):
    image = Image.new('RGB', size, '#e4efec')
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 180, size[0]-50, size[1]-80), fill='#175d5a')
    draw.rectangle((position, 220, position+120, 300), fill='#efbb54')
    draw.text((55, 45), f'SYNTHETIC EVIDENCE ONLY / t={timestamp:.3f}s', fill='#173d3d')
    draw.text((55, 75), 'Generated shapes; no people, no bank footage', fill='#173d3d')
    stream = BytesIO()
    image.save(stream, 'JPEG', quality=90)
    return {'timestamp_sec': timestamp, 'image_base64': base64.b64encode(stream.getvalue()).decode('ascii')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('local/previews'))
    args = parser.parse_args()
    evidence = [synthetic_frame(t, x) for t, x in [(1, 160), (2, 360), (3, 560)]]
    job = {'status': 'done', 'job_id': 'synthetic-demonstration-only', 'filename': 'Синтетический пример - Қазақша ӘҒҚҢӨҰҮҺІ.mov',
           'created_at': '2026-10-17T12:00:00+05:00', 'completed_at': '2026-10-17T12:00:12+05:00',
           'source_sha256': None,  # No source video exists for this generated demonstration.
           'result': {'verdict': 'violation', 'violations': [
               {'type': 'unconfirmed_event', 'timecode_sec': 2, 'description': 'Демонстрационный сигнал модели. На синтетических кадрах меняется положение жёлтого прямоугольника. Это не банковское нарушение и не пример обучающей категории.',
                'evidence_frames': evidence},
               {'type': 'unconfirmed_event', 'timecode_sec': 6, 'description': 'Второй синтетический сигнал для проверки переноса страниц. <Текст> & специальные символы сохраняются буквально.',
                'evidence_frames': [synthetic_frame(t, x, (540, 720)) for t, x in [(5, 90), (6, 180), (7, 270)]]}]},
           'review': {'events': [{'start_sec': 1, 'end_sec': 3}, {'start_sec': 5, 'end_sec': 7}],
                      'metadata': {'size_bytes': None, 'duration_sec': 10, 'width': 960, 'height': 540,
                                   'average_fps': 25, 'nominal_fps': 25, 'frame_rate_mode': 'synthetic', 'codec': 'synthetic descriptor'},
                      'limitations': ['Этот документ использует исключительно синтетические кадры. Исходный видеоролик не создавался; его SHA-256 не установлен.',
                                      'Реальная модель не запускалась. Версии и хеши модели не заявляются.',
                                      'Казахские символы: Әә Ғғ Ққ Ңң Өө Ұұ Үү Һһ Іі.'],
                      'version_snapshot': {'model': {'id': 'SYNTHETIC / no model run'},
                                           'configuration': {'id': 'synthetic-example-v1'}, 'code': {'version': 'synthetic-demo'},
                                           'parameters': {}}}}
    positive = generate_report(job, args.output_dir)
    shutil.copyfile(positive.pdf_path, args.output_dir / 'preliminary-audit.pdf')
    negative_job = dict(job, job_id='synthetic-negative-example', result={'verdict': 'no_violation', 'violations': []})
    generate_report(negative_job, args.output_dir / 'negative')
    print(args.output_dir / 'preliminary-audit.pdf')
    print(positive.passport_path)


if __name__ == '__main__':
    main()
