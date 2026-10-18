"""Synthetic MOV through our bridge into an isolated original FastAPI instance.

No production ports, keys, model artifacts or bank videos are used. The analyzer
is a deterministic stub; FFmpeg decode/evidence, HTTP, jobs and reports are real.
"""
import hashlib
from http.client import HTTPConnection
import importlib.util
import json
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import uvicorn
from cashframe.api import create_app
from cashframe.config import Settings
from cashframe.video import evidence_frame

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('e2e_preview',ROOT/'preview.py')
bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)


class SyntheticBridgeIntegration(unittest.TestCase):
    def test_mov_job_evidence_preview_failure_reports_and_manual_annotation(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-audit-e2e-') as directory:
            root=Path(directory);video=root/'synthetic.mov'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=64x96:d=1:r=8','-c:v','libx264',str(video)],check=True,capture_output=True,timeout=10)
            key=secrets.token_urlsafe(24)  # ephemeral, never displayed or persisted by bridge
            def analyzer(path,work,stage):
                stage('evidence');frame=evidence_frame(path,.5)
                violation={'type':'Synthetic action','timecode_sec':frame['timestamp_sec'],'description':'Synthetic geometric fixture','evidence_frames':[frame]}
                return {'verdict':'violation','violations':[violation]}, {'duration_sec':1.,'events':[{**violation,'start_sec':0.,'end_sec':1.,'localization_status':'model_proposal'}],'limitations':['Synthetic technical test only']}
            app=create_app(Settings(root=root,api_key=key),analyzer=analyzer)
            backend_socket=socket.socket();backend_socket.bind(('127.0.0.1',0));backend_socket.listen()
            config=uvicorn.Config(app,log_level='critical',access_log=False,lifespan='on')
            backend=uvicorn.Server(config);thread=threading.Thread(target=backend.run,kwargs={'sockets':[backend_socket]},daemon=True);thread.start()
            proxy=None
            try:
                deadline=time.monotonic()+5
                while not backend.started and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(backend.started)
                class TestServer(bridge.PreviewServer):backend_address=backend_socket.getsockname()
                proxy=TestServer(('127.0.0.1',0),bridge.Handler)
                proxy_thread=threading.Thread(target=proxy.serve_forever,daemon=True);proxy_thread.start()
                def request(path,method='GET',body=None,content_type=None):
                    headers={'X-Cashframe-Client':bridge.CLIENT_HEADER,'X-API-Key':key}
                    if content_type:headers['Content-Type']=content_type
                    connection=HTTPConnection(*proxy.server_address,timeout=10);connection.request(method,'/bridge'+path,body=body,headers=headers)
                    response=connection.getresponse();result=(response.status,dict(response.getheaders()),response.read());connection.close();return result
                self.assertEqual(json.loads(request('/health/ready')[2])['ready'],True)
                boundary='synthetic-boundary'
                body=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.mov"\r\nContent-Type: video/quicktime\r\n\r\n'.encode()+video.read_bytes()+f'\r\n--{boundary}--\r\n'.encode())
                status,_,payload=request('/api/v1/analyze','POST',body,f'multipart/form-data; boundary={boundary}')
                self.assertEqual(status,202);job=json.loads(payload)['job_id'];path='/api/v1/jobs/'+job
                deadline=time.monotonic()+5
                while True:
                    completed=json.loads(request(path)[2])
                    if completed['status']!='processing':break
                    self.assertLess(time.monotonic(),deadline);time.sleep(.02)
                self.assertEqual(completed['status'],'done');self.assertEqual(completed['verdict'],'violation')
                self.assertTrue(completed['violations'][0]['evidence_frames'][0]['image_base64'])
                self.assertEqual(request(path+'/video')[0],409)  # analyzer stub intentionally creates no preview
                for suffix,hash_header in (('/report.pdf','X-Report-SHA256'),('/passport','X-Passport-SHA256')):
                    code,headers,data=request(path+suffix);self.assertEqual(code,200);self.assertEqual(headers[hash_header],hashlib.sha256(data).hexdigest())
                item={'start_sec':0,'end_sec':1,'reviewer':'Synthetic reviewer','observed_fact':'Synthetic blue frame','type':'Synthetic action','visibility':'clear','confirmed':False,'origin':'human','rule_id':None,'rule_status':'unresolved','rule_applicability_confirmed':False}
                self.assertEqual(request(path+'/annotations','POST',json.dumps(item).encode(),'application/json')[0],200)
                self.assertEqual(len(json.loads(request(path+'/annotations')[2])['annotations']),1)
                self.assertEqual(json.loads(request(path)[2])['verdict'],'violation')
            finally:
                if proxy:proxy.shutdown();proxy.server_close();proxy_thread.join(timeout=3)
                backend.should_exit=True;thread.join(timeout=5);backend_socket.close()
                self.assertFalse(thread.is_alive())


if __name__=='__main__':unittest.main()
