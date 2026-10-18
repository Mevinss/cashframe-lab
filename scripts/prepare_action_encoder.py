"""Download only pinned public SigLIP assets; this command never opens dataset files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

MODEL_ID='google/siglip-base-patch16-224'
REVISION='7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed'
FILES=('README.md','config.json','model.safetensors','preprocessor_config.json',
       'special_tokens_map.json','spiece.model','tokenizer.json','tokenizer_config.json')


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()


def download_with_resume(url,target):
    """Two bounded five-minute transfer attempts, resuming partial public bytes."""
    temporary=target.with_name(target.name+'.part')
    for attempt in range(2):
        deadline=time.monotonic()+300
        offset=temporary.stat().st_size if temporary.exists() else 0
        headers={'User-Agent':'cashframe-local-research/1'}
        if offset: headers['Range']=f'bytes={offset}-'
        try:
            request=urllib.request.Request(url,headers=headers)
            with urllib.request.urlopen(request,timeout=30) as response:
                if offset and (response.status!=206 or not response.headers.get('Content-Range','').startswith(f'bytes {offset}-')):
                    raise ValueError('Public server did not honor the resume range')
                with temporary.open('ab' if offset else 'wb') as stream:
                    while block:=response.read(1024*1024):
                        stream.write(block)
                        if time.monotonic()>deadline: raise TimeoutError('Public transfer time budget reached')
            temporary.replace(target)
            return
        except (TimeoutError,OSError):
            if attempt==1: raise

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='local/action-encoder')
    args=parser.parse_args()
    os.environ.update(HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_XET='1',
                      HF_HUB_DISABLE_IMPLICIT_TOKEN='1',DO_NOT_TRACK='1')
    from huggingface_hub import model_info
    started=time.perf_counter()
    root=Path(args.output).resolve(); root.mkdir(parents=True,exist_ok=True)
    info=model_info(MODEL_ID,revision=REVISION,files_metadata=True,token=False)
    if info.sha!=REVISION or info.card_data.get('license')!='apache-2.0':
        raise ValueError('Pinned model identity or license mismatch')
    remote={item.rfilename:item for item in info.siblings}
    hashes={}
    for name in FILES:
        target=root/name
        lfs=getattr(remote[name],'lfs',None)
        existing_valid=target.exists() and lfs is not None and sha256(target)==lfs.sha256
        if not existing_valid:
            download_with_resume(f'https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}',target)
        digest=sha256(target)
        if lfs is not None and digest!=lfs.sha256:
            raise ValueError('Public weight download checksum mismatch')
        hashes[name]=digest
    if 'license: apache-2.0' not in (root/'README.md').read_text(encoding='utf-8'):
        raise ValueError('Downloaded pinned model card license mismatch')
    manifest={'schema_version':'cashframe.action-encoder/1','model_id':MODEL_ID,
              'revision':REVISION,'license':'apache-2.0',
              'license_source':f'https://huggingface.co/{MODEL_ID}/blob/{REVISION}/README.md',
              'files':hashes,'weight_format':'safetensors','remote_code':False,
              'public_anonymous_download':True}
    (root/'manifest.json').write_text(json.dumps(manifest,sort_keys=True,indent=2),encoding='utf-8')
    print(json.dumps({'status':'encoder_prepared','files':len(hashes),
                      'wall_sec':round(time.perf_counter()-started,3)}))

if __name__=='__main__':
    try: main()
    except Exception:
        print('Encoder preparation failed; no dataset files were opened.',file=sys.stderr)
        raise SystemExit(1)


