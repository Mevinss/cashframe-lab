"""Train optional local visual types from confirmed human interval labels."""
import argparse
import json

from cashframe.taxonomy import InsufficientTypeData, train_types


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--annotations', required=True, help='Local confirmed annotation JSONL')
    parser.add_argument('--manifest', required=True, help='Trusted original-source manifest JSON')
    parser.add_argument('--split', required=True, help='Trusted fixed source split JSON')
    parser.add_argument('--features', required=True, help='Existing local DINO feature cache directory')
    parser.add_argument('--output', required=True, help='Local type artifact ending in .npz')
    args = parser.parse_args()
    try:
        report = train_types(args.annotations, args.manifest, args.split, args.features, args.output)
    except (InsufficientTypeData, ValueError, OSError) as exc:
        print(json.dumps({'status': 'not_trained', 'reason': str(exc), 'artifact_installed': False}))
        return 2
    print(json.dumps({'status': 'active' if report['active'] else 'inactive_validation_failed',
                      'types': len(report['labels']), 'validation_macro_f1': report['validation']['macro_f1'],
                      'validation_coverage': report['validation']['coverage'],
                      'notice': 'Synthetic tests and small validation sets do not establish production or new-camera accuracy.'}))
    return 0 if report['active'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
