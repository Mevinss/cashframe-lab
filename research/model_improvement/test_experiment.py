import unittest
from unittest.mock import patch
import numpy as np
from research.model_improvement.experiment import SPECS,fit,score,select,select_threshold,run_prepared,PROTOCOL


class ExperimentTests(unittest.TestCase):
    def fixtures(self):
        bags=[np.full((8,3),.05*i) for i in range(8)]+[np.full((8,3),2+.05*i) for i in range(8)]
        return [f'synthetic-{i}' for i in range(16)],bags,[0]*8+[1]*8

    def test_fold_reference_is_training_only_and_data_not_mutated(self):
        _,bags,labels=self.fixtures();before=[b.copy() for b in bags]
        model=fit(bags,labels,SPECS[0]);original=model['negative_mean'].copy()
        score(model,[np.full((9,3),1000.)])
        np.testing.assert_array_equal(original,model['negative_mean'])
        np.testing.assert_allclose(original,np.mean([b.mean(0) for b in bags[:8]],axis=0))
        for a,b in zip(before,bags):np.testing.assert_array_equal(a,b)

    def test_selection_deterministic_and_test_not_an_argument(self):
        ids,bags,labels=self.fixtures()
        a,winner,rows=select(ids,bags,labels);b,again,_=select(ids,bags,labels)
        self.assertEqual(winner,again);self.assertEqual(len(rows),4)
        np.testing.assert_array_equal(a['coef'],b['coef'])
        self.assertFalse(PROTOCOL['test_access'])
        self.assertFalse(PROTOCOL['production_promotion'])

    def test_authorization_gate_before_file_access(self):
        with patch('pathlib.Path.read_text',side_effect=AssertionError('Must not read data')):
            with self.assertRaises(PermissionError):run_prepared('absent','absent','absent','absent','absent')

    def test_finite_shapes_threshold_and_budget(self):
        ids,bags,labels=self.fixtures()
        with self.assertRaises(ValueError):fit([np.array([[np.nan]])],[0],SPECS[0])
        threshold,result=select_threshold([.1,.2,.7,.9],[0,0,1,1]);self.assertGreaterEqual(result['recall'],.75)
        def exhausted():raise RuntimeError('Synthetic budget reached')
        with self.assertRaises(RuntimeError):select(ids,bags,labels,exhausted)


    def test_prepared_runner_never_reads_test_features_or_uses_test_label(self):
        import tempfile,json,hashlib
        from pathlib import Path
        from cashframe.research import fit_video_model,save_frozen
        from cashframe.learning import window_features
        root=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix='synthetic-research-',dir=root/'local') as temporary:
            base=Path(temporary);features=base/'features';features.mkdir();v2=base/'synthetic-reference';v2.mkdir()
            encoder={'synthetic':True};(features/'metadata.json').write_text(json.dumps({'encoder':encoder}))
            (v2/'config.json').write_text(json.dumps({'encoder':encoder}))
            manifest=[];split={'train':[],'validation':[],'test':[]};bags=[];labels=[]
            for partition,count in [('train',16),('validation',4),('test',1)]:
                for index in range(count):
                    gid=hashlib.sha256(f'synthetic-{partition}-{index}'.encode()).hexdigest();split[partition].append(gid)
                    if partition=='test':
                        manifest.append({'group_id':gid,'sha256':gid,'label':'must-not-be-read'})
                        continue
                    label=index%2;values=np.full((8,3),.02*index+2*label);times=np.arange(8)/4
                    np.savez(features/(gid+'.npz'),times=times,values=values)
                    manifest.append({'group_id':gid,'sha256':gid,'label':label,'duration_sec':2.})
                    if partition=='train':
                        bags.append(window_features(times,values,2.)[1]);labels.append(label)
            reference=fit_video_model(bags,labels,{'descriptor':'mean','kernel':'linear','ridge':1.,'bandwidth':1.})
            save_frozen(v2,reference,0.,{'encoder':encoder})
            for name,data in [('manifest',manifest),('split',split)]:
                (base/(name+'.json')).write_text(json.dumps(data))
            original=np.load
            def checked(path,*args,**kwargs):
                if Path(path).stem in split['test']:raise AssertionError('Test feature access forbidden')
                return original(path,*args,**kwargs)
            with patch('numpy.load',side_effect=checked):
                result=run_prepared(base/'manifest.json',base/'split.json',features,base/'new-output',v2,authorized=True)
            self.assertFalse(result['test_evaluated']);self.assertFalse(result['promotion'])
            self.assertTrue((base/'new-output/candidate.npz').is_file())
            with self.assertRaises(ValueError):
                run_prepared(base/'manifest.json',base/'split.json',features,base/'new-output',v2,authorized=True)


if __name__=='__main__':unittest.main()
