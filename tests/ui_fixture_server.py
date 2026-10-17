"""Synthetic UI testing only. Never runs the trained model or source dataset."""
from pathlib import Path
import subprocess
import sys
import time

import uvicorn
from cashframe.api import create_app
from cashframe.config import Settings
from cashframe.video import browser_copy,evidence_frame


def synthetic_analyzer(path,work,stage):
    stage('extracting_features');time.sleep(1)
    frames=[evidence_frame(path,t) for t in [.25,1.,1.75]]
    browser_copy(path,work/'preview.mp4')
    event={'id':'fixture-1','type':'Тестовый сигнал интерфейса','timecode_sec':1.,'description':'Синтетическая сцена для проверки интерфейса. Не является результатом модели.',
           'start_sec':.5,'end_sec':1.5,'priority':'unspecified','priority_reason':'Тестовый пример; приоритет не назначен.'}
    result={'verdict':'violation','violations':[{k:event[k] for k in ['type','timecode_sec','description']}|{'evidence_frames':frames}]}
    review={'events':[event],'duration_sec':2.,'limitations':['Синтетический пример для проверки интерфейса, не оценка модели.'],
            'measurements':{'analysis_wall_sec':2.,'samples':8,'rss_mb':0}}
    return result,review


if __name__=='__main__':
    root=Path('local/ui-fixture').resolve();root.mkdir(parents=True,exist_ok=True)
    target=root/'synthetic.mov'
    if not target.exists():
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=464x848:rate=12:duration=2','-c:v','libx264',str(target)],check=True)
    app=create_app(Settings(root=root,api_key='ui-fixture-key'),analyzer=synthetic_analyzer)
    from fastapi.responses import HTMLResponse
    @app.get('/test/mobile')
    def mobile():
        return HTMLResponse('<!doctype html><html><head><title>Mobile UI fixture — 390 px</title></head><body style="margin:0;background:#ddd"><iframe title="Мобильная проверка 390 px" src="/" style="width:390px;height:844px;border:0;background:white"></iframe></body></html>')
    uvicorn.run(app,host='127.0.0.1',port=8001)
