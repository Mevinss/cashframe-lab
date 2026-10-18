"""Prepare local human review or derive versioned TCN review from master facts."""
import argparse
import json

from cashframe.review_bridge import prepare_review, write_bridge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'convert'))
    parser.add_argument('--manifest', default='local/dataset/manifest.json')
    parser.add_argument('--split', default='local/dataset/split.json')
    parser.add_argument('--table', default='local/dataset/events-review.csv')
    parser.add_argument('--coverage', default='local/dataset/review-coverage.csv',
                        help='Canonical coverage CSV shared with the local dataset-review UI')
    parser.add_argument('--output', help='New CSV destination; existing files are never overwritten')
    args = parser.parse_args()
    if not args.output:
        parser.error('--output is required')
    try:
        if args.operation == 'prepare':
            result = prepare_review(args.table, args.manifest, args.split, args.output, args.coverage)
        else:
            result = write_bridge(args.table, args.coverage, args.manifest, args.split, args.output)
    except (ValueError, FileNotFoundError, FileExistsError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
