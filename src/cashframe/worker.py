from pathlib import Path
import json
import os
import subprocess
import sys
import threading
import time

from .data import read_json
from .schemas import CompletedResult


class Worker:
    def __init__(self,settings,store,analyzer=None):
        self.settings,self.store,self.analyzer=settings,store,analyzer
        self.slot=threading.BoundedSemaphore(1)
        self.threads=[]

    def reserve(self):return self.slot.acquire(blocking=False)

    def release(self):self.slot.release()

    def start(self,jid,path,accepted_at):
        thread=threading.Thread(target=self._run,args=(jid,path,accepted_at),daemon=True)
        self.threads.append(thread)
        thread.start()

    def _run(self,jid,path,accepted_at):
        work=path.parent
        def stage(value):self.store.update(jid,stage=value)
        try:
            if self.analyzer:
                result,review=self.analyzer(path,work,stage)
            else:
                environment=dict(os.environ,PYTHONPATH=str(self.settings.root/'src'),PYTHONIOENCODING='utf-8')
                command=[sys.executable,'-m','cashframe.cli','analyze','--input',str(path),'--output',str(work),
                         '--model',str(self.settings.model_dir),'--encoder',str(self.settings.local/'encoder')]
                with (work/'worker.log').open('wb') as log:
                    child=subprocess.Popen(command,stdout=log,stderr=log,env=environment)
                    try:
                        while child.poll() is None:
                            if time.monotonic()-accepted_at>self.settings.deadline_sec:
                                raise TimeoutError('processing_deadline')
                            try:
                                if (work/'stage.json').exists():stage(read_json(work/'stage.json')['stage'])
                            except (OSError,json.JSONDecodeError):pass
                            time.sleep(.2)
                        if child.returncode:raise RuntimeError('analyzer_failed')
                    finally:
                        if child.poll() is None:
                            if os.name=='nt':
                                subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True)
                            else:
                                child.kill()
                            child.wait(timeout=10)
                result,review=read_json(work/'result.json'),read_json(work/'review.json')
            if time.monotonic()-accepted_at>self.settings.deadline_sec:
                raise TimeoutError('processing_deadline')
            validated=CompletedResult(job_id=jid,**result).model_dump()
            self.store.update(jid,status='done',stage='done',result=validated,review=review,
                              elapsed_sec=round(time.monotonic()-accepted_at,3))
        except Exception as exc:
            code='processing_deadline' if isinstance(exc,TimeoutError) else 'analysis_failed'
            self.store.update(jid,status='failed',stage='failed',error=code)
        finally:
            self.release()

    def shutdown(self):
        for thread in self.threads:thread.join(timeout=3)
