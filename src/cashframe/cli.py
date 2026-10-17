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
    train.add_argument('--skip-test', action='store_true', help='Restore/train without reading the already-viewed test features')
    analyze = commands.add_parser('analyze')
    analyze.add_argument('--input', type=Path, required=True)
    analyze.add_argument('--output', type=Path, required=True)
    analyze.add_argument('--model', type=Path, default=Path('local/experiment'))
    analyze.add_argument('--encoder', type=Path, default=Path('local/encoder'))
    analyze.add_argument('--budget-sec',type=float,default=240)
    preview = commands.add_parser('preview')
    preview.add_argument('--input', type=Path, required=True)
    preview.add_argument('--output', type=Path, required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8000)
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
        report = train_experiment(args.manifest, args.split, args.features, args.output, evaluate_test=not args.skip_test)
        print(json.dumps({k:v for k,v in report.items() if k!='test'}, ensure_ascii=False, indent=2))
        print(json.dumps({k:{a:b for a,b in v.items() if a!='per_video'} for k,v in report['test'].items()},indent=2))
    elif args.command == 'analyze':
        from .pipeline import analyze_video, block_outgoing_network
        from .data import write_json
        block_outgoing_network()
        def stage(name):
            write_json(args.output/'stage.json',{'stage':name})
        result,review=analyze_video(args.input,args.model,args.output,stage,args.encoder,budget_sec=args.budget_sec)
        write_json(args.output/'result.json',result)
        write_json(args.output/'review.json',review)
        print(json.dumps(review['measurements']))
    elif args.command == 'preview':
        from .video import browser_copy
        temporary=args.output.with_name('preview-building.mp4')
        browser_copy(args.input,temporary)
        temporary.replace(args.output)
    elif args.command == 'serve':
        import uvicorn
        from .api import create_app
        from .config import Settings
        settings=Settings()
        if not settings.api_key:
            raise SystemExit('Set CASHFRAME_API_KEY before starting the server.')
        uvicorn.run(create_app(settings),host=args.host,port=args.port)


if __name__ == '__main__':
    main()
