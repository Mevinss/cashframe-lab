from pathlib import Path
import argparse
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from cashframe.storage import JobStore


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Remove expired jobs, never original videos or confirmed annotations')
    parser.add_argument('--hours',type=float,default=24)
    args=parser.parse_args()
    if args.hours<1:raise SystemExit('Retention must be at least one hour')
    folder=(ROOT/'local/jobs').resolve();store=JobStore(ROOT/'local/jobs.sqlite')
    count=0
    for jid in store.expired(time.time()-args.hours*3600):
        target=(folder/jid).resolve()
        if target.parent!=folder:raise SystemExit('Unsafe cleanup path')
        shutil.rmtree(target,ignore_errors=True)
        if not target.exists():store.delete(jid);count+=1
    print(f'Removed expired jobs: {count}')
