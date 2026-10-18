from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
import psutil

from .data import read_json
from .schemas import CompletedResult
from .progress import validate_progress


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
        self.threads=[t for t in self.threads if t.is_alive()]
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
                environment=dict(os.environ,PYTHONPATH=str(self.settings.root/'src'),PYTHONIOENCODING='utf-8',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2')
                command=[sys.executable,'-m','cashframe.cli','analyze','--input',str(path),'--output',str(work),
                         '--model',str(self.settings.model_dir),'--encoder',str(self.settings.local/'encoder'),
                         '--budget-sec',str(max(1,self.settings.deadline_sec-(time.monotonic()-accepted_at)-10))]
                with (work/'worker.log').open('wb') as log:
                    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
                    child=subprocess.Popen(command,stdout=log,stderr=log,env=environment,**options)
                    with self.process_lock:self.processes.add(child)
                    try:
                        last_payload=None
                        while child.poll() is None:
                            if self.stopping.is_set():raise RuntimeError('service_stopping')
                            if time.monotonic()-accepted_at>self.settings.deadline_sec:
                                raise TimeoutError('processing_deadline')
                            try:
                                process=psutil.Process(child.pid)
                                rss=sum(p.memory_info().rss for p in [process,*process.children(recursive=True)] if p.is_running())
                                if rss>self.settings.max_worker_rss_mb*1024*1024:raise MemoryError('worker_memory_limit')
                            except psutil.NoSuchProcess:pass
                            try:
                                if (work/'stage.json').exists():
                                    payload=read_json(work/'stage.json')
                                    if payload!=last_payload:
                                        progress=validate_progress(payload.get('progress'))
                                        if progress and progress['stage']==payload['stage']:
                                            progress['updated_at']=payload['progress'].get('updated_at',progress['updated_at'])
                                            self.store.update(jid,stage=payload['stage'],progress=progress)
                                        else:stage(payload['stage'])
                                        last_payload=payload
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
            source_duration=self.store.get(jid)['duration_sec']
            validated=CompletedResult.model_validate(dict(job_id=jid,**result),context={'duration_sec':source_duration}).model_dump()
            self.store.update(jid,status='done',stage='done',result=validated,review=review,
                              completed_at=time.time(),elapsed_sec=round(time.monotonic()-accepted_at,3))
            if not self.analyzer:self._prepare_preview(jid,path)
        except Exception as exc:
            with (work/'diagnostic.log').open('a',encoding='utf-8') as log:
                log.write(f'job={jid}\n');traceback.print_exc(file=log)
            code='service_restarted' if self.stopping.is_set() else 'processing_deadline' if isinstance(exc,TimeoutError) else 'resource_limit' if isinstance(exc,MemoryError) else 'analysis_failed'
            job=self.store.get(jid)
            if job and job['status']!='done':self.store.update(jid,status='failed',stage='failed',error=code)
        finally:
            self.release()

    def _prepare_preview(self,jid,path):
        """Optional media runs only after the durable completed result is saved."""
        try:
            environment=dict(os.environ,PYTHONPATH=str(self.settings.root/'src'),PYTHONIOENCODING='utf-8')
            options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
            with (path.parent/'preview.log').open('wb') as log:
                child=subprocess.Popen([sys.executable,'-m','cashframe.cli','preview','--input',str(path),
                                        '--output',str(path.parent/'preview.mp4')],stdout=log,stderr=log,env=environment,**options)
                with self.process_lock:self.processes.add(child)
                try:
                    child.wait(timeout=20)
                    if child.returncode:raise RuntimeError('preview_failed')
                finally:
                    self.terminate(child)
                    with self.process_lock:self.processes.discard(child)
            status='ready'
        except Exception:
            status='unavailable'
            with (path.parent/'preview.log').open('a',encoding='utf-8') as log:traceback.print_exc(file=log)
        job=self.store.get(jid)
        if job and job['status']=='done':
            review=dict(job.get('review') or {},preview_status=status)
            self.store.update(jid,review=review)

    def shutdown(self):
        self.stopping.set()
        with self.process_lock:children=list(self.processes)
        for child in children:self.terminate(child)
        for thread in self.threads:thread.join(timeout=3)

    @staticmethod
    def terminate(child):
        if child.poll() is None:
            if os.name=='nt':
                result=subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,timeout=10)
                if result.returncode and child.poll() is None:
                    # Restricted Windows environments may deny taskkill while
                    # still allowing the parent to terminate its own child.
                    child.kill()
            else:
                try:os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            child.wait(timeout=10)
