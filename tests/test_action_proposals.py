"""Synthetic-only safeguards for the offline research proposal tool."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from PIL import Image
from cashframe.action_proposals import (ACTION_PROMPTS, CONTROL_PROMPTS, FrozenInputs,
    build_proposal, decide_action, eligible_points, letterbox, offline_network,
    verify_encoder, write_outputs)
from cashframe.label_table import export_rows


class ActionProposalTests(unittest.TestCase):
    def scores(self, winner=0, value=.45):
        result=np.full((3,len(ACTION_PROMPTS)+len(CONTROL_PROMPTS)),.1)
        result[:,winner]=value
        return result

    def test_action_requires_absolute_margin_temporal_support_and_neutral_controls(self):
        self.assertEqual(decide_action(self.scores())['action_id'],'count_cash')
        for values, reason in [(self.scores(value=.15),'weak_similarity'),
            (self.scores() + np.eye(3,len(ACTION_PROMPTS)+len(CONTROL_PROMPTS))*.5,'temporal_disagreement')]:
            self.assertEqual(decide_action(values)['action_id'],'unknown')
        values=self.scores(); values[:,len(ACTION_PROMPTS)]=.5
        self.assertEqual(decide_action(values)['reason'],'neutral_or_occluded_wins')
        values=self.scores(); values[:,1]=.44
        self.assertEqual(decide_action(values)['reason'],'ambiguous_actions')
        for values in (np.zeros((2,7)),np.full((3,8),np.nan)):
            with self.assertRaises(ValueError): decide_action(values)

    def test_proposal_never_confirms_or_creates_training_label_interval_or_visibility(self):
        row={'event_id':'e','source_sha256':'a'*64,'point_sec':3,'video_id':'v'}
        result=build_proposal(row,'train',self.scores(),[1,3,5],[11,13,15],{'model_sha256':'b'*64})
        self.assertEqual(result['origin'],'automatic')
        self.assertIs(result['confirmed'],False)
        self.assertIs(result['training_eligible'],False)
        self.assertEqual(result['visibility'],'unknown')
        self.assertIsNone(result['start_sec']); self.assertIsNone(result['end_sec'])
        self.assertEqual(result['candidate_rule_ids'],[])
        self.assertEqual(result['pts_absolute_sec'],[11,13,15])
        self.assertEqual(result['model_sha256'],'b'*64)
        self.assertNotIn('confidence',result)

    def test_held_out_and_external_excluded_before_source_files_are_accessed(self):
        manifest=[{'group_id':p,'sha256':str(i)*64,'file':p+'.mov','path':'MISSING',
          'duration_sec':10,'points_sec':[3]} for i,p in enumerate(('a','b','c','d'),1)]
        split={'train':['a'],'validation':['b'],'test':['c']}
        rows=export_rows(manifest,split)
        selected=eligible_points(rows,manifest,split)
        self.assertEqual([part for _,_,part in selected],['train','validation'])
        rows[0]['point_sec']=4
        with self.assertRaises(ValueError): eligible_points(rows,manifest,split)
        with self.assertRaises(ValueError): eligible_points(rows,manifest,split|{'test':['a']})

    def test_frozen_input_bytes_must_not_change(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'manifest.json'; path.write_text('[]')
            frozen=FrozenInputs({'manifest':path}); frozen.verify()
            path.write_text('[ ]')
            with self.assertRaises(ValueError): frozen.verify()

    def test_encoder_manifest_checks_actual_content_and_rejects_pickle_or_path_escape(self):
        with TemporaryDirectory() as directory:
            root=Path(directory); (root/'model.safetensors').write_bytes(b'synthetic')
            import hashlib
            payload={'model_id':'google/siglip-base-patch16-224','revision':'7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed',
              'license':'apache-2.0','files':{'model.safetensors':hashlib.sha256(b'synthetic').hexdigest()}}
            (root/'manifest.json').write_text(json.dumps(payload))
            verify_encoder(root)
            (root/'model.safetensors').write_bytes(b'changed')
            with self.assertRaises(ValueError): verify_encoder(root)
            payload['files']={'../escape.bin':'0'*64}
            (root/'manifest.json').write_text(json.dumps(payload))
            with self.assertRaises(ValueError): verify_encoder(root)

    def test_letterbox_preserves_edges_without_crop(self):
        original=Image.new('RGB',(10,20),'red'); original.putpixel((0,0),(0,255,0))
        boxed=letterbox(original,20)
        self.assertEqual(boxed.size,(20,20)); self.assertEqual(boxed.getpixel((5,0)),(0,255,0))
        self.assertEqual(boxed.getpixel((0,0)),(127,127,127))

    def test_assisted_copy_preserves_every_original_cell_and_heldout_blank_suggestions(self):
        import csv
        from cashframe.label_table import COLUMNS
        original=[dict.fromkeys(COLUMNS,'') for _ in range(2)]
        original[0].update(event_id='train-event',point_sec='03.000',annotation_status='unreviewed')
        original[1].update(event_id='test-event',point_sec='7',annotation_status='unreviewed')
        proposal={'event_id':'train-event','proposed_type_id':'unknown','suggested_action':'',
          'proposal_status':'abstained','decision':{'reason':'weak_similarity'}}
        with TemporaryDirectory() as directory:
            prefix=Path(directory)/'new-proposals'
            write_outputs(prefix,[proposal],original,{'test-event':'excluded_heldout'},{'count':1})
            with prefix.with_suffix('.assisted-review.csv').open(encoding='utf-8-sig',newline='') as stream:
                rows=list(csv.DictReader(stream))
            for expected,row in zip(original,rows):
                self.assertEqual({key:row[key] for key in COLUMNS},expected)
            self.assertEqual(rows[1]['proposal_reason'],'excluded_heldout')
            for key in ('suggested_type_id','suggested_action','candidate_rule_ids','proposal_status','model_version'):
                self.assertEqual(rows[1][key],'')
            with self.assertRaises(ValueError): write_outputs(prefix,[],original,{},{} )
    def test_duplicate_sample_pts_cannot_multiply_temporal_evidence(self):
        row={'event_id':'e','source_sha256':'a'*64,'point_sec':0,'video_id':'v'}
        result=build_proposal(row,'train',self.scores(),[0,0,2],[10,10,12],{})
        self.assertEqual(result['proposed_type_id'],'unknown')
        self.assertEqual(result['decision']['reason'],'insufficient_distinct_samples')

    def test_synthetic_video_decodes_exact_original_pts_and_full_frame(self):
        import shutil, subprocess
        from cashframe.action_proposals import decode_point_frames
        from cashframe.video import frame_times
        if not shutil.which('ffmpeg'): self.skipTest('FFmpeg not available')
        with TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.mkv'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
              'color=c=blue:s=20x10:r=1:d=6','-vf','setpts=PTS+10/TB','-fps_mode','vfr',
              '-c:v','ffv1',str(path)],capture_output=True,check=True,timeout=15)
            times=frame_times(path)
            images,relative,absolute=decode_point_frames(path,3,times)
            self.assertEqual(relative,[1,3,5]); self.assertEqual(absolute,[11,13,15])
            self.assertEqual([image.size for image in images],[(20,10)]*3)
            for image in images: image.close()
    def test_multistream_decoding_uses_same_first_stream_as_pts_probe(self):
        import shutil, subprocess
        from cashframe.action_proposals import decode_point_frames
        from cashframe.video import frame_times
        if not shutil.which('ffmpeg'): self.skipTest('FFmpeg not available')
        with TemporaryDirectory() as directory:
            path=Path(directory)/'two-streams.mkv'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i',
              'color=c=blue:s=20x10:r=1:d=6','-f','lavfi','-i',
              'color=c=red:s=400x200:r=1:d=6','-filter_complex',
              '[0:v]setpts=PTS+10/TB[a];[1:v]setpts=PTS+10/TB[b]',
              '-map','[a]','-map','[b]','-disposition:v:0','0','-disposition:v:1','default',
              '-fps_mode','vfr','-c:v','ffv1',str(path)],
              capture_output=True,check=True,timeout=15)
            images,relative,absolute=decode_point_frames(path,3,frame_times(path))
            self.assertEqual([image.size for image in images],[(20,10)]*3)
            self.assertEqual(relative,[1,3,5]); self.assertEqual(absolute,[11,13,15])
            for image in images:
                red,_,blue=image.getpixel((0,0)); self.assertGreater(blue,red); image.close()
    def test_preparation_resumes_public_partial_bytes_and_rejects_range_ignored(self):
        import importlib.util
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('prepare_actions',Path(__file__).parents[1]/'scripts/prepare_action_encoder.py')
        prepare=importlib.util.module_from_spec(spec); spec.loader.exec_module(prepare)
        class Response:
            def __init__(self,status):
                self.status=status; self.headers={'Content-Range':'bytes 5-10/11'}; self.parts=iter([b'second',b''])
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,size): return next(self.parts)
        with TemporaryDirectory() as directory:
            target=Path(directory)/'public-weights'; partial=Path(directory)/'public-weights.part'
            partial.write_bytes(b'first')
            with patch.object(prepare.urllib.request,'urlopen',return_value=Response(206)) as request:
                prepare.download_with_resume('https://example.invalid/public',target)
                self.assertEqual(request.call_args.args[0].get_header('Range'),'bytes=5-')
            self.assertEqual(target.read_bytes(),b'firstsecond')
            partial.write_bytes(b'first')
            with patch.object(prepare.urllib.request,'urlopen',return_value=Response(200)):
                with self.assertRaises(ValueError): prepare.download_with_resume('https://example.invalid/public',target)
            self.assertEqual(partial.read_bytes(),b'first')
    def test_network_socket_is_blocked(self):
        import socket
        with offline_network():
            with self.assertRaises(RuntimeError): socket.create_connection(('example.com',443))

if __name__=='__main__': unittest.main()






