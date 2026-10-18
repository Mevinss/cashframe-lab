"""Export, validate and convert local human event review CSV without video frames."""
import argparse
import json
from pathlib import Path

from cashframe.data import read_json
from cashframe.label_table import (confirmed_annotations, export_rows, read_csv,
                                   summary, write_csv, write_documentation)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('export', 'validate', 'convert'))
    parser.add_argument('--manifest', default='local/dataset/manifest.json')
    parser.add_argument('--split', default='local/dataset/split.json')
    parser.add_argument('--table', default='local/dataset/events-review.csv')
    parser.add_argument('--output', help='New JSONL handoff path for convert')
    args = parser.parse_args()
    manifest, split = read_json(args.manifest), read_json(args.split)
    target = Path(args.table)
    if args.operation == 'export':
        if target.exists():
            parser.error('Export refuses to overwrite an existing review table')
        rows = export_rows(manifest, split)
        write_csv(target, rows, manifest, split)
        write_documentation(target)
        target.with_suffix('.summary.json').write_text(
            json.dumps(summary(rows, manifest, split), ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        rows = read_csv(target, manifest, split)
    if args.operation == 'convert':
        if not args.output:
            parser.error('convert requires --output')
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects a previous human-confirmed handoff.
        with output.open('x', encoding='utf-8') as stream:
            for row in confirmed_annotations(rows, manifest, split):
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
    print(json.dumps(summary(rows, manifest, split), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
