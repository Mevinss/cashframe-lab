from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import threading
import time
import unittest

from fastapi.testclient import TestClient
from cashframe.api import create_app
from cashframe.config import Settings
from cashframe.storage import JobStore


class ReportApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.settings=Settings(root=Path(self.temp.name),api_key='test-secret')
        self.store=JobStore(self.settings.local/'jobs.sqlite')
        self.jid=self.store.create()
        (self.settings.local/'jobs'/self.jid).mkdir(parents=True)
        self.headers={'X-API-Key':'test-secret'}
        self.store.update(self.jid,status='done',stage='done',filename='synthetic.mov',source_sha256='a'*64,duration_sec=1,
                          result={'job_id':self.jid,'status':'done','verdict':'no_violation','violations':[]},review={'events':[],'duration_sec':1})

    def test_authentication_and_exact_jury_contract_unchanged(self):
        with TestClient(create_app(self.settings,analyzer=lambda *args:None)) as client:
            for suffix in ('report.pdf','passport'):
                self.assertEqual(client.get(f'/api/v1/jobs/{self.jid}/{suffix}').status_code,401)
            result=client.get('/api/v1/jobs/'+self.jid,headers=self.headers).json()
            self.assertEqual(set(result),{'job_id','status','verdict','violations'})
            pdf=client.get(f'/api/v1/jobs/{self.jid}/report.pdf',headers=self.headers)
            self.assertEqual(pdf.status_code,200)
            self.assertTrue(pdf.content.startswith(b'%PDF'))
            self.assertEqual(pdf.headers['x-report-sha256'],hashlib.sha256(pdf.content).hexdigest())
            self.assertNotIn(b'test-secret',pdf.content)
            passport=client.get(f'/api/v1/jobs/{self.jid}/passport',headers=self.headers)
            self.assertEqual(passport.status_code,200)
            self.assertEqual(passport.headers['x-passport-sha256'],hashlib.sha256(passport.content).hexdigest())
            self.assertEqual(pdf.content,client.get(f'/api/v1/jobs/{self.jid}/report.pdf',headers=self.headers).content)

    def test_pending_failed_expired_and_tampered_reports(self):
        with TestClient(create_app(self.settings,analyzer=lambda *args:None)) as client:
            pending=self.store.create()
            for status in ('processing','failed'):
                self.store.update(pending,status=status)
                self.assertEqual(client.get(f'/api/v1/jobs/{pending}/report.pdf',headers=self.headers).status_code,409)
            self.assertEqual(client.get(f'/api/v1/jobs/{self.jid}/report.pdf',headers=self.headers).status_code,200)
            report=next((self.settings.local/'jobs'/self.jid/'reports').rglob('report.pdf'))
            report.write_bytes(b'tampered')
            self.assertEqual(client.get(f'/api/v1/jobs/{self.jid}/report.pdf',headers=self.headers).status_code,409)
            self.store.update(self.jid,created_at=time.time()-25*3600)
            self.assertEqual(client.get(f'/api/v1/jobs/{self.jid}/passport',headers=self.headers).status_code,404)

    def test_annotation_visibility_provenance_and_rule_confirmation(self):
        item={'start_sec':0,'end_sec':1,'confirmed':True,'origin':'human','reviewer':'tester',
              'observed_fact':'Synthetic action','type':'fixture','visibility':'clear','rule_id':'RK231-20',
              'rule_status':'confirmed','rule_applicability_confirmed':True,'training_eligible':True}
        with TestClient(create_app(self.settings,analyzer=lambda *args:None)) as client:
            before=client.get(f'/api/v1/jobs/{self.jid}/passport',headers=self.headers).content
            self.assertEqual(client.post(f'/api/v1/jobs/{self.jid}/annotations',headers=self.headers,json=item).status_code,200)
            saved=json.loads((self.settings.local/'annotations/confirmed.jsonl').read_text(encoding='utf-8').splitlines()[0])
            self.assertFalse(saved['training_eligible'])
            self.assertEqual(saved['visibility'],'clear')
            self.assertEqual(client.get(f'/api/v1/jobs/{self.jid}/annotations',headers=self.headers).json()['annotations'][0]['observed_fact'],'Synthetic action')
            after=client.get(f'/api/v1/jobs/{self.jid}/passport',headers=self.headers).content
            self.assertNotEqual(before,after)
            self.assertEqual(len(list((self.settings.local/'jobs'/self.jid/'reports').glob('*/passport.json'))),2)
            self.assertEqual(client.get('/api/v1/jobs/'+self.jid,headers=self.headers).json()['violations'],[])
            item['visibility']='partial'
            self.assertEqual(client.post(f'/api/v1/jobs/{self.jid}/annotations',headers=self.headers,json=item).status_code,422)


if __name__ == '__main__':unittest.main()
