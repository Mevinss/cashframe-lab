"""Read-only jury handoff checks. Never prints the API key or response bodies."""
import argparse
import json
from pathlib import Path

from cashframe.config import ROOT
from cashframe.jury_delivery import build_manifest, probe_service, read_existing_key, validate_base_url
from cashframe.launcher import load_environment


def main():
    parser = argparse.ArgumentParser(description='Check jury UI/API readiness without uploading videos or printing secrets.')
    parser.add_argument('--base-url', '--url', dest='url', default='http://127.0.0.1:8000', help='Root service URL; no credentials or query parameters.')
    parser.add_argument('--output', type=Path, default=ROOT / 'local/jury-delivery.json', help='Secret-free local delivery report.')
    args = parser.parse_args()
    try:
        url = validate_base_url(args.url)
        key = read_existing_key(ROOT, load_environment(ROOT))
        report = build_manifest(url, probe_service(url, key))
    except (ValueError, OSError):
        parser.exit(2, 'Preflight could not run. Check the root service URL and existing installation configuration.\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Readiness, public delivery and quality are distinct. Exit 1 means the
    # observed technical handoff failed, not that a public URL was fabricated.
    return 0 if report['technical_preflight_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
