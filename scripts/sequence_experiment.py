"""Prepare human review, fit an isolated TCN, or compare frozen models locally."""
import argparse
import json

from cashframe.sequence_candidate import write_review_table
from cashframe.sequence_training import train, evaluate, benchmark_raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('review-table', 'train', 'evaluate', 'benchmark'))
    parser.add_argument('--manifest', default='local/dataset/manifest.json')
    parser.add_argument('--split', default='local/dataset/split.json')
    parser.add_argument('--features', default='local/features')
    parser.add_argument('--review', default='local/annotations/event-review.csv')
    parser.add_argument('--candidate', default='local/experiments/sequence-tcn')
    parser.add_argument('--baseline', default='local/baselines/v1-original')
    parser.add_argument('--report', default='local/experiments/sequence-tcn/test-comparison.json')
    parser.add_argument('--encoder', default='local/encoder')
    args = parser.parse_args()
    try:
        if args.phase == 'review-table':
            result = {'status': 'review_table_ready', 'source_groups': write_review_table(args.manifest, args.review)}
        elif args.phase == 'train':
            result = train(args.manifest, args.split, args.features, args.review, args.candidate, args.baseline)
        elif args.phase == 'evaluate':
            result = evaluate(args.manifest, args.split, args.features, args.review, args.candidate, args.baseline, args.report)
        else:
            result = benchmark_raw(args.manifest, args.split, args.candidate, args.baseline, args.encoder, args.report)
    except (ValueError, FileNotFoundError, FileExistsError, KeyError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
