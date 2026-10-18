"""Team launcher: literal env files and a persistent, installation-specific key."""
import argparse
import os
from pathlib import Path
import secrets

from .config import ROOT, Settings


VARIABLES = {'CASHFRAME_HOST', 'CASHFRAME_PORT', 'CASHFRAME_API_KEY'}


def load_environment(root: Path, environ=None):
    values = {'CASHFRAME_HOST': '127.0.0.1', 'CASHFRAME_PORT': '8000', 'CASHFRAME_API_KEY': ''}
    for name in ('.env', '.env.local'):
        path = root / name
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, separator, value = line.partition('=')
            key, value = key.strip(), value.strip()
            if not separator or key not in VARIABLES:
                raise ValueError(f'{name}:{number}: expected a supported CASHFRAME_NAME=value setting.')
            if value.startswith(('"', "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError(f'{name}:{number}: unmatched quote.')
                value = value[1:-1]
            values[key] = value
    # No shell evaluation, expansion, or modification of the parent environment.
    environment = os.environ if environ is None else environ
    values.update({key: environment[key] for key in VARIABLES if key in environment})
    return values


def resolve_api_key(root: Path, supplied: str):
    if supplied.strip():
        return supplied.strip()
    path = root / 'local' / 'api-key.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as stream:
            stream.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    key = path.read_text(encoding='utf-8').strip()
    if not key:
        raise ValueError('local/api-key.txt is empty. Remove it to generate a new key.')
    return key


def main():
    parser = argparse.ArgumentParser(description='Start local video review using .env and .env.local')
    parser.add_argument('--host', help='Override CASHFRAME_HOST')
    parser.add_argument('--port', type=int, help='Override CASHFRAME_PORT')
    parser.add_argument('--model-dir',type=Path,help='Optional local model directory, relative to project root')
    parser.add_argument('--dataset-review',action='store_true',help='Enable private training-data review on this installation')
    args = parser.parse_args()
    try:
        values = load_environment(ROOT)
        host = args.host or values['CASHFRAME_HOST']
        port = args.port if args.port is not None else int(values['CASHFRAME_PORT'])
        if not host or not 1 <= port <= 65535:
            raise ValueError('Set a nonempty CASHFRAME_HOST and CASHFRAME_PORT between 1 and 65535.')
        settings = Settings(api_key=resolve_api_key(ROOT, values['CASHFRAME_API_KEY']),model_directory=args.model_dir,
                            dataset_review_enabled=args.dataset_review)
    except ValueError as error:
        parser.error(str(error))
    from .api import create_app
    import uvicorn
    if not values['CASHFRAME_API_KEY'].strip():
        print('Access key: local/api-key.txt (enter it in the interface).', flush=True)
    if not (settings.model_dir / 'config.json').is_file():
        print('Model not installed. Follow docs/team-setup.md before analyzing videos.', flush=True)
    uvicorn.run(create_app(settings), host=host, port=port)


if __name__ == '__main__':
    main()
