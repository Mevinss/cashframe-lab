"""Measured stage counters; no clock-based estimates of analytical completion."""
import json
import os
from pathlib import Path
import time


def validate_progress(value):
    if not isinstance(value,dict):return None
    stage=value.get('stage');completed=value.get('completed',0);total=value.get('total')
    unit=value.get('unit','items')
    if not isinstance(stage,str) or not stage or len(stage)>80:return None
    if type(completed) is not int or completed<0:return None
    if total is not None and (type(total) is not int or not 0<total<=10**9 or completed>total):return None
    if unit not in ('samples','frames','items'):return None
    return {'stage':stage,'completed':completed,'total':total,
            'percent':min(99,int(100*completed/total)) if total else None,
            'scope':'stage','unit':unit,'updated_at':time.time()}


class StageReporter:
    def __init__(self,path,clock=time.monotonic):
        self.path=Path(path);self.clock=clock;self.stage='accepted';self.last_write=None

    def _write(self,progress):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'stage':self.stage,'progress':progress}),encoding='utf-8')
        os.replace(temporary,self.path)

    def __call__(self,name):
        progress=validate_progress({'stage':name})
        if progress is None:raise ValueError('Invalid processing stage')
        self.stage=name;self.last_write=None;self._write(progress)

    def progress(self,completed,total,unit='items'):
        progress=validate_progress({'stage':self.stage,'completed':completed,'total':total,'unit':unit})
        if progress is None:raise ValueError('Invalid measured progress')
        now=self.clock()
        if self.last_write is not None and now-self.last_write<.5 and completed!=total:return
        self._write(progress);self.last_write=now


def job_progress(job):
    stage=job.get('stage','accepted')
    if job.get('status')=='done':
        return {'stage':'done','completed':1,'total':1,'percent':100,'scope':'stage','unit':'items','updated_at':job.get('completed_at')}
    value=job.get('progress')
    if job.get('status')=='processing' and isinstance(value,dict) and value.get('stage')==stage:
        progress=validate_progress(value)
        if progress:
            progress['updated_at']=value.get('updated_at',progress['updated_at'])
            return progress
    return validate_progress({'stage':stage})
