from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cashframe.data import read_json


class ProgressTests(unittest.TestCase):
    def test_temporary_progress_replace_lock_recovers_without_losing_analysis(self):
        from unittest.mock import patch
        import os
        from cashframe.progress import StageReporter
        with TemporaryDirectory() as directory:
            path=Path(directory)/'stage.json';reporter=StageReporter(path)
            reporter('extracting_features');replace=os.replace;attempts=[]
            def locked_once(source,destination):
                attempts.append(1)
                if len(attempts)==1:raise PermissionError('synthetic Windows sharing lock')
                return replace(source,destination)
            with patch('cashframe.progress.os.replace',side_effect=locked_once):
                reporter.progress(2,4,'samples')
            self.assertEqual(read_json(path)['progress']['completed'],2)

    def test_locked_progress_snapshot_is_optional_and_next_update_recovers(self):
        from unittest.mock import patch
        from cashframe.progress import StageReporter
        with TemporaryDirectory() as directory:
            path=Path(directory)/'stage.json';clock=[0.]
            reporter=StageReporter(path,clock=lambda:clock[0]);reporter('extracting_features')
            before=path.read_bytes()
            with patch('cashframe.progress.os.replace',side_effect=PermissionError('synthetic sharing lock')):
                reporter.progress(1,4,'samples')
            self.assertEqual(path.read_bytes(),before)
            clock[0]=1.;reporter.progress(4,4,'samples')
            self.assertEqual(read_json(path)['progress']['completed'],4)

    @unittest.skipUnless(__import__('os').name=='nt','Windows file sharing semantics')
    def test_real_windows_reader_without_delete_share_does_not_abort_progress(self):
        import ctypes
        from ctypes import wintypes
        from cashframe.progress import StageReporter
        with TemporaryDirectory() as directory:
            path=Path(directory)/'stage.json';reporter=StageReporter(path)
            reporter('extracting_features')
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
                wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
            kernel.CreateFileW.restype=wintypes.HANDLE
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.CreateFileW(str(path),0x80000000,3,None,3,0x80,None)
            self.assertNotEqual(handle,wintypes.HANDLE(-1).value)
            try:reporter.progress(1,4,'samples')
            finally:kernel.CloseHandle(handle)
            reporter.progress(4,4,'samples')
            self.assertEqual(read_json(path)['progress']['completed'],4)

    def test_feature_counters_match_actual_processed_samples(self):
        import numpy as np
        from types import SimpleNamespace
        from unittest.mock import patch
        from cashframe.features import extract_features
        counters=[];times=[0.,.25,.5]
        encoder=SimpleNamespace(config={'sample_fps':4.,'input_size':8},encode=lambda image:np.array([1.,2.]))
        samples=[(t,np.zeros((8,8,3),dtype=np.uint8)) for t in times]
        with patch('cashframe.features.frame_times',return_value=times),patch('cashframe.features.iter_samples',return_value=iter(samples)):
            sequence=extract_features('synthetic',encoder,progress_callback=lambda *a:counters.append(a))
        self.assertEqual(counters[0],(0,3,'samples'))
        self.assertEqual(counters[-1],(len(sequence.times),3,'samples'))

    def test_protected_review_exposes_progress_without_changing_jury_contract(self):
        from fastapi.testclient import TestClient
        from cashframe.api import create_app
        from cashframe.config import Settings
        from cashframe.storage import JobStore
        with TemporaryDirectory() as directory:
            settings=Settings(root=Path(directory),api_key='fixture-key')
            headers={'X-API-Key':'fixture-key'}
            with TestClient(create_app(settings,analyzer=lambda *a:None)) as client:
                store=JobStore(settings.local/'jobs.sqlite');jid=store.create()
                store.update(jid,stage='extracting_features',progress={'stage':'extracting_features','completed':3,'total':4,'unit':'samples'})
                url=f'/api/v1/jobs/{jid}'
                self.assertEqual(client.get(url+'/review').status_code,401)
                self.assertEqual(client.get(url+'/compliance').status_code,401)
                self.assertEqual(client.get(url+'/review',headers=headers).json()['progress']['percent'],75)
                self.assertEqual(client.get(url,headers=headers).json(),{'job_id':jid,'status':'processing'})
                store.update(jid,status='done',stage='done',result={'job_id':jid,'status':'done','verdict':'no_violation','violations':[]},review={'events':[]})
                self.assertEqual(client.get(url+'/review',headers=headers).json()['progress']['percent'],100)
                self.assertEqual(client.get(url+'/compliance',headers=headers).json()['events'],[])
                self.assertEqual(set(client.get(url,headers=headers).json()),{'job_id','status','verdict','violations'})

    def test_measured_progress_is_bounded_and_atomic(self):
        from cashframe.progress import StageReporter, validate_progress
        with TemporaryDirectory() as directory:
            path=Path(directory)/'stage.json';clock=[0.]
            reporter=StageReporter(path,clock=lambda:clock[0])
            reporter('extracting_features');reporter.progress(1,4,'samples')
            self.assertEqual(read_json(path)['progress']['percent'],25)
            reporter.progress(2,4,'samples')
            self.assertEqual(read_json(path)['progress']['completed'],1)
            clock[0]=1.;reporter.progress(4,4,'samples')
            self.assertEqual(read_json(path)['progress']['percent'],99)
            self.assertEqual(read_json(path)['progress']['completed'],4)
            self.assertIsNone(validate_progress({'stage':'x','completed':1,'total':0}))
            self.assertIsNone(validate_progress({'stage':'x','completed':True,'total':2}))
            reporter('evidence')
            self.assertIsNone(read_json(path)['progress']['percent'])

    def test_failure_does_not_report_completion(self):
        from cashframe.progress import job_progress
        self.assertNotEqual(job_progress({'status':'failed','stage':'failed'})['percent'],100)
        self.assertEqual(job_progress({'status':'done','stage':'done'})['percent'],100)


class ComplianceTests(unittest.TestCase):
    def test_confirmed_action_survives_without_legal_basis(self):
        from cashframe.compliance import event_compliance
        job={'source_sha256':'a'*64,'duration_sec':60,'review':{'events':[{'id':'one','start_sec':45,'end_sec':49}]}}
        a={'source_sha256':'a'*64,'start_sec':47,'end_sec':48,'origin':'human','confirmed':True,
           'type':'Передача документа','reviewer':'QA','observed_fact':'Synthetic observation',
           'visibility':'clear','rule_status':'unresolved','rule_id':None}
        result=event_compliance(job,[],[a])[0]
        self.assertEqual(result['observed_fact'],a['observed_fact'])
        self.assertEqual(result['status'],'unresolved');self.assertIsNone(result['rule'])

    def test_ambiguous_overlap_does_not_confirm_two_events(self):
        from cashframe.compliance import event_compliance
        job={'source_sha256':'a'*64,'duration_sec':60,'review':{'events':[
            {'id':'one','start_sec':40,'end_sec':48},{'id':'two','start_sec':47,'end_sec':55}]}}
        a={'source_sha256':'a'*64,'start_sec':47,'end_sec':48,'origin':'human','confirmed':True,
           'type':'Передача документа','reviewer':'QA','observed_fact':'Synthetic observation','visibility':'clear',
           'rule_status':'confirmed','rule_id':'r','rule_applicability_confirmed':True}
        rule={'id':'r','verified':True,'url':'https://adilet.zan.kz/rus/docs/test'}
        result=event_compliance(job,[rule],[a])
        self.assertFalse(any(e['status']=='confirmed' for e in result))
        self.assertTrue(all(e['association_status']=='ambiguous' for e in result))

    def test_rule_requires_matching_source_human_visibility_and_applicability(self):
        from cashframe.compliance import event_compliance
        source='a'*64
        job={'source_sha256':source,'duration_sec':60,'result':{'violations':[{'type':'unknown','timecode_sec':47}]},
             'review':{'events':[{'id':'event-1','start_sec':46,'end_sec':49,'type':'unknown'}]}}
        rule={'id':'rule-1','verified':True,'url':'https://adilet.zan.kz/rus/docs/test','title':'Rule','section':'20'}
        annotation={'source_sha256':source,'start_sec':46,'end_sec':48,'origin':'human','confirmed':True,
                    'type':'Передача документа','reviewer':'Tester','observed_fact':'Synthetic observation',
                    'visibility':'clear','rule_id':'rule-1','rule_status':'confirmed','rule_applicability_confirmed':True}
        result=event_compliance(job,[rule],[annotation])[0]
        self.assertEqual(result['status'],'confirmed');self.assertEqual(result['rule']['id'],'rule-1')
        self.assertEqual(result['origin'],'human');self.assertEqual(result['observed_fact'],'Synthetic observation')
        for override in ({'source_sha256':'b'*64},{'visibility':'partial'},{'rule_applicability_confirmed':False},{'origin':'automatic'}):
            result=event_compliance(job,[rule],[dict(annotation,**override)])[0]
            self.assertNotEqual(result['status'],'confirmed')

    def test_no_rule_leak_to_other_event_or_unsafe_url(self):
        from cashframe.compliance import event_compliance
        job={'source_sha256':'a'*64,'duration_sec':60,'result':{'violations':[]},'review':{'events':[
            {'id':'e1','start_sec':1,'end_sec':2},{'id':'e2','start_sec':47,'end_sec':48}]}}
        a={'source_sha256':'a'*64,'start_sec':47,'end_sec':48,'origin':'human','confirmed':True,'type':'Передача документа',
           'observed_fact':'Test','reviewer':'Reviewer','visibility':'clear','rule_id':'r','rule_status':'confirmed','rule_applicability_confirmed':True}
        rule={'id':'r','verified':True,'url':'javascript:alert(1)'}
        self.assertTrue(all(e['rule'] is None for e in event_compliance(job,[rule],[a])))
        rule['url']='https://adilet.zan.kz/rus/docs/test'
        result=event_compliance(job,[rule],[a])
        self.assertEqual(result[0]['status'],'unresolved');self.assertEqual(result[1]['status'],'confirmed')


if __name__=='__main__':unittest.main()
