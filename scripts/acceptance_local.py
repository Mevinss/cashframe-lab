"""Real MOV acceptance via HTTP; private inputs, evidence and reports stay local.

Select cases by ground-truth label and duration, never by the model's outcome.
This checks integration and timing, not independent generalization accuracy.
"""
import argparse
import base64
import hashlib
import io
from pathlib import Path
import time

import httpx
from PIL import Image
from pypdf import PdfReader
from cashframe.config import ROOT
from cashframe.data import read_json,write_json
from cashframe.launcher import load_environment,resolve_api_key


def run_case(client,headers,case,source,output):
    start=time.perf_counter()
    while True:
        with Path(source['path']).open('rb') as stream:
            response=client.post('/api/v1/analyze',headers=headers,files={'file':(Path(source['path']).name,stream,'video/quicktime')})
        if response.status_code!=429:break
        if time.perf_counter()-start>30:raise TimeoutError('Worker did not become available')
        time.sleep(1)
    response.raise_for_status();assert set(response.json())=={'job_id'}
    jid=response.json()['job_id'];accepted=time.perf_counter()
    while time.perf_counter()-start<300:
        response=client.get('/api/v1/jobs/'+jid,headers=headers);response.raise_for_status();result=response.json()
        if result['status']!='processing':break
        time.sleep(.5)
    assert result['status']=='done',result
    done_wall=time.perf_counter()-start
    assert done_wall<300
    assert set(result)=={'job_id','status','verdict','violations'}
    assert result['verdict'] in ('violation','no_violation')
    review=client.get(f'/api/v1/jobs/{jid}/review',headers=headers).json()['review']
    jpeg_count=0
    for event in result['violations']:
        assert set(event)=={'type','timecode_sec','description','evidence_frames'}
        assert event['type'] and event['description'] and 0<=event['timecode_sec']<source['duration_sec']
        assert 1<=len(event['evidence_frames'])<=3
        for frame in event['evidence_frames']:
            assert set(frame)=={'timestamp_sec','image_base64'}
            assert 0<=frame['timestamp_sec']<source['duration_sec']
            raw=base64.b64decode(frame['image_base64'],validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                assert image.format=='JPEG';assert image.size==(review['metadata']['width'],review['metadata']['height']);image.verify()
            jpeg_count+=1
    assert review['measurements']['outgoing_network_blocked'] is True
    passport=client.get(f'/api/v1/jobs/{jid}/passport',headers=headers);passport.raise_for_status()
    assert hashlib.sha256(passport.content).hexdigest()==passport.headers['X-Passport-SHA256']
    pdf=client.get(f'/api/v1/jobs/{jid}/report.pdf',headers=headers);pdf.raise_for_status()
    assert hashlib.sha256(pdf.content).hexdigest()==pdf.headers['X-Report-SHA256']
    pages=PdfReader(io.BytesIO(pdf.content)).pages
    assert len(pages)>0 and 'Предварительный' in pages[0].extract_text()
    output.mkdir(parents=True,exist_ok=True)
    (output/f'{case}.pdf').write_bytes(pdf.content);(output/f'{case}-passport.json').write_bytes(passport.content)
    # The optional preview is allowed to finish after the completed AI result.
    preview_status=None
    for _ in range(25):
        media=client.get(f'/api/v1/jobs/{jid}/video',headers=headers)
        preview_status=media.status_code
        if media.status_code==200:break
        time.sleep(1)
    return {'case':case,'job_id':jid,'source_partition':source['partition'],'truth':source['label'],
            'duration_sec':source['duration_sec'],'verdict':result['verdict'],'events':len(result['violations']),
            'matches_binary_label':(result['verdict']=='violation')==bool(source['label']),
            'wall_sec':round(done_wall,3),'processing_after_accept_sec':round(done_wall-(accepted-start),3),
            'jpeg_count':jpeg_count,'exact_contract_valid':True,'pdf_pages':len(pages),'report_hashes_valid':True,
            'preview_http_status':preview_status,'measurements':review['measurements'],
            'localization_statuses':[e.get('localization_status') for e in review['events']]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--output',type=Path,default=Path('local/verification/acceptance'))
    parser.add_argument('--cases',default='positive,negative,longest',help='Comma-separated predeclared case names')
    args=parser.parse_args()
    split=read_json('local/dataset/split.json');manifest=read_json('local/dataset/manifest.json')
    pool=[dict(row,partition=next(name for name,ids in split.items() if row['group_id'] in ids)) for row in manifest if row['group_id'] in split['train']+split['validation']]
    training=[row for row in pool if row['partition']=='train']
    cases=[('positive',min((r for r in training if r['label']==1),key=lambda r:r['duration_sec'])),
           ('negative',min((r for r in training if r['label']==0),key=lambda r:r['duration_sec'])),
           ('longest',max(pool,key=lambda r:r['duration_sec']))]
    key=resolve_api_key(ROOT,load_environment(ROOT)['CASHFRAME_API_KEY']);headers={'X-API-Key':key}
    requested=args.cases.split(',')
    if set(requested)-{'positive','negative','longest'}:raise ValueError('Unknown case name')
    cases=[(name,source) for name,source in cases if name in requested]
    previous=args.output/'results.json'
    results=[row for row in read_json(previous)['cases'] if row['case'] not in requested] if previous.exists() else []
    with httpx.Client(base_url=args.url,timeout=90,trust_env=False) as client:
        assert client.get('/health/ready').status_code==200
        assert client.get('/api/v1/jobs/unknown').status_code==401
        assert client.get('/api/v1/jobs/unknown',headers={'X-API-Key':'wrong'}).status_code==401
        assert client.get('/').status_code==200
        for case,source in cases:
            result=run_case(client,headers,case,source,args.output);results.append(result)
            write_json(args.output/'results.json',{'test_features_used':False,'selection':'train/validation label and duration only','cases':results})
            print(result,flush=True)


if __name__=='__main__':main()
