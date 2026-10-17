import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from cashframe.api import create_app
from cashframe.config import Settings
from cashframe.storage import JobStore
from cashframe.worker import Worker


class LifecycleTests(unittest.TestCase):
    def test_stalled_upload_has_deadline_and_releases_slot(self):
        with tempfile.TemporaryDirectory() as d:
            settings=Settings(root=Path(d),api_key='test',deadline_sec=.05)
            app=create_app(settings,analyzer=lambda *a:({},{}))
            boundary='test-boundary'
            first=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.mov"\r\nContent-Type: video/quicktime\r\n\r\n').encode()
            sent=[]; reads=0
            async def receive():
                nonlocal reads
                reads+=1
                if reads==1:return {'type':'http.request','body':first,'more_body':True}
                await asyncio.sleep(.4)
                return {'type':'http.request','body':f'\r\n--{boundary}--\r\n'.encode(),'more_body':False}
            async def send(message):sent.append(message)
            scope={'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'POST','scheme':'http','path':'/api/v1/analyze','raw_path':b'/api/v1/analyze','query_string':b'',
                   'headers':[(b'x-api-key',b'test'),(b'content-type',f'multipart/form-data; boundary={boundary}'.encode())],'client':('test',1),'server':('test',80),'root_path':''}
            start=time.monotonic();asyncio.run(app(scope,receive,send))
            response=next(m for m in sent if m['type']=='http.response.start')
            self.assertEqual(response['status'],408)
            self.assertLess(time.monotonic()-start,.3)

    def test_shutdown_terminates_real_child_and_marks_failed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);settings=Settings(root=root,api_key='test')
            store=JobStore(root/'jobs.sqlite');worker=Worker(settings,store)
            path=root/'input.mov';path.write_bytes(b'test')
            jid=store.create();created=[];original_popen=subprocess.Popen
            def sleeping_child(command,**kwargs):
                if '-m' not in command or 'cashframe.cli' not in command:
                    return original_popen(command,**kwargs)
                child=original_popen([sys.executable,'-c','import time; time.sleep(30)'],**kwargs)
                created.append(child);return child
            try:
                with patch('cashframe.worker.subprocess.Popen',side_effect=sleeping_child):
                    worker.reserve();worker.start(jid,path,time.monotonic())
                    for _ in range(100):
                        if created:break
                        time.sleep(.01)
                    self.assertTrue(created)
                    worker.shutdown()
                self.assertIsNotNone(created[0].poll())
                self.assertEqual(store.get(jid)['status'],'failed')
            finally:
                for child in created:
                    if child.poll() is None:child.kill();child.wait()
