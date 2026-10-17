import base64
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import threading
import time
import unittest

from fastapi.testclient import TestClient
from cashframe.api import create_app
from cashframe.config import Settings
from cashframe.storage import JobStore


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video = self.root/'fixture.mov'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=64x96:d=1:r=8','-c:v','libx264',str(self.video)],check=True)
        self.headers={'X-API-Key':'a-secret-for-tests'}
        self.settings=Settings(root=self.root,api_key=self.headers['X-API-Key'])

    def tearDown(self):
        self.temp.cleanup()

    def post(self,client,name='fixture.mov',data=None):
        return client.post('/api/v1/analyze',headers=self.headers,files={'file':(name,data if data is not None else self.video.read_bytes(),'video/quicktime')})

    def wait(self,client,job_id):
        for _ in range(100):
            result=client.get('/api/v1/jobs/'+job_id,headers=self.headers)
            if result.json()['status']!='processing':return result.json()
            time.sleep(.02)
        self.fail('job did not finish')

    def test_auth_and_unknown_job(self):
        with TestClient(create_app(self.settings,analyzer=lambda *a:{})) as client:
            for headers in [{},{'X-API-Key':'wrong'}]:
                self.assertEqual(client.get('/api/v1/jobs/no',headers=headers).status_code,401)
            self.assertEqual(client.get('/api/v1/jobs/no',headers=self.headers).status_code,404)

    def test_exact_done_contract_and_evidence(self):
        from cashframe.video import evidence_frame
        def analyzer(path,work,stage):
            stage('evidence')
            frame=evidence_frame(path,.5)
            return {'verdict':'violation','violations':[{'type':'Визуальный сигнал — тип не подтверждён','timecode_sec':.5,'description':'Требуется проверка человеком','evidence_frames':[frame]}]}, {'events':[],'limitations':['Fixture']}
        with TestClient(create_app(self.settings,analyzer=analyzer)) as client:
            response=self.post(client)
            self.assertEqual(response.status_code,202)
            self.assertEqual(set(response.json()),{'job_id'})
            result=self.wait(client,response.json()['job_id'])
            self.assertEqual(set(result),{'job_id','status','verdict','violations'})
            self.assertEqual(result['status'],'done')
            item=result['violations'][0]
            self.assertEqual(set(item),{'type','timecode_sec','description','evidence_frames'})
            self.assertTrue(base64.b64decode(item['evidence_frames'][0]['image_base64']).startswith(b'\xff\xd8'))

    def test_bad_format_and_corruption(self):
        with TestClient(create_app(self.settings,analyzer=lambda *a:({},{}))) as client:
            self.assertEqual(self.post(client,'bad.exe').status_code,415)
            self.assertEqual(self.post(client,data=b'invalid MOV').status_code,422)

    def test_analysis_error_never_negative_verdict(self):
        def fail(*args):raise RuntimeError('sensitive internal path')
        with TestClient(create_app(self.settings,analyzer=fail)) as client:
            job_id=self.post(client).json()['job_id']
            result=self.wait(client,job_id)
            self.assertEqual(result['status'],'failed')
            self.assertNotIn('verdict',result)
            self.assertNotIn('sensitive',str(result))

    def test_busy_worker_rejected(self):
        gate=threading.Event()
        def pause(*args):
            gate.wait(3)
            return {'verdict':'no_violation','violations':[]}, {'events':[]}
        with TestClient(create_app(self.settings,analyzer=pause)) as client:
            try:
                self.assertEqual(self.post(client).status_code,202)
                self.assertEqual(self.post(client).status_code,429)
            finally:gate.set()

    def test_size_limit_and_readiness(self):
        self.settings.max_upload_bytes=100
        with TestClient(create_app(self.settings,analyzer=lambda *a:({},{}))) as client:
            self.assertEqual(self.post(client).status_code,413)
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(client.get('/health/ready').status_code,503)

    def test_restart_marks_processing_failed(self):
        store=JobStore(self.root/'jobs.sqlite')
        jid=store.create()
        self.assertEqual(store.get(jid)['status'],'processing')
        store.recover()
        self.assertEqual(store.get(jid)['status'],'failed')
