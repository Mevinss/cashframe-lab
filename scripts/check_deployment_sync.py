"""Check UI/API parity without uploading data, restarting hosts or printing keys."""
import argparse
import json
from pathlib import Path

from cashframe.config import ROOT
from cashframe.deployment_parity import compare_deployments
from cashframe.jury_delivery import read_existing_key
from cashframe.launcher import load_environment


def main():
    parser = argparse.ArgumentParser(description='Read-only local/HTTPS deployment comparison.')
    parser.add_argument('--reference', default='http://127.0.0.1:8000')
    parser.add_argument('--target', required=True, help='Explicit HTTPS origin, without credentials or query.')
    parser.add_argument('--check-auth', action='store_true',
                        help='Send this installation key to /api/v1/system on the two specified origins.')
    parser.add_argument('--output', type=Path, default=ROOT / 'local/deployment-parity.json')
    args = parser.parse_args()
    try:
        key = read_existing_key(ROOT, load_environment(ROOT)) if args.check_auth else None
        report = compare_deployments(args.reference, args.target, key=key)
    except (ValueError, OSError):
        parser.exit(2, 'Check requires valid origin URLs and, with --check-auth, an existing installation key.\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({name: report[name] for name in (
        'frontend_matches', 'both_ready', 'api_schema_matches', 'auth',
        'runtime_weights_identity_verified', 'limitations')}, ensure_ascii=False, indent=2))
    auth_ok = not args.check_auth or all(report['auth'][name]['accepted'] for name in ('reference', 'target'))
    return 0 if report['frontend_matches'] and report['both_ready'] and report['api_schema_matches'] and auth_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
