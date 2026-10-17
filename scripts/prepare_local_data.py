"""Extract the authorized case archive locally without replacing original files."""
import argparse
from pathlib import Path
from zipfile import ZipFile
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, default=Path('local/source'))
    args = parser.parse_args()
    root = args.output.resolve()
    count = 0
    with ZipFile(args.archive) as archive:
        for item in archive.infolist():
            if item.is_dir() or Path(item.filename).suffix.lower() not in ('.mov', '.xlsx'):
                continue
            target = (root / item.filename).resolve()
            if root not in target.parents:
                raise ValueError('Archive member escapes destination')
            if target.exists():
                raise FileExistsError('Destination exists; use another output directory')
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            count += 1
    print(f'Extracted {count} local files; source archive unchanged.')


if __name__ == '__main__':
    main()
