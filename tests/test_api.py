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

    def test_overview_uses_real_jobs_and_preserves_strict_result(self):
        with TestClient(create_app(self.settings,analyzer=lambda *a:({'verdict':'no_violation','violations':[]},{'events':[]}))) as client:
            self.assertEqual(client.get('/api/v1/overview').status_code,401)
            self.assertEqual(client.get('/api/v1/system').status_code,401)
            jid=self.post(client).json()['job_id']
            self.assertEqual(self.wait(client,jid)['status'],'done')
            overview=client.get('/api/v1/overview',headers=self.headers).json()
            self.assertEqual(overview['available'],1)
            self.assertEqual(overview['done_with_signals'],0)
            self.assertEqual(overview['recent'][0]['job_id'],jid)
            self.assertNotIn('result',overview['recent'][0])
            self.assertEqual(client.get(f'/api/v1/jobs/{jid}/objects',headers=self.headers).json(),
                             {'status':'not_configured','source_sha256':None,'model_id':None,
                              'observations':[],'conditions':[]})

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
            self.assertEqual(client.post('/api/v1/analyze',headers=self.headers,content=b'not multipart').status_code,422)

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

    def test_model_config_without_classifier_is_not_ready(self):
        self.settings.model_dir.mkdir(parents=True)
        (self.settings.model_dir/'config.json').write_text('{"selected_variant":"trained"}')
        encoder=self.settings.local/'encoder';encoder.mkdir()
        (encoder/'encoder.json').write_text('{}')
        (encoder/'weights.pth').write_bytes(b'incomplete')
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(client.get('/health/ready').status_code,503)

    def test_expired_job_deadline_is_failed(self):
        def slow(*args):
            # Exhaust the budget after acceptance, so machine load cannot make
            # this a test of multipart upload time instead of worker expiry.
            self.settings.deadline_sec=0
            return {'verdict':'no_violation','violations':[]},{}
        with TestClient(create_app(self.settings,analyzer=slow)) as client:
            accepted=self.post(client)
            self.assertEqual(accepted.status_code,202)
            jid=accepted.json()['job_id']
            result=self.wait(client,jid)
            self.assertEqual(result,{'job_id':jid,'status':'failed','error':'processing_deadline'})

    def test_confirmed_annotations_are_persistent_and_not_training_inputs(self):
        def analyze(*args):return {'verdict':'no_violation','violations':[]},{'events':[]}
        with TestClient(create_app(self.settings,analyzer=analyze)) as client:
            jid=self.post(client).json()['job_id'];self.wait(client,jid)
            item={'start_sec':0,'end_sec':.5,'confirmed':True,'origin':'human','reviewer':'test',
                  'observed_fact':'Synthetic colors','type':'fixture','rule_id':None,'rule_status':'unresolved','training_eligible':True}
            response=client.post(f'/api/v1/jobs/{jid}/annotations',headers=self.headers,json=item)
            self.assertEqual(response.status_code,200)
            import json
            saved=json.loads((self.settings.local/'annotations/confirmed.jsonl').read_text(encoding='utf-8').splitlines()[0])
            self.assertFalse(saved['training_eligible'])
            self.assertEqual(len(saved['source_sha256']),64)

    def test_retention_cleans_without_restart(self):
        from unittest.mock import patch
        self.settings.retention_hours=.00005
        def analyze(*args):return {'verdict':'no_violation','violations':[]},{'events':[]}
        with patch('cashframe.api.time.time',return_value=1000) as now, TestClient(create_app(self.settings,analyzer=analyze)) as client:
            jid=self.post(client).json()['job_id'];self.wait(client,jid)
            folder=self.settings.local/'jobs'/jid
            now.return_value=1001
            for _ in range(40):
                if not folder.exists():break
                time.sleep(.025)
            self.assertFalse(folder.exists())
            self.assertEqual(client.get('/api/v1/jobs/'+jid,headers=self.headers).status_code,404)

    def test_experimental_profile_requires_its_own_classifier_files(self):
        import json
        model=self.settings.model_dir;model.mkdir(parents=True)
        (model/'config.json').write_text(json.dumps({'selected_variant':'trained','video_classifier':'selected.npz'}))
        (model/'trained.npz').write_bytes(b'fixture')
        encoder=self.settings.local/'encoder';encoder.mkdir()
        (encoder/'encoder.json').write_text('{}');(encoder/'weights.pth').write_bytes(b'fixture')
        with TestClient(create_app(self.settings)) as client:
            self.assertEqual(client.get('/health/ready').status_code,503)
            (model/'selected.npz').write_bytes(b'fixture');(model/'selection-frozen.json').write_text('{}')
            # File presence is insufficient: these fixtures are not loadable models.
            self.assertEqual(client.get('/health/ready').status_code,503)
