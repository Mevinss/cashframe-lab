import unittest
import numpy as np
from cashframe import research

class ResearchTests(unittest.TestCase):
    def test_partition_audit_rejects_duplicate_source_under_different_ids(self):
        rows=[{'group_id':'a','sha256':'same','label':0},{'group_id':'b','sha256':'same','label':1}]
        with self.assertRaisesRegex(ValueError,'source'):
            research.audit_partitions(rows,{'train':['a'],'validation':['b'],'test':[]})

    def test_group_folds_never_split_source_and_are_reproducible(self):
        ids=list('abcdefgh'); labels=[0,1,0,1,0,1,0,1]
        folds=research.group_folds(ids,labels,n_folds=2)
        self.assertEqual(folds,research.group_folds(ids,labels,n_folds=2))
        self.assertEqual(sorted(x for train,valid in folds for x in valid),ids)
        for train,valid in folds:
            self.assertFalse(set(train)&set(valid))
            self.assertEqual(sum(labels[ids.index(x)] for x in valid),2)

    def test_metrics_include_accuracy_and_specificity_with_honest_counts(self):
        got=research.binary_metrics([0,0,1,1],[0,1,1,1])
        self.assertEqual(got['accuracy'],.75)
        self.assertEqual(got['specificity'],.5)
        self.assertEqual(got['balanced_accuracy'],.75)
        self.assertEqual(got['confusion_matrix'],[[1,1],[0,2]])
        self.assertEqual(research.binary_metrics([0,0],[0,0])['recall'],0)

    def test_threshold_rejects_test_and_nonfinite_scores(self):
        with self.assertRaisesRegex(ValueError,'test'):
            research.calibrate_threshold([0,1],[0,1],partition='test')
        with self.assertRaisesRegex(ValueError,'finite'):
            research.calibrate_threshold([0,np.nan],[0,1],partition='validation')
        threshold,result=research.calibrate_threshold([.1,.2,.7,.9],[0,0,1,1],partition='validation')
        self.assertGreater(threshold,.2)
        self.assertLessEqual(threshold,.7)
        self.assertEqual(result['accuracy'],1)

    def test_fit_transform_uses_training_only_and_predicts_unseen_rows(self):
        spec={'descriptor':'mean','kernel':'rbf','ridge':.1,'bandwidth':1.}
        train=[np.array([[v,v+1],[v,v+1]]) for v in [-2,-1,1,2]]
        model=research.fit_video_model(train,[0,0,1,1],spec)
        np.testing.assert_allclose(model['mean'],[0,1])
        before=model['mean'].copy()
        scores=research.score_videos(model,[np.array([[-1,-0.]]),np.array([[1,2.]]),np.array([[1000,1000.]])])
        np.testing.assert_array_equal(model['mean'],before)
        self.assertLess(scores[0],scores[1])
        self.assertTrue(np.isfinite(scores).all())

    def test_selection_has_no_test_input_and_frozen_artifact_is_reproducible(self):
        train=[np.array([[v,v+1],[v,v+1]]) for v in [-4,-3,-2,-1,1,2,3,4]]
        spec=[{'descriptor':'mean','kernel':'rbf','ridge':.1,'bandwidth':1.}]
        result=research.select_from_train_validation(list('abcdefgh'),train,[0]*4+[1]*4,train[:2]+train[-2:],[0,0,1,1],spec,n_folds=2)
        self.assertEqual(result['selected_spec'],spec[0])
        self.assertEqual(result['validation']['accuracy'],1)
        self.assertEqual(result['selection_partitions'],['train','validation'])
        self.assertNotIn('test',result)



class FrozenModelTests(unittest.TestCase):
    def test_frozen_model_roundtrip_and_corruption_rejection(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        train=[np.array([[v,v+1]]) for v in [-2,-1,1,2]]
        model=research.fit_video_model(train,[0,0,1,1],{'descriptor':'mean','kernel':'linear','ridge':.1,'bandwidth':1.})
        with TemporaryDirectory() as folder:
            research.save_frozen(folder,model,.2,{'selection_partitions':['train','validation']})
            loaded,threshold,config=research.load_frozen(folder)
            self.assertEqual(threshold,.2)
            np.testing.assert_allclose(research.score_videos(loaded,train),research.score_videos(model,train))
            with (Path(folder)/'selected.npz').open('ab') as stream:stream.write(b'corrupted')
            with self.assertRaisesRegex(ValueError,'hash'):
                research.load_frozen(folder)

class ExperimentRunnerTests(unittest.TestCase):
    def test_select_phase_never_opens_test_feature_or_reads_its_label(self):
        import runpy,json
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as folder:
            root=Path(folder);features=root/'features';features.mkdir()
            manifest=[];split={'train':[],'validation':[],'test':['heldout']}
            for partition,n in [('train',8),('validation',4)]:
                for i in range(n):
                    gid=partition+str(i);label=i%2;split[partition].append(gid)
                    manifest.append({'group_id':gid,'sha256':gid,'label':label,'duration_sec':1.,'points_sec':[.5] if label else []})
                    np.savez(features/(gid+'.npz'),times=np.array([0.,.5,1.]),values=np.full((3,2),-1. if not label else 1.))
            manifest.append({'group_id':'heldout','sha256':'heldout','label':'must-not-read','duration_sec':1.})
            for name,value in [('manifest',manifest),('split',split)]:
                (root/(name+'.json')).write_text(json.dumps(value),encoding='utf8')
            (features/'metadata.json').write_text(json.dumps({'encoder':{'synthetic':True}}),encoding='utf8')
            runner=runpy.run_path(str(Path(__file__).parents[1]/'scripts'/'experiment_v2.py'))
            runner['main'](['--manifest',str(root/'manifest.json'),'--split',str(root/'split.json'),'--features',str(features),'--output',str(root/'output'),'--phase','select','--old-model',str(root/'absent-old-model')])
            self.assertTrue((root/'output'/'selection-frozen.json').exists())
            self.assertFalse((root/'output'/'test-report.json').exists())


class OptInTests(unittest.TestCase):
    def test_opt_in_clones_base_and_preserves_original_config(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import json
        train=[np.array([[v,v+1]]) for v in [-2,-1,1,2]]
        model=research.fit_video_model(train,[0,0,1,1],{'descriptor':'mean','kernel':'linear','ridge':.1,'bandwidth':1.})
        with TemporaryDirectory() as folder:
            root=Path(folder);base=root/'base';base.mkdir();candidate=root/'candidate';target=root/'target'
            original={'encoder':{'model':'synthetic'},'selected_variant':'trained','thresholds':{'trained':.5}}
            (base/'config.json').write_text(json.dumps(original),encoding='utf8')
            (base/'trained.npz').write_bytes(b'old-weight-fixture')
            research.save_frozen(candidate,model,.2,{'encoder':{'model':'synthetic'}})
            research.prepare_opt_in(base,candidate,target)
            self.assertEqual(json.loads((base/'config.json').read_text()),original)
            self.assertEqual((target/'trained.npz').read_bytes(),b'old-weight-fixture')
            self.assertEqual(json.loads((target/'config.json').read_text())['video_classifier'],'selected.npz')
            self.assertTrue((target/'selection-frozen.json').is_file())
            with self.assertRaises(FileExistsError):research.prepare_opt_in(base,candidate,target)




class EncoderCompatibilityTests(unittest.TestCase):
    def test_loader_rejects_encoder_config_mismatch(self):
        from tempfile import TemporaryDirectory
        train=[np.array([[v,v+1]]) for v in [-2,-1,1,2]]
        model=research.fit_video_model(train,[0,0,1,1],{'descriptor':'mean','kernel':'linear','ridge':.1,'bandwidth':1.})
        with TemporaryDirectory() as folder:
            research.save_frozen(folder,model,.2,{'encoder':{'revision':'one'}})
            with self.assertRaisesRegex(ValueError,'encoder'):
                research.load_frozen(folder,expected_encoder={'revision':'two'})
            loaded,threshold,_=research.load_frozen(folder,expected_encoder={'revision':'one'})
            self.assertEqual(threshold,.2)




class ResourceBudgetTests(unittest.TestCase):
    def test_selection_stops_on_budget_before_fitting_or_calibrating(self):
        windows=[np.array([[float(i)]]) for i in range(8)]
        def exhausted():raise RuntimeError('experiment budget exhausted')
        with self.assertRaisesRegex(RuntimeError,'budget'):
            research.select_from_train_validation(list('abcdefgh'),windows,[0]*4+[1]*4,
                windows[:2],[0,1],n_folds=2,check_budget=exhausted)


if __name__=='__main__':unittest.main()
