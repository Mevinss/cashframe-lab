"""Independent client: verify jury contract, timestamps and JPEG decoding."""
from pathlib import Path
import argparse
import base64
import io
import json
import os
import time

import httpx
from PIL import Image


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('video',type=Path)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--key-file',type=Path,default=Path('local/api-key.txt'))
    parser.add_argument('--output',type=Path,default=Path('local/api-smoke.json'))
    args=parser.parse_args()
    from cashframe.launcher import load_environment
    from cashframe.config import ROOT
    key=os.getenv('CASHFRAME_API_KEY') or (args.key_file.read_text().strip() if args.key_file.exists() else load_environment(ROOT)['CASHFRAME_API_KEY'])
    if not key:raise ValueError('Provide CASHFRAME_API_KEY or an existing --key-file')
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
