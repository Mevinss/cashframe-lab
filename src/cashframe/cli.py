from pathlib import Path
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description='Local video review experiments')
    commands = parser.add_subparsers(dest='command', required=True)
    audit = commands.add_parser('audit')
    audit.add_argument('--videos', type=Path, default=Path('D:/open/open/videos'))
    audit.add_argument('--labels', type=Path, default=Path('D:/open/open/open_labels.xlsx'))
    audit.add_argument('--output', type=Path, default=Path('local/dataset'))
    args = parser.parse_args()
    if args.command == 'audit':
        from .data import audit_dataset
        print(json.dumps(audit_dataset(args.videos, args.labels, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
