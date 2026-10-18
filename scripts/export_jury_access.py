"""Create a private Markdown handoff using an existing key, never a Git secret."""
import argparse
import json
from pathlib import Path

from cashframe.config import ROOT
from cashframe.jury_access_export import export_jury_access


def main():
    parser = argparse.ArgumentParser(description='Export existing jury access to ignored local/jury-access.md.')
    parser.add_argument('--base-url', required=True, help='Explicit HTTPS root origin, or HTTP loopback for a local handoff.')
    parser.add_argument('--key-file', type=Path, help='Existing key file; otherwise use current installation configuration.')
    args = parser.parse_args()
    try:
        result = export_jury_access(ROOT, args.base_url, key_file=args.key_file)
    except (ValueError, OSError):
        parser.exit(2, 'Could not export access. Check the root URL (HTTPS except loopback), existing single-line key and private local directory.\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
