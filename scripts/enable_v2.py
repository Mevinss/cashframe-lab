"""Prepare a separate local model directory for explicit experimental opt-in."""
import argparse
from cashframe.research import prepare_opt_in

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='local/experiment')
    parser.add_argument('--candidate',default='local/experiments/v2')
    parser.add_argument('--output',default='local/experiment-v2')
    args=parser.parse_args()
    print(prepare_opt_in(args.base,args.candidate,args.output))
