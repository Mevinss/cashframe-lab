from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import hmac
import json
import shutil
import threading
import time

from fastapi import Depends,FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartException,MultiPartParser

from .annotations import validate_annotation
from .config import Settings,ROOT
from .data import read_json,write_json,sha256_file
from .storage import JobStore
from .video import probe_video
from .worker import Worker
from .reporting import read_annotations,report_files
from .readiness import RuntimeReadiness
from .progress import job_progress
from .compliance import event_compliance
from typing import Literal


class UploadTooLarge(Exception):pass


class LimitBody:
    def __init__(self,app,limit):self.app,self.limit=app,limit
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        count=0
        async def bounded_receive():
            nonlocal count
            message=await receive()
            count+=len(message.get('body',b''))
            limit = 16384 if scope.get('path', '').endswith('/annotations') else self.limit
            if count>limit:raise UploadTooLarge()
            return message
        try:await self.app(scope,bounded_receive,send)
        except UploadTooLarge:await JSONResponse({'detail':'Файл превышает допустимый размер.'},status_code=413)(scope,receive,send)


def create_app(settings=None,analyzer=None):
    settings=settings or Settings()
    store=JobStore(settings.local/'jobs.sqlite')
    worker=Worker(settings,store,analyzer)
    runtime_readiness=RuntimeReadiness(settings)
    annotation_lock=threading.Lock()
    report_lock=threading.RLock()
    jobs_dir=settings.local/'jobs';jobs_dir.mkdir(parents=True,exist_ok=True)

    def cleanup():
        with report_lock:
            for jid in store.expired(time.time()-settings.retention_hours*3600):
                target=jobs_dir/jid
                if target.resolve().parent!=jobs_dir.resolve():raise ValueError('Unsafe cleanup path')
                shutil.rmtree(target,ignore_errors=True)
                if not target.exists():store.delete(jid)

    @asynccontextmanager
    async def lifespan(app):
        store.recover();cleanup()
        async def sweep():
            while True:
                await asyncio.sleep(min(60,max(.05,settings.retention_hours*3600/2)))
                cleanup()
        task=asyncio.create_task(sweep())
        try:yield
        finally:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
            worker.shutdown()

    app=FastAPI(title='Локальная проверка видео',lifespan=lifespan)
    app.add_middleware(LimitBody,limit=settings.max_upload_bytes+1024*1024)
    key_header=APIKeyHeader(name='X-API-Key',auto_error=False)
    def authorize(key=Depends(key_header)):
        if not settings.api_key or not key or not hmac.compare_digest(key.encode('utf-8'),settings.api_key.encode('utf-8')):
            raise HTTPException(401,'Неверный или отсутствующий API-ключ.')
    def get_job(jid):
        job=store.get(jid)
        if not job:raise HTTPException(404,'Задание не найдено.')
        if job['status']!='processing' and time.time()-job['created_at']>settings.retention_hours*3600:
            cleanup()
            raise HTTPException(404,'Срок хранения задания истёк.')
        return job
    def ready():
        if not settings.api_key:return False
        if analyzer is not None:return True
        try:return runtime_readiness.check()
        except (OSError,ValueError,KeyError):return False

    @app.get('/health/ready')
    def health():
        available=ready()
        return JSONResponse({'ready':available,'mode':'research','taxonomy_confirmed':False},status_code=200 if available else 503)

    @app.get('/api/v1/overview',dependencies=[Depends(authorize)])
    def overview():
        jobs=store.recent(time.time()-settings.retention_hours*3600,settings.max_stored_jobs)
        completed=[job for job in jobs if job['status']=='done']
        measured=[job['elapsed_sec'] for job in completed if isinstance(job.get('elapsed_sec'),(int,float))]
        return {'retention_hours':settings.retention_hours,
                'available':len(jobs),
                'processing':sum(job['status']=='processing' for job in jobs),
                'done_with_signals':sum(bool(job.get('result',{}).get('violations')) for job in completed),
                'failed':sum(job['status']=='failed' for job in jobs),
                'average_processing_sec':round(sum(measured)/len(measured),2) if measured else None,
                'recent':[{'job_id':job['job_id'],'status':job['status'],'filename':job.get('filename'),
                           'verdict':job.get('result',{}).get('verdict'),
                           'created_at':job['created_at'],'elapsed_sec':job.get('elapsed_sec'),
                           'signal_count':len(job.get('result',{}).get('violations',[])) if job['status']=='done' else None}
                          for job in jobs[:10]],
                'jobs':[{'job_id':job['job_id'],'status':job['status'],'filename':job.get('filename'),
                         'verdict':job.get('result',{}).get('verdict'),
                         'created_at':job['created_at'],'elapsed_sec':job.get('elapsed_sec'),
                         'signal_count':len(job.get('result',{}).get('violations',[])) if job['status']=='done' else None}
                        for job in jobs]}

    @app.get('/api/v1/system',dependencies=[Depends(authorize)])
    async def system():
        available=await asyncio.to_thread(ready)
        return {'ready':available,'model_profile':settings.model_dir.name,
                'money_detector':'not_configured','tracking':'not_configured',
                'retention_hours':settings.retention_hours,'mode':'research'}

    @app.post('/api/v1/analyze',status_code=202,dependencies=[Depends(authorize)])
    async def analyze(request:Request):
        accepted_at=time.monotonic()
        if not await asyncio.to_thread(ready):raise HTTPException(503,'Модель не подготовлена. Выполните команды подготовки и обучения.')
        cleanup()
        if shutil.disk_usage(settings.local).free < (settings.min_free_disk_mb*1024*1024+settings.max_upload_bytes):
            raise HTTPException(507,'Недостаточно свободного места для нового видео.')
        if sum(p.is_dir() for p in jobs_dir.iterdir())>=settings.max_stored_jobs:
            raise HTTPException(503,'Достигнут лимит сохранённых заданий. Выполните очистку по сроку хранения.')
        if not worker.reserve():raise HTTPException(429,'Сервис занят. Повторите запрос после завершения текущего видео.',headers={'Retry-After':'10'})
        job_id=None;started=False;upload=None;form=None
        try:
            if not request.headers.get('content-type','').lower().startswith('multipart/form-data'):
                raise HTTPException(422,'Нужно multipart-поле file.')
            async def upload_stream():
                source=request.stream().__aiter__()
                while True:
                    remaining=settings.deadline_sec-(time.monotonic()-accepted_at)
                    if remaining<=0:raise MultiPartException('upload_deadline')
                    try:chunk=await asyncio.wait_for(anext(source),timeout=remaining)
                    except StopAsyncIteration:return
                    except asyncio.TimeoutError as exc:raise MultiPartException('upload_deadline') from exc
                    except UploadTooLarge as exc:raise MultiPartException('upload_size') from exc
                    yield chunk
            try:
                parser=MultiPartParser(request.headers,upload_stream(),max_files=1,max_fields=1)
                try:form=await parser.parse()
                except BaseException:
                    # Also close partial files on disconnect/cancellation
                    # also close them on disconnect/cancellation before propagating.
                    for handle in parser._files_to_close_on_error:handle.close()
                    raise
            except MultiPartException as exc:
                if exc.message=='upload_deadline':raise HTTPException(408,'Загрузка превысила лимит времени. Повторите запрос.') from exc
                if exc.message=='upload_size':raise HTTPException(413,'Файл превышает допустимый размер.') from exc
                raise HTTPException(422,'Некорректный multipart-запрос.') from exc
            upload=form.get('file')
            if not upload or not hasattr(upload,'filename'):raise HTTPException(422,'Нужно multipart-поле file.')
            if Path(upload.filename or '').suffix.lower() not in ('.mov','.mp4'):raise HTTPException(415,'Поддерживаются MOV и MP4.')
            job_id=store.create();folder=jobs_dir/job_id;folder.mkdir()
            path=folder/'input.mov'
            count=0
            with path.open('wb') as target:
                while chunk:=await upload.read(1024*1024):
                    if time.monotonic()-accepted_at>settings.deadline_sec:raise HTTPException(408,'Загрузка превысила лимит времени.')
                    count+=len(chunk)
                    if count>settings.max_upload_bytes:raise HTTPException(413,'Файл превышает допустимый размер.')
                    target.write(chunk)
            try:info=await asyncio.to_thread(probe_video,path)
            except Exception as exc:
                raise HTTPException(422,'Не удалось прочитать видеофайл.') from exc
            if info.duration_sec>settings.max_duration_sec:raise HTTPException(422,'Видео слишком длинное для текущего лимита обработки.')
            digest=await asyncio.to_thread(sha256_file,path)
            if time.monotonic()-accepted_at>settings.deadline_sec:raise HTTPException(408,'Подготовка видео превысила лимит времени.')
            store.update(job_id,filename=Path(upload.filename).name,duration_sec=info.duration_sec,source_sha256=digest)
            worker.start(job_id,path,accepted_at);started=True
            return {'job_id':job_id}
        finally:
            if form is not None:await form.close()
            if not started:
                worker.release()
                if job_id:
                    store.delete(job_id);shutil.rmtree(jobs_dir/job_id,ignore_errors=True)

    @app.get('/api/v1/jobs/{job_id}',dependencies=[Depends(authorize)])
    def job_status(job_id:str):
        job=get_job(job_id)
        if job['status']=='done':return job['result']
        return {'job_id':job_id,'status':job['status']} | ({'error':job['error']} if job['status']=='failed' else {})

    @app.get('/api/v1/jobs/{job_id}/review',dependencies=[Depends(authorize)])
    def review(job_id:str):
        job=get_job(job_id)
        return {'job_id':job_id,'status':job['status'],'stage':job['stage'],'filename':job.get('filename'),
                'elapsed_sec':job.get('elapsed_sec'),'progress':job_progress(job),'review':job.get('review')}

    @app.get('/api/v1/jobs/{job_id}/compliance',dependencies=[Depends(authorize)])
    def compliance(job_id:str):
        job=get_job(job_id)
        with annotation_lock:records=read_annotations(settings.local,job_id)
        return {'job_id':job_id,'events':event_compliance(job,read_json(ROOT/'config/rules.json')['rules'],records)}

    @app.get('/api/v1/jobs/{job_id}/objects',dependencies=[Depends(authorize)])
    def objects(job_id:str):
        get_job(job_id)
        return {'status':'not_configured','source_sha256':None,'model_id':None,
                'observations':[],'conditions':[]}

    @app.get('/api/v1/jobs/{job_id}/video',dependencies=[Depends(authorize)])
    def video(job_id:str):
        get_job(job_id)
        path=jobs_dir/job_id/'preview.mp4'
        if not path.exists():raise HTTPException(409,'Копия для браузера ещё не готова.')
        return FileResponse(path,media_type='video/mp4')

    @app.get('/api/v1/rules',dependencies=[Depends(authorize)])
    def rules():return read_json(ROOT/'config/rules.json')

    def download_report(job_id,kind,language='ru'):
        with report_lock:
            job=get_job(job_id)
            if job['status']!='done':raise HTTPException(409,'Отчёт доступен после успешного завершения анализа.')
            try:
                with annotation_lock:annotations=read_annotations(settings.local,job_id)
                folder,integrity=report_files(job,jobs_dir/job_id,read_json(ROOT/'config/rules.json')['rules'],annotations,language=language)
                name='report.pdf' if kind=='pdf' else 'passport.json'
                data=(folder/name).read_bytes()
            except (ValueError,KeyError,OSError):
                raise HTTPException(409,'Не удалось проверить или сформировать отчёт. Повторите проверку целостности локальных данных.')
            return Response(data,media_type='application/pdf' if kind=='pdf' else 'application/json',
                            headers={'Content-Disposition':f'attachment; filename="{job_id}-{name}"',
                                     'Cache-Control':'no-store','X-Report-SHA256':integrity['report.pdf'],
                                     'X-Passport-SHA256':integrity['passport.json']})

    @app.get('/api/v1/jobs/{job_id}/report.pdf',dependencies=[Depends(authorize)])
    def report_pdf(job_id:str,language:Literal['ru','kk','en']='ru'):return download_report(job_id,'pdf',language)

    @app.get('/api/v1/jobs/{job_id}/passport',dependencies=[Depends(authorize)])
    def passport(job_id:str,language:Literal['ru','kk','en']='ru'):return download_report(job_id,'passport',language)

    @app.get('/api/v1/jobs/{job_id}/annotations',dependencies=[Depends(authorize)])
    def annotations(job_id:str):
        get_job(job_id)
        with annotation_lock:return {'annotations':read_annotations(settings.local,job_id)}

    @app.post('/api/v1/jobs/{job_id}/annotations',dependencies=[Depends(authorize)])
    async def annotate(job_id:str,request:Request):
        job=get_job(job_id)
        if job['status']!='done':raise HTTPException(409,'Разметка доступна после успешного анализа.')
        try:item=validate_annotation(await request.json(),job['duration_sec'])
        except (ValueError,KeyError,TypeError):raise HTTPException(422,'Проверьте интервал, наблюдаемый факт и автора подтверждения.')
        if item.get('rule_id') and not any(r['id']==item['rule_id'] and r.get('verified') for r in read_json(ROOT/'config/rules.json')['rules']):
            raise HTTPException(422,'Правило отсутствует в проверенном реестре.')
        partition='external'
        try:
            splits=read_json(settings.local/'dataset/split.json')
            partition=next((name for name,ids in splits.items() if job['source_sha256'] in ids),'external')
        except OSError:pass
        item.update(job_id=job_id,source_sha256=job['source_sha256'],source_partition=partition,
                    training_eligible=partition=='train' and item.get('confirmed') is True and item.get('visibility')=='clear')
        destination=settings.local/'annotations'/('confirmed.jsonl' if item.get('confirmed') else 'proposed.jsonl')
        destination.parent.mkdir(parents=True,exist_ok=True)
        with annotation_lock:
            with destination.open('a',encoding='utf-8') as stream:stream.write(json.dumps(item,ensure_ascii=False)+'\n')
        return {'saved':True,'confirmed':bool(item.get('confirmed'))}

    @app.get('/')
    def index():
        path=ROOT/'web/index.html'
        if not path.exists():return JSONResponse({'status':'API ready; interface is being implemented'})
        return FileResponse(path)
    if (ROOT/'web').exists():app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')
    return app
