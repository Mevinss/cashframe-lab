import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from cashframe.annotations import validate_annotation
from cashframe.api import create_app
from cashframe.config import Settings
from cashframe.data import write_json
from cashframe.events import propose_events
from cashframe.learning import load_model, model_scores, video_score, window_features, inference_window_parameters
from cashframe.readiness import validate_runtime, RuntimeReadiness
from cashframe.research import score_videos
from cashframe.storage import JobStore
from cashframe.worker import Worker


class NumericalSafetyTests(unittest.TestCase):
    def test_pipeline_uses_the_parameters_in_its_passport(self):
        from cashframe.pipeline import analyze_video
        from cashframe.features import FeatureSequence
        from cashframe.video import VideoInfo
        with TemporaryDirectory() as folder:
            root=Path(folder);source=root/'input.mov';source.write_bytes(b'fixture')
            encoder_dir=root/'encoder';encoder_dir.mkdir();(encoder_dir/'weights.pth').write_bytes(b'fixture')
            config={'selected_variant':'trained','thresholds':{'trained':.5},'event_threshold':.7,
                    'encoder':{'sample_fps':4.,'input_size':8},'window_radius_sec':.25,'window_stride_sec':1.}
            write_json(root/'config.json',config)
            np.savez(root/'trained.npz',mean=np.zeros(6),std=np.ones(6),coef=np.zeros(6),bias=-10.)
            sequence=FeatureSequence(np.arange(0,2,.25),np.ones((8,2)),.1,[0.,1.])
            with patch('cashframe.pipeline.probe_video',return_value=VideoInfo(2,64,64,0,'h264')),patch('cashframe.pipeline.validate_decode'),patch('cashframe.pipeline.Encoder') as encoder,patch('cashframe.pipeline.extract_features',return_value=sequence),patch('cashframe.pipeline.window_features',wraps=window_features) as windows:
                encoder.return_value.path=encoder_dir;encoder.return_value.config=config['encoder']
                result,review=analyze_video(source,root,root/'work',encoder_dir=encoder_dir)
            self.assertEqual(windows.call_args.kwargs,{'radius':.25,'stride':1.})
            self.assertEqual(review['version_snapshot']['parameters']['window_radius_sec'],.25)
            self.assertEqual(review['version_snapshot']['parameters']['window_stride_sec'],1.)
            self.assertEqual(review['version_snapshot']['parameters']['event_threshold'],.7)
            self.assertEqual(result['verdict'],'no_violation')

    def model(self):
        return dict(kind='linear', mean=np.zeros(6), std=np.ones(6), coef=np.ones(6), bias=0.)

    def test_nan_inf_model_and_input_never_become_negative(self):
        for value in (np.nan, np.inf, -np.inf):
            for key in ('mean', 'std', 'coef', 'bias'):
                model=self.model()
                if key=='bias':model[key]=value
                else:model[key][0]=value
                with self.subTest(value=value,key=key),self.assertRaises(ValueError):
                    video_score(model,np.ones((2,6)))
            windows=np.ones((2,6));windows[0,0]=value
            with self.assertRaises(ValueError):model_scores(self.model(),windows)

    def test_corrupt_model_shapes_std_and_thresholds_rejected(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            for value in (np.nan,np.inf):
                model=self.model();model.pop('kind')
                np.savez(root/'trained.npz',**model)
                write_json(root/'config.json',{'selected_variant':'trained','thresholds':{'trained':value}})
                with self.assertRaises(ValueError):load_model(root)
        model=self.model();model['std'][0]=0
        with self.assertRaises(ValueError):model_scores(model,np.ones((2,6)))
        model=self.model();model['coef']=np.ones(5)
        with self.assertRaises(ValueError):model_scores(model,np.ones((2,6)))

    def test_nonfinite_video_head_rejected(self):
        model=dict(descriptor='mean',mean=np.zeros(2),std=np.ones(2),reference=np.ones((2,2)),alpha=np.array([np.nan,1.]),kernel='linear')
        with self.assertRaises(ValueError):score_videos(model,[np.ones((2,2))])

    def test_overflow_is_not_hidden_by_clipping(self):
        model=self.model();model['std'][0]=1e-300
        with np.errstate(over='ignore'),self.assertRaises(ValueError):
            model_scores(model,np.full((2,6),1e100))

    def test_effective_window_configuration_changes_computation(self):
        times=np.arange(0,4,.25);features=times[:,None]
        config={'window_radius_sec':.25,'window_stride_sec':1.}
        centers,windows=window_features(times,features,4.,**inference_window_parameters(config))
        self.assertEqual(centers.tolist(),[0,1,2,3,4])
        self.assertAlmostEqual(windows[1,1],np.std([.75,1.,1.25]))
        for key in config:
            with self.assertRaises(ValueError):inference_window_parameters({**config,key:np.nan})

    def test_flat_and_below_threshold_predictions_do_not_invent_action_time(self):
        times=np.arange(0,10,.5)
        for scores in (np.full(len(times),.9),np.linspace(.1,.2,len(times))):
            events=propose_events(times,scores,.8,10.,True)
            self.assertEqual(events[0]['localization_status'],'unresolved')
            self.assertEqual(events[0]['timecode_sec'],0.)
            self.assertIn('контекст',events[0]['description'])
        scores=np.zeros(len(times));scores[8]=.9
        self.assertEqual(propose_events(times,scores,.8,10.,True)[0]['timecode_sec'],4.)

    def test_fragmented_positive_preserves_verdict_with_bounded_context(self):
        times=np.arange(0,100,.5);scores=np.zeros(len(times));scores[::8]=.9
        events=propose_events(times,scores,.8,100.,True)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['localization_reason'],'fragmented_signal')
        self.assertEqual(events[0]['proposed_interval_count'],25)
        self.assertEqual(events[0]['timecode_sec'],0.)


class ApiSafetyTests(unittest.TestCase):
    def test_disk_budget_rejects_before_upload_is_stored(self):
        from collections import namedtuple
        disk=namedtuple('Disk','total used free')(1000,1000,0)
        with TemporaryDirectory() as folder:
            settings=Settings(root=Path(folder),api_key='test')
            with TestClient(create_app(settings,analyzer=lambda *a:None)) as client,patch('cashframe.api.shutil.disk_usage',return_value=disk):
                self.assertEqual(client.post('/api/v1/analyze',headers={'X-API-Key':'test'}).status_code,507)
                self.assertEqual(list((settings.local/'jobs').iterdir()),[])

    def test_unicode_header_returns_401_instead_of_exception(self):
        with TemporaryDirectory() as folder:
            settings=Settings(root=Path(folder),api_key='ascii-key')
            with TestClient(create_app(settings,analyzer=lambda *a:None)) as client:
                response=client.get('/api/v1/jobs/unknown',headers=[(b'X-API-Key','неверный'.encode('utf-8'))])
                self.assertEqual(response.status_code,401)

    def test_annotation_extras_body_limit_and_stream_limit(self):
        with self.assertRaises(ValueError):validate_annotation({'start_sec':0,'end_sec':1,'extra':{}},2)
        with TemporaryDirectory() as folder:
            settings=Settings(root=Path(folder),api_key='test')
            store=JobStore(settings.local/'jobs.sqlite');jid=store.create()
            store.update(jid,status='done',duration_sec=2,source_sha256='a'*64)
            with TestClient(create_app(settings,analyzer=lambda *a:None)) as client:
                url=f'/api/v1/jobs/{jid}/annotations';headers={'X-API-Key':'test','Content-Type':'application/json'}
                self.assertEqual(client.post(url,headers=headers,json={'start_sec':0,'end_sec':1,'extra':'x'}).status_code,422)
                self.assertEqual(client.post(url,headers=headers,content=b' '*17000).status_code,413)
                self.assertEqual(client.post(url,headers=headers,content=iter([b' '*9000,b' '*9000])).status_code,413)
                self.assertFalse((settings.local/'annotations').exists())

    def test_patched_starlette_and_range_response(self):
        from importlib.metadata import version
        self.assertGreaterEqual(tuple(map(int,version('starlette').split('.')[:2])),(0,49))
        self.assertNotEqual(version('starlette'),'0.49.0')
        with TemporaryDirectory() as folder:
            with TestClient(create_app(Settings(root=Path(folder),api_key='test'),analyzer=lambda *a:None)) as client:
                response=client.get('/static/app.js',headers={'Range':'bytes=0-9'})
                self.assertEqual(response.status_code,206)
                self.assertEqual(len(response.content),10)

    def test_failed_preview_cannot_erase_completed_result(self):
        with TemporaryDirectory() as folder:
            settings=Settings(root=Path(folder),api_key='test')
            store=JobStore(settings.local/'jobs.sqlite');jid=store.create()
            store.update(jid,status='done',result={'verdict':'no_violation','violations':[]},review={})
            path=settings.local/'jobs'/jid/'input.mov';path.parent.mkdir(parents=True);path.write_bytes(b'fixture')
            worker=Worker(settings,store)
            with patch('cashframe.worker.subprocess.Popen',side_effect=OSError('preview unavailable')):
                worker._prepare_preview(jid,path)
            job=store.get(jid)
            self.assertEqual(job['status'],'done')
            self.assertEqual(job['review']['preview_status'],'unavailable')
            self.assertEqual(job['result']['verdict'],'no_violation')


class ReadinessTests(unittest.TestCase):
    def test_runtime_loads_encoder_and_exercises_classifier(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);metadata={'input_size':8,'model':'test','sample_fps':4.}
            write_json(root/'config.json',{'selected_variant':'trained','thresholds':{'trained':.5},'encoder':metadata})
            np.savez(root/'trained.npz',mean=np.zeros(6),std=np.ones(6),coef=np.zeros(6),bias=0.)
            with patch('cashframe.features.Encoder') as encoder,patch('cashframe.pipeline.block_outgoing_network'),patch('cashframe.readiness.subprocess.run'):
                encoder.return_value.config=metadata;encoder.return_value.encode.return_value=np.ones(2)
                self.assertTrue(validate_runtime(root,root)['ready'])
                encoder.assert_called_once_with(root)
                encoder.return_value.encode.assert_called_once()
                encoder.return_value.encode.return_value=np.array([np.nan,1.])
                with self.assertRaises(ValueError):validate_runtime(root,root)

    def test_success_cache_invalidates_when_artifact_changes(self):
        with TemporaryDirectory() as folder:
            settings=Settings(root=Path(folder));settings.model_dir.mkdir(parents=True)
            artifact=settings.model_dir/'trained.npz';artifact.write_bytes(b'first')
            readiness=RuntimeReadiness(settings)
            with patch('cashframe.readiness.subprocess.run') as run:
                run.return_value.returncode=0
                self.assertTrue(readiness.check());self.assertTrue(readiness.check());self.assertEqual(run.call_count,1)
                artifact.write_bytes(b'changed')
                run.return_value.returncode=1
                self.assertFalse(readiness.check());self.assertEqual(run.call_count,2)


if __name__=='__main__':unittest.main()
