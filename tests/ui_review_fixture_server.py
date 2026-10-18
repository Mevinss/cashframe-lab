"""Synthetic source-review browser fixture. Never loads the bank manifest."""
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

import uvicorn
from cashframe.api import create_app
from cashframe.config import Settings


if __name__ == '__main__':
    with TemporaryDirectory(prefix='videoaudit-review-fixture-') as temporary:
        root=Path(temporary)
        dataset=root/'local/dataset';dataset.mkdir(parents=True)
        manifest=[];split={'train':[],'validation':[],'test':[]}
        for number,(color,partition,label) in enumerate([('navy','train',1),('teal','validation',0),('gold','test',1)],1):
            path=root/f'X-{number}.mov'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',f'color=c={color}:s=160x240:d=10:r=8',
                            '-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            manifest.append(dict(file=path.name,path=str(path),sha256=digest,group_id=digest,
                                 files=[path.name],label=label,duration_sec=10,points_sec=[5] if label else []))
            split[partition].append(digest)
        (dataset/'manifest.json').write_text(json.dumps(manifest))
        (dataset/'split.json').write_text(json.dumps(split))
        uvicorn.run(create_app(Settings(root=root,api_key='synthetic-review-key',dataset_review_enabled=True),
                               analyzer=lambda *args:None),host='127.0.0.1',port=8892,log_level='warning')
