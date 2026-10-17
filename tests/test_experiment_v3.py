import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from cashframe.data import write_json
from cashframe.research import describe_video

spec=importlib.util.spec_from_file_location('experiment_v3',Path(__file__).resolve().parents[1]/'scripts/experiment_v3.py')
experiment=importlib.util.module_from_spec(spec);spec.loader.exec_module(experiment)


class TemporalExperimentTests(unittest.TestCase):
    def test_temporal_descriptors_respond_to_order_and_have_fixed_size(self):
        values=np.arange(24).reshape(8,3)
        self.assertFalse(np.array_equal(describe_video(values,'temporal_pyramid'),describe_video(values[::-1],'temporal_pyramid')))
        self.assertEqual(describe_video(values[:1],'temporal_pyramid').shape,(15,))
        self.assertEqual(describe_video(values[:1],'temporal_pyramid')[3:6].tolist(),[0.,0.,0.])
        self.assertEqual(describe_video(values,'dynamics').shape,(9,))

    def test_conservative_threshold_requires_recall_and_specificity(self):
        self.assertIsNone(experiment.conservative_threshold([.9,.8,.1,.2],[0,0,1,1]))
        threshold,metrics=experiment.conservative_threshold([.1,.2,.7,.8],[0,0,1,1])
        self.assertGreater(threshold,.2);self.assertEqual(metrics['specificity'],1.)

    def test_selection_never_reads_test_features_or_labels(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);features=root/'features';features.mkdir();base=root/'base';base.mkdir()
            split={'train':[f't{i}' for i in range(8)],'validation':[f'v{i}' for i in range(4)],'test':['locked']}
            rows=[]
            for partition in ('train','validation'):
                for i,gid in enumerate(split[partition]):
                    label=i%2;rows.append({'group_id':gid,'sha256':gid,'label':label,'duration_sec':2.})
                    np.savez(features/(gid+'.npz'),times=np.arange(0,2,.25),values=np.full((8,2),label*3.))
            rows.append({'group_id':'locked','sha256':'locked','label':'MUST_NOT_READ'})
            write_json(root/'manifest.json',rows);write_json(root/'split.json',split)
            write_json(base/'config.json',{'selected_variant':'trained','thresholds':{'trained':.1},'encoder':{}})
            np.savez(base/'trained.npz',mean=np.zeros(6),std=np.ones(6),coef=np.zeros(6),bias=0.)
            result=experiment.run(root/'manifest.json',root/'split.json',features,root/'experiment',base)
            self.assertFalse(result['test_evaluated']);self.assertEqual(result['validation']['accuracy'],1.)
            self.assertNotIn('locked',result['feature_hashes'])
            with self.assertRaises(FileExistsError):experiment.run(root/'manifest.json',root/'split.json',features,root/'experiment',base)
