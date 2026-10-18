import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np

from cashframe.data import sha256_file,write_json
from cashframe.sequence_candidate import ANNOTATION_COLUMNS
from cashframe.sequence_training import train


class SequenceTrainingTest(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec('torch') is not None,
                         'PyTorch is installed separately for local training')
    def test_selection_does_not_open_test_feature_files_or_require_test_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            feature_dir = root/'features'; feature_dir.mkdir()
            baseline_dir = root/'baseline'; baseline_dir.mkdir()
            split = {'train': ['train-n1', 'train-n2', 'train-p1', 'train-p2'],
                     'validation': ['val-n', 'val-p'], 'test': ['test-n', 'test-p']}
            manifest = []
            for partition, ids in split.items():
                for gid in ids:
                    label = int('-p' in gid)
                    manifest.append({'group_id': gid, 'sha256': gid, 'file': gid+'.mov',
                                     'label': label, 'duration_sec': 6., 'points_sec': [2.] if label else []})
                    if partition != 'test':
                        rng = np.random.default_rng(abs(sum(map(ord, gid))))
                        values = rng.normal(0, .1, (24, 384)).astype(np.float32)
                        if label:
                            values[4:16] += .5
                        np.savez(feature_dir/(gid+'.npz'), times=np.arange(24)*.25, values=values)
            encoder = {'model': 'synthetic'}
            write_json(root/'manifest.json', manifest)
            write_json(root/'split.json', split)
            write_json(feature_dir/'metadata.json', {'encoder': encoder})
            write_json(baseline_dir/'config.json', {'split': split, 'encoder': encoder})
            with (root/'review.csv').open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=ANNOTATION_COLUMNS)
                writer.writeheader()
                for group in manifest:
                    if group['group_id'] in split['test']:
                        continue
                    positive = bool(group['label'])
                    writer.writerow({'source_sha256': group['sha256'], 'source_file': group['file'],
                                     'organizer_label': group['label'], 'organizer_point_sec': 2 if positive else '',
                                     'start_sec': 1 if positive else '', 'end_sec': 4 if positive else '',
                                     'visibility': 'clear' if positive else '',
                                     'coverage_complete': 'no' if positive else 'yes',
                                     'reviewer': 'test reviewer', 'confirmed': 'yes' if positive else 'no'})
            result = train(root/'manifest.json', root/'split.json', feature_dir, root/'review.csv',
                           root/'candidate', baseline_dir)
            self.assertEqual(result['status'], 'frozen')
            self.assertTrue((root/'candidate'/'weights.pt').is_file())
            self.assertFalse((feature_dir/'test-p.npz').exists())
            self.assertFalse((feature_dir/'test-n.npz').exists())


class SequenceComparisonTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.baseline=self.root/'baseline';self.baseline.mkdir()
        self.encoder={'model':'synthetic'}
        self.config={'selected_variant':'trained','thresholds':{'trained':.5},
                     'encoder':self.encoder,'window_radius_sec':.01,'window_stride_sec':2.}
        self.head={'mean':np.zeros(3),'std':np.ones(3),'coef':np.array([6.,0.,0.]),'bias':np.asarray(-1.)}
        self.write_baseline()

    def write_baseline(self):
        np.savez(self.baseline/'trained.npz',**self.head)
        write_json(self.baseline/'config.json',self.config)

    def install_video_classifier(self,threshold):
        from cashframe.research import save_frozen
        dimension=len(self.head['mean'])
        model={'mean':np.zeros(dimension),'std':np.ones(dimension),
               'reference':np.ones((1,dimension)),'alpha':np.ones(1),
               'descriptor':'mean','kernel':'linear','ridge':1.,'bandwidth':1.}
        save_frozen(self.baseline,model,threshold,{'encoder':self.encoder})
        self.config['video_classifier']='selected.npz'
        self.write_baseline()

    def test_v1_comparator_uses_both_configured_window_parameters(self):
        from cashframe.sequence_training import baseline_cached_events
        events,positive=baseline_cached_events(self.baseline,np.arange(5.),
                                              np.array([[0.],[1.],[0.],[0.],[0.]]),4.,self.encoder)
        # Radius .01 and stride 2 exclude the spike at 1s. Either default alone includes it.
        self.assertFalse(positive)
        self.assertEqual(events,[])
        events,positive=baseline_cached_events(self.baseline,np.arange(5.),np.ones((5,1)),4.,self.encoder)
        self.assertTrue(positive)
        self.assertEqual(events[0]['localization_status'],'unresolved')

    def test_v2_comparator_uses_frozen_video_head_and_runtime_boundary_tolerance(self):
        from cashframe.sequence_training import baseline_cached_events
        self.head['coef']=np.zeros(3)
        self.install_video_classifier(1/3+np.finfo(np.float64).eps)
        events,positive=baseline_cached_events(self.baseline,np.arange(5.),np.ones((5,1)),4.,self.encoder)
        # The window head is negative; the v2 score is one FP64 rounding step below its inclusive threshold.
        self.assertTrue(positive)
        self.assertEqual(events[0]['localization_status'],'unresolved')
        with self.assertRaises(ValueError):
            baseline_cached_events(self.baseline,np.arange(5.),np.ones((5,1)),4.,{'model':'other'})

    @unittest.skipUnless(importlib.util.find_spec('torch') is not None,
                         'PyTorch is installed separately for local training')
    def test_evaluate_compares_actual_v2_and_counts_every_original_point(self):
        import torch
        from cashframe.sequence_training import evaluate,make_model
        features=self.root/'features';features.mkdir()
        candidate=self.root/'candidate';candidate.mkdir()
        split={'train':[],'validation':[],'test':['normal','positive']}
        manifest=[]
        for gid,label in [('normal',0),('positive',1)]:
            manifest.append({'group_id':gid,'sha256':gid,'file':gid+'.mov','label':label,
                             'duration_sec':12.,'points_sec':[3.,9.] if label else []})
            np.savez(features/(gid+'.npz'),times=np.arange(24)*.5,
                     values=np.full((24,384),float(label),dtype=np.float32))
        write_json(self.root/'manifest.json',manifest);write_json(self.root/'split.json',split)
        write_json(features/'metadata.json',{'encoder':self.encoder})
        review=self.root/'review.csv'
        with review.open('w',newline='',encoding='utf-8') as stream:
            writer=csv.DictWriter(stream,fieldnames=ANNOTATION_COLUMNS);writer.writeheader()
            writer.writerow({'source_sha256':'normal','organizer_label':0,'coverage_complete':'yes','reviewer':'synthetic'})
            writer.writerow({'source_sha256':'positive','organizer_label':1,'start_sec':2,'end_sec':4,
                             'coverage_complete':'yes','reviewer':'synthetic','visibility':'clear','confirmed':'yes'})
        self.head={'mean':np.zeros(1152),'std':np.ones(1152),'coef':np.zeros(1152),'bias':np.asarray(-1.)}
        self.config['split']=split
        self.install_video_classifier(.2)
        model=make_model(2)
        with torch.no_grad():
            for parameter in model.parameters():parameter.zero_()
        torch.save(model.state_dict(),candidate/'weights.pt')
        np.savez(candidate/'normalization.npz',mean=np.zeros(384),std=np.ones(384))
        frozen={'version':1,'selection_partitions':['train','validation'],'encoder':self.encoder,
                'classes':[],'threshold':.6,'window_sec':4.,'stride_sec':1.,'samples':16,
                'manifest_sha256':sha256_file(self.root/'manifest.json'),
                'split_sha256':sha256_file(self.root/'split.json'),
                'training_review_sha256':__import__('hashlib').sha256(b'[]').hexdigest(),
                'weights_sha256':sha256_file(candidate/'weights.pt'),
                'normalization_sha256':sha256_file(candidate/'normalization.npz')}
        write_json(candidate/'frozen.json',frozen)
        report=evaluate(self.root/'manifest.json',self.root/'split.json',features,review,candidate,
                        self.baseline,self.root/'comparison.json')
        self.assertEqual(report['comparison']['baseline']['misses'],0)
        self.assertEqual(report['comparison']['candidate']['misses'],1)
        for name,extra in [('baseline',1),('candidate',0)]:
            localization=report['comparison'][name]['point_localization']
            self.assertEqual(localization['reference'],'original_organizer_points_weak_labels')
            for tolerance in ('2_sec','5_sec'):
                counts=localization[tolerance]
                self.assertEqual((counts['matched'],counts['missed'],counts['extra']),(0,2,extra))
        self.assertNotIn('timecode_error_mean_sec_on_detected_annotated_videos',report['comparison']['baseline'])


class TemporalPointMetricsTest(unittest.TestCase):
    def test_one_good_peak_cannot_hide_a_second_missed_event(self):
        from cashframe.sequence_training import temporal_point_metrics
        result=temporal_point_metrics([{'timecode_sec':3.}],[3.,21.],2.)
        self.assertEqual((result['matched'],result['missed'],result['extra']),(1,1,0))
        self.assertEqual(result['recall'],.5)

    def test_matching_maximizes_cardinality_before_minimizing_error(self):
        from cashframe.sequence_training import temporal_point_metrics
        result=temporal_point_metrics([{'timecode_sec':2.},{'timecode_sec':5.}],[0.,3.],2.)
        self.assertEqual((result['matched'],result['missed'],result['extra']),(2,0,0))
        self.assertEqual(result['mean_absolute_error_sec'],2.)
        result=temporal_point_metrics([{'timecode_sec':8.}],[0.,9.],10.)
        self.assertEqual(result['mean_absolute_error_sec'],1.)

    def test_tolerances_extra_predictions_and_unresolved_events_are_counted(self):
        from cashframe.sequence_training import temporal_point_metrics
        events=[{'timecode_sec':13.},{'timecode_sec':34.},{'timecode_sec':90.},
                {'timecode_sec':10.,'localization_status':'unresolved'}]
        strict=temporal_point_metrics(events,[10.,30.],2.)
        loose=temporal_point_metrics(events,[10.,30.],5.)
        self.assertEqual((strict['matched'],strict['missed'],strict['extra']),(0,2,4))
        self.assertEqual((loose['matched'],loose['missed'],loose['extra']),(2,0,2))
        self.assertEqual(loose['unresolved_predictions'],1)
        self.assertEqual(loose['mean_absolute_error_sec'],3.5)
        empty=temporal_point_metrics([],[],2.)
        self.assertEqual((empty['matched'],empty['missed'],empty['extra']),(0,0,0))
        self.assertIsNone(empty['mean_absolute_error_sec'])


if __name__ == '__main__':
    unittest.main()
