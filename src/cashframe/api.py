from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import hmac
import json
import shutil
import time

from fastapi import Depends,FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from .annotations import validate_annotation
from .config import Settings,ROOT
from .data import read_json,write_json
from .storage import JobStore
from .video import probe_video
from .worker import Worker


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
            if count>self.limit:raise UploadTooLarge()
            return message
        try:await self.app(scope,bounded_receive,send)
        except UploadTooLarge:await JSONResponse({'detail':'Файл превышает допустимый размер.'},status_code=413)(scope,receive,send)


def create_app(settings=None,analyzer=None):
    settings=settings or Settings()
    store=JobStore(settings.local/'jobs.sqlite')
    worker=Worker(settings,store,analyzer)
    jobs_dir=settings.local/'jobs';jobs_dir.mkdir(parents=True,exist_ok=True)

    def cleanup():
        for jid in store.expired(time.time()-settings.retention_hours*3600):
            target=jobs_dir/jid
            if target.resolve().parent!=jobs_dir.resolve():raise ValueError('Unsafe cleanup path')
            shutil.rmtree(target,ignore_errors=True)
            store.delete(jid)

    @asynccontextmanager
    async def lifespan(app):
        store.recover();cleanup()
        yield
        worker.shutdown()

    app=FastAPI(title='Локальная проверка видео',lifespan=lifespan)
    app.add_middleware(LimitBody,limit=settings.max_upload_bytes+1024*1024)
    key_header=APIKeyHeader(name='X-API-Key',auto_error=False)
    def authorize(key=Depends(key_header)):
        if not settings.api_key or not key or not hmac.compare_digest(key,settings.api_key):
            raise HTTPException(401,'Неверный или отсутствующий API-ключ.')
    def get_job(jid):
        job=store.get(jid)
        if not job:raise HTTPException(404,'Задание не найдено.')
        return job
    def ready():
        return bool(settings.api_key) and (analyzer is not None or all(p.exists() for p in [settings.model_dir/'config.json',settings.local/'encoder/encoder.json',settings.local/'encoder/weights.pth']))

    @app.get('/health/ready')
    def health():
        return JSONResponse({'ready':ready(),'mode':'research','taxonomy_confirmed':False},status_code=200 if ready() else 503)

    @app.post('/api/v1/analyze',status_code=202,dependencies=[Depends(authorize)])
    async def analyze(request:Request):
        accepted_at=time.monotonic()
        if not ready():raise HTTPException(503,'Модель не подготовлена. Выполните команды подготовки и обучения.')
        if not worker.reserve():raise HTTPException(429,'Сервис занят. Повторите запрос после завершения текущего видео.',headers={'Retry-After':'10'})
        job_id=None;started=False;upload=None
        try:
            form=await request.form(max_files=1,max_fields=1)
            upload=form.get('file')
            if not upload or not hasattr(upload,'filename'):raise HTTPException(422,'Нужно multipart-поле file.')
            if Path(upload.filename or '').suffix.lower() not in ('.mov','.mp4'):raise HTTPException(415,'Поддерживаются MOV и MP4.')
            job_id=store.create();folder=jobs_dir/job_id;folder.mkdir()
            path=folder/'input.mov'
            count=0
            with path.open('wb') as target:
                while chunk:=await upload.read(1024*1024):
                    count+=len(chunk)
                    if count>settings.max_upload_bytes:raise HTTPException(413,'Файл превышает допустимый размер.')
                    target.write(chunk)
            try:info=await asyncio.to_thread(probe_video,path)
            except (ValueError,Exception) as exc:
                if isinstance(exc,HTTPException):raise
                raise HTTPException(422,'Не удалось прочитать видеофайл.') from exc
            if info.duration_sec>settings.max_duration_sec:raise HTTPException(422,'Видео слишком длинное для текущего лимита обработки.')
            store.update(job_id,filename=Path(upload.filename).name,duration_sec=info.duration_sec)
            worker.start(job_id,path,accepted_at);started=True
            return {'job_id':job_id}
        finally:
            if upload and hasattr(upload,'close'):await upload.close()
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
                'elapsed_sec':job.get('elapsed_sec'),'review':job.get('review')}

    @app.get('/api/v1/jobs/{job_id}/video',dependencies=[Depends(authorize)])
    def video(job_id:str):
        get_job(job_id)
        path=jobs_dir/job_id/'preview.mp4'
        if not path.exists():raise HTTPException(409,'Копия для браузера ещё не готова.')
        return FileResponse(path,media_type='video/mp4')

    @app.get('/api/v1/rules',dependencies=[Depends(authorize)])
    def rules():return read_json(ROOT/'config/rules.json')

    @app.post('/api/v1/jobs/{job_id}/annotations',dependencies=[Depends(authorize)])
    async def annotate(job_id:str,request:Request):
        job=get_job(job_id)
        try:item=validate_annotation(await request.json(),job['duration_sec'])
        except (ValueError,KeyError,TypeError):raise HTTPException(422,'Проверьте интервал, наблюдаемый факт и автора подтверждения.')
        destination=jobs_dir/job_id/'confirmed-annotations.jsonl' if item.get('confirmed') else jobs_dir/job_id/'proposed-annotations.jsonl'
        with destination.open('a',encoding='utf-8') as stream:stream.write(json.dumps(item,ensure_ascii=False)+'\n')
        return {'saved':True,'confirmed':bool(item.get('confirmed'))}

    @app.get('/')
    def index():
        path=ROOT/'web/index.html'
        if not path.exists():return JSONResponse({'status':'API ready; interface is being implemented'})
        return FileResponse(path)
    if (ROOT/'web').exists():app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')
    return app
