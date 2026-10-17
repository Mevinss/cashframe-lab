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
    prepare = commands.add_parser('prepare-encoder')
    prepare.add_argument('--output', type=Path, default=Path('local/encoder'))
    features = commands.add_parser('features')
    features.add_argument('--manifest', type=Path, default=Path('local/dataset/manifest.json'))
    features.add_argument('--encoder', type=Path, default=Path('local/encoder'))
    features.add_argument('--output', type=Path, default=Path('local/features'))
    train = commands.add_parser('train')
    train.add_argument('--manifest', type=Path, default=Path('local/dataset/manifest.json'))
    train.add_argument('--split', type=Path, default=Path('local/dataset/split.json'))
    train.add_argument('--features', type=Path, default=Path('local/features'))
    train.add_argument('--output', type=Path, default=Path('local/experiment'))
    args = parser.parse_args()
    if args.command == 'audit':
        from .data import audit_dataset
        print(json.dumps(audit_dataset(args.videos, args.labels, args.output), ensure_ascii=False, indent=2))
    elif args.command == 'prepare-encoder':
        from .features import prepare_encoder
        print(json.dumps(prepare_encoder(args.output), ensure_ascii=False, indent=2))
    elif args.command == 'features':
        from .features import extract_dataset
        extract_dataset(args.manifest, args.encoder, args.output)
    elif args.command == 'train':
        from .learning import train_experiment
        report = train_experiment(args.manifest, args.split, args.features, args.output)
        print(json.dumps({k:v for k,v in report.items() if k!='test'}, ensure_ascii=False, indent=2))
        print(json.dumps({k:{a:b for a,b in v.items() if a!='per_video'} for k,v in report['test'].items()},indent=2))


if __name__ == '__main__':
    main()
