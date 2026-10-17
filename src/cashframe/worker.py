from pathlib import Path
import json
import os
import signal
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
        self.stopping=threading.Event()
        self.process_lock=threading.Lock()
        self.processes=set()

    def reserve(self):return not self.stopping.is_set() and self.slot.acquire(blocking=False)

    def release(self):self.slot.release()

    def start(self,jid,path,accepted_at):
        thread=threading.Thread(target=self._run,args=(jid,path,accepted_at),daemon=True)
        self.threads.append(thread)
        thread.start()

    def _run(self,jid,path,accepted_at):
        work=path.parent
        def stage(value):self.store.update(jid,stage=value)
        try:
            if self.stopping.is_set():raise RuntimeError('service_stopping')
            if self.analyzer:
                result,review=self.analyzer(path,work,stage)
            else:
                environment=dict(os.environ,PYTHONPATH=str(self.settings.root/'src'),PYTHONIOENCODING='utf-8')
                command=[sys.executable,'-m','cashframe.cli','analyze','--input',str(path),'--output',str(work),
                         '--model',str(self.settings.model_dir),'--encoder',str(self.settings.local/'encoder'),
                         '--budget-sec',str(max(1,self.settings.deadline_sec-(time.monotonic()-accepted_at)-10))]
                with (work/'worker.log').open('wb') as log:
                    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
                    child=subprocess.Popen(command,stdout=log,stderr=log,env=environment,**options)
                    with self.process_lock:self.processes.add(child)
                    try:
                        while child.poll() is None:
                            if self.stopping.is_set():raise RuntimeError('service_stopping')
                            if time.monotonic()-accepted_at>self.settings.deadline_sec:
                                raise TimeoutError('processing_deadline')
                            try:
                                if (work/'stage.json').exists():stage(read_json(work/'stage.json')['stage'])
                            except (OSError,json.JSONDecodeError):pass
                            time.sleep(.2)
                        if child.returncode:raise RuntimeError('analyzer_failed')
                    finally:
                        self.terminate(child)
                        with self.process_lock:self.processes.discard(child)
                result,review=read_json(work/'result.json'),read_json(work/'review.json')
            if self.stopping.is_set():raise RuntimeError('service_stopping')
            if time.monotonic()-accepted_at>self.settings.deadline_sec:
                raise TimeoutError('processing_deadline')
            validated=CompletedResult(job_id=jid,**result).model_dump()
            self.store.update(jid,status='done',stage='done',result=validated,review=review,
                              completed_at=time.time(),elapsed_sec=round(time.monotonic()-accepted_at,3))
        except Exception as exc:
            code='service_restarted' if self.stopping.is_set() else 'processing_deadline' if isinstance(exc,TimeoutError) else 'analysis_failed'
            self.store.update(jid,status='failed',stage='failed',error=code)
        finally:
            self.release()

    def shutdown(self):
        self.stopping.set()
        with self.process_lock:children=list(self.processes)
        for child in children:self.terminate(child)
        for thread in self.threads:thread.join(timeout=3)

    @staticmethod
    def terminate(child):
        if child.poll() is None:
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,timeout=10)
            else:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            child.wait(timeout=10)
