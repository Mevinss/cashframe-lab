"""Independent client: verify jury contract, timestamps and JPEG decoding."""
from pathlib import Path
import argparse
import base64
import io
import json
import time

import httpx
from PIL import Image


def read_api_key(root, key_file=None):
    """Match launcher configuration unless a key file is explicitly selected.

    The client only reads existing credentials; it never creates or rotates them.
    """
    from cashframe.launcher import load_environment
    if key_file is not None:
        path = Path(key_file)
    else:
        configured = load_environment(Path(root))['CASHFRAME_API_KEY'].strip()
        if configured:
            return configured
        path = Path(root) / 'local' / 'api-key.txt'
    try:
        key = path.read_text(encoding='utf-8-sig').strip()
    except FileNotFoundError as exc:
        raise ValueError('Provide CASHFRAME_API_KEY or an existing --key-file; the smoke client does not generate keys.') from exc
    if not key:
        raise ValueError('The selected API key file is empty. Set CASHFRAME_API_KEY or select a nonempty --key-file.')
    return key


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('video',type=Path)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--key-file',type=Path,help='Explicit key file; otherwise use launcher environment and existing local/api-key.txt')
    parser.add_argument('--output',type=Path,default=Path('local/api-smoke.json'))
    args=parser.parse_args()
    from cashframe.config import ROOT
    try:key=read_api_key(ROOT,args.key_file)
    except ValueError as error:parser.error(str(error))
    headers={'X-API-Key':key}
    start=time.perf_counter()
    with httpx.Client(base_url=args.url,timeout=60,trust_env=False) as client:
        assert client.get('/api/v1/jobs/no',headers={'X-API-Key':'wrong'}).status_code==401
        with args.video.open('rb') as stream:
            response=client.post('/api/v1/analyze',headers=headers,files={'file':(args.video.name,stream,'video/quicktime')})
        response.raise_for_status()
        assert set(response.json())=={'job_id'}
        jid=response.json()['job_id']
        while time.perf_counter()-start<300:
            response=client.get('/api/v1/jobs/'+jid,headers=headers);response.raise_for_status()
            result=response.json()
            if result['status']!='processing':break
            time.sleep(1)
        assert result['status']=='done',result
        assert set(result)=={'job_id','status','verdict','violations'}
        assert result['verdict'] in ['violation','no_violation']
        review=client.get('/api/v1/jobs/'+jid+'/review',headers=headers).json()
        duration=review['review']['duration_sec']
        for event in result['violations']:
            assert set(event)=={'type','timecode_sec','description','evidence_frames'}
            assert 0<=event['timecode_sec']<duration
            assert 1<=len(event['evidence_frames'])<=3
            for frame in event['evidence_frames']:
                assert 0<=frame['timestamp_sec']<duration
                image=Image.open(io.BytesIO(base64.b64decode(frame['image_base64'],validate=True)))
                assert image.format=='JPEG';image.verify()
        summary={'job_id':jid,'status':result['status'],'verdict':result['verdict'],'events':len(result['violations']),
                 'wall_sec':round(time.perf_counter()-start,3),'contract_valid':True,'jpeg_valid':True,'review':review['review']['measurements']}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
