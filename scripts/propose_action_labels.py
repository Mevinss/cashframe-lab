"""Generate separate local, unconfirmed action research proposals (held-out test excluded)."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from cashframe.action_proposals import run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',default='local/dataset/manifest.json')
    parser.add_argument('--split',default='local/dataset/split.json')
    parser.add_argument('--review',default='local/dataset/events-review.csv')
    parser.add_argument('--encoder',default='local/action-encoder')
    parser.add_argument('--output',default='local/dataset/action-proposals-v1')
    parser.add_argument('--threads',type=int,default=4)
    args=parser.parse_args()
    try:
        summary=run(args.manifest,args.split,args.review,args.encoder,args.output,args.threads)
    except Exception:
        # No source names, frames, descriptions, model scores or environment tokens in console errors.
        print('Local proposal run failed; no source annotations were changed.',file=sys.stderr)
        return 1
    allowed=('status','eligible_points','proposal_count','excluded_rows','counts','abstained','wall_sec','peak_process_rss_mb')
    print(json.dumps({key:summary[key] for key in allowed}))
    return 0

if __name__=='__main__': raise SystemExit(main())
