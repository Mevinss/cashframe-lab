"""Synthetic source-context integration; no real model accuracy is asserted."""
import base64
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from PIL import Image

from cashframe.api import create_app
from cashframe.config import Settings
from cashframe.storage import JobStore


class CandidateApiTests(unittest.TestCase):
    def test_exact_typed_prediction_offers_verified_reference_without_changing_jury_result(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);store=JobStore(root/'local/jobs.sqlite');jid=store.create()
            image=BytesIO();Image.new('RGB',(16,16),'navy').save(image,'JPEG')
            category='leave_before_service_end'
            event={'type':category,'timecode_sec':1.,'description':'Synthetic action proposal',
                   'evidence_frames':[{'timestamp_sec':1.,'image_base64':base64.b64encode(image.getvalue()).decode()}]}
            result={'job_id':jid,'status':'done','verdict':'violation','violations':[event]}
            meta={'id':'event-1','type':category,'start_sec':.5,'end_sec':1.5,'timecode_sec':1.,
                  'localization_status':'model_proposal',
                  'type_prediction':{'type':category,'origin':'automatic','confirmed':False}}
            store.update(jid,status='done',stage='done',duration_sec=2.,source_sha256='a'*64,
                         result=result,review={'events':[meta],'duration_sec':2.})
            (root/'local/jobs'/jid).mkdir(parents=True)
            with TestClient(create_app(Settings(root=root,api_key='fixture-only'),analyzer=lambda *args:None)) as client:
                response=client.get(f'/api/v1/jobs/{jid}/compliance',headers={'X-API-Key':'fixture-only'})
                self.assertEqual(response.status_code,200)
                item=response.json()['events'][0]
                self.assertEqual(item['status'],'unresolved');self.assertIsNone(item['rule'])
                self.assertEqual(item['candidate_rules'][0]['rule']['id'],'RK231-22')
                self.assertEqual(item['candidate_rules'][0]['applicability_status'],'not_confirmed')
                self.assertEqual(client.get(f'/api/v1/jobs/{jid}',headers={'X-API-Key':'fixture-only'}).json(),result)
                passport=client.get(f'/api/v1/jobs/{jid}/passport',headers={'X-API-Key':'fixture-only'})
                self.assertEqual(passport.status_code,200)
                self.assertEqual(passport.json()['events'][0]['candidate_rules'][0]['rule']['id'],'RK231-22')
                pdf=client.get(f'/api/v1/jobs/{jid}/report.pdf',headers={'X-API-Key':'fixture-only'})
                self.assertEqual(pdf.status_code,200);self.assertTrue(pdf.content.startswith(b'%PDF'))
