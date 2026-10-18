"""Offline, unconfirmed observed-action suggestions. Never an active classifier.

Only public model preparation has network access. Inference runs with socket
operations denied, local-only weights, CPU tensors, and FFmpeg file/pipe inputs.
"""
from collections import Counter
from contextlib import contextmanager
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import numpy as np
from PIL import Image

from .data import read_json, sha256_file
from .label_table import COLUMNS, read_csv
from .taxonomy import _trusted_sources

MODEL_ID='google/siglip-base-patch16-224'
MODEL_REVISION='7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed'
PROMPT_VERSION='cashframe-observed-actions-en/1'
ACTION_PROMPTS={
 'count_cash':('A person is counting paper banknotes by hand.',
               'A bank teller is sorting and counting cash banknotes on a desk.'),
 'leave_workstation':('A person is walking away from their workstation.',
                      'A bank employee is leaving the desk and walking away.'),
 'handle_cash_storage':('A person is opening or closing a cash drawer or cash safe.',
                         'A bank teller is placing cash into a cash drawer or taking it out.'),
 'hand_over_document':('A person is handing a paper document to another person.',
                       'A bank teller and customer are exchanging a paper document.'),
 'transfer_cash':('A person is handing cash banknotes to another person.',
                   'A bank teller and customer are exchanging money banknotes.')}
CONTROL_PROMPTS={
 'ordinary_work':('A person is sitting at a desk doing ordinary office work.',
                   'A bank employee is working at a computer.'),
 'read_document':('A person is reading or writing on a paper document.',
                  'Paper documents are lying on a desk without being handed over.'),
 'unidentified_object':('A person is holding a small object that cannot be identified.',
                        'The objects in the hands are too small to identify.'),
 'occluded_hands':('The hands and their actions are hidden behind a desk.',
                    'The view is obstructed and the action is not visible.'),
 'unclear_action':('A distant surveillance view where the precise action is unclear.',
                    'A blurry low resolution image where the hands cannot be seen clearly.'),
 'stationary_people':('People are standing or talking near a counter.',
                       'A person is standing near a desk without a visible exchange.'),
 'no_visible_action':('An empty workstation with no person doing a visible action.',
                       'A surveillance view without an identifiable hand action.')}
CLASS_IDS=tuple(ACTION_PROMPTS)+tuple(CONTROL_PROMPTS)
THRESHOLDS={'min_cosine':.24,'action_margin':.025,'control_margin':.02,
            'min_supporting_frames':2,'center_frame_must_agree':True}
ENCODER_FILES={'README.md','config.json','model.safetensors','preprocessor_config.json',
               'special_tokens_map.json','spiece.model','tokenizer.json','tokenizer_config.json'}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


class FrozenInputs:
    """Refuse to publish results if trusted input bytes changed during the run."""
    def __init__(self,paths):
        self.paths={name:Path(path) for name,path in paths.items()}
        self.hashes={name:sha256_file(path) for name,path in self.paths.items()}

    def verify(self):
        if any(sha256_file(path)!=self.hashes[name] for name,path in self.paths.items()):
            raise ValueError('Frozen input content changed')


def verify_encoder(root):
    root=Path(root).resolve()
    manifest=read_json(root/'manifest.json')
    if (manifest.get('model_id')!=MODEL_ID or manifest.get('revision')!=MODEL_REVISION
        or manifest.get('license')!='apache-2.0'):
        raise ValueError('Pinned encoder identity/license mismatch')
    files=manifest.get('files',{})
    if not files or 'model.safetensors' not in files:
        raise ValueError('Missing safetensors content hash')
    for name,digest in files.items():
        if name not in ENCODER_FILES or Path(name).name!=name:
            raise ValueError('Unsafe encoder manifest entry')
        target=root/name
        if target.is_symlink() or sha256_file(target)!=digest:
            raise ValueError('Encoder content hash mismatch')
    return manifest


_NETWORK_GUARD=threading.local()
_AUDIT_INSTALLED=False

def _network_audit(event,args):
    if getattr(_NETWORK_GUARD,'blocked',False) and event in (
        'socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.sendto',
        'socket.bind','socket.gethostbyaddr'):
        raise RuntimeError('Network is disabled during local proposal processing')


@contextmanager
def offline_network():
    """Deny network for this whole process; restore Python hooks on leaving.

    The audit hook is permanent but active only while this guard is active.
    Socket method replacement additionally covers inference worker threads.
    FFmpeg children are separately limited to the file and pipe protocols.
    """
    global _AUDIT_INSTALLED
    if not _AUDIT_INSTALLED:
        sys.addaudithook(_network_audit); _AUDIT_INSTALLED=True
    keys={'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1',
          'HF_HUB_DISABLE_IMPLICIT_TOKEN':'1','DO_NOT_TRACK':'1','CUDA_VISIBLE_DEVICES':''}
    previous={key:os.environ.get(key) for key in keys}
    old_connect,old_connect_ex=socket.socket.connect,socket.socket.connect_ex
    old_getaddrinfo,old_sendto=socket.getaddrinfo,socket.socket.sendto
    old_guard=getattr(_NETWORK_GUARD,'blocked',False)
    def deny(*args,**kwargs): raise RuntimeError('Network is disabled during local proposal processing')
    try:
        os.environ.update(keys); _NETWORK_GUARD.blocked=True
        socket.socket.connect=deny; socket.socket.connect_ex=deny
        socket.getaddrinfo=deny; socket.socket.sendto=deny
        yield
    finally:
        socket.socket.connect=old_connect; socket.socket.connect_ex=old_connect_ex
        socket.getaddrinfo=old_getaddrinfo; socket.socket.sendto=old_sendto
        _NETWORK_GUARD.blocked=old_guard
        for key,value in previous.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value


def eligible_points(rows,manifest,split):
    trusted=_trusted_sources(manifest,split)
    selected=[]
    for row in rows:
        group,partition=trusted[row['source_sha256']]
        if partition not in ('train','validation'): continue
        point=row.get('point_sec')
        points=group.get('points_sec') or ([group['point_sec']] if group.get('point_sec') is not None else [])
        if point is None: continue
        if float(point) not in points: raise ValueError('Proposal requests must reference original manifest points')
        selected.append((row,group,partition))
    return selected


def letterbox(image,size=224):
    image=image.convert('RGB')
    ratio=min(size/image.width,size/image.height)
    width,height=max(1,round(image.width*ratio)),max(1,round(image.height*ratio))
    resized=image.resize((width,height),Image.Resampling.BICUBIC)
    canvas=Image.new('RGB',(size,size),(127,127,127))
    canvas.paste(resized,((size-width)//2,(size-height)//2))
    return canvas


def decide_action(scores):
    scores=np.asarray(scores,dtype=float)
    if scores.shape!=(3,len(CLASS_IDS)) or not np.isfinite(scores).all():
        raise ValueError('Expected finite cosine scores for three frames and every frozen prompt group')
    aggregate=scores.mean(axis=0)
    order=np.argsort(aggregate[:len(ACTION_PROMPTS)])[::-1]
    best,second=int(order[0]),int(order[1])
    control=float(aggregate[len(ACTION_PROMPTS):].max())
    candidate=CLASS_IDS[best]
    support=np.argmax(scores,axis=1)==best
    reason='action_proposed'
    if control>=float(aggregate[best])-THRESHOLDS['control_margin']:
        reason='neutral_or_occluded_wins'
    elif aggregate[best]<THRESHOLDS['min_cosine']: reason='weak_similarity'
    elif int(support.sum())<THRESHOLDS['min_supporting_frames'] or not support[1]:
        reason='temporal_disagreement'
    elif aggregate[best]-aggregate[second]<THRESHOLDS['action_margin']:
        reason='ambiguous_actions'
    return {'action_id':candidate if reason=='action_proposed' else 'unknown',
            'reason':reason,'top_action_candidate':candidate,
            'top_cosine':float(aggregate[best]),'action_margin':float(aggregate[best]-aggregate[second]),
            'control_margin':float(aggregate[best]-control),'supporting_frames':int(support.sum()),
            'class_mean_cosine':{key:float(value) for key,value in zip(CLASS_IDS,aggregate)}}


def build_proposal(row,partition,scores,relative_pts,absolute_pts,provenance):
    decision=decide_action(scores)
    if len(relative_pts)!=3 or len(absolute_pts)!=3 or not np.isfinite(relative_pts+absolute_pts).all():
        raise ValueError('Expected three finite original sample PTS')
    if len(set(absolute_pts))<3:
        decision.update(action_id='unknown',reason='insufficient_distinct_samples')
    action=decision['action_id']
    return dict(provenance, schema_version='cashframe.action-proposal/1',
        event_id=row['event_id'],video_id=row['video_id'],source_sha256=row['source_sha256'],
        source_partition=partition,point_sec=float(row['point_sec']),
        origin='automatic',confirmed=False,training_eligible=False,
        proposed_type_id=action,suggested_action=(ACTION_PROMPTS[action][0] if action!='unknown' else ''),
        proposal_status='needs_human_review' if action!='unknown' else 'abstained',
        visibility='unknown',start_sec=None,end_sec=None,candidate_rule_ids=[],
        legal_conclusion=None,pts_relative_sec=list(relative_pts),pts_absolute_sec=list(absolute_pts),
        decision=decision,scores_per_frame=np.asarray(scores).tolist(),score_kind='uncalibrated_cosine_similarity',
        prompt_version=PROMPT_VERSION,prompts_sha256=fingerprint({'actions':ACTION_PROMPTS,'controls':CONTROL_PROMPTS}),
        thresholds=dict(THRESHOLDS),frame_sampling='original point offsets -2/0/+2 seconds; three still frames; not a true interval')


class LocalSiglip:
    def __init__(self,root,threads=4):
        import torch
        from transformers import AutoTokenizer, SiglipModel
        from transformers.utils import logging
        logging.set_verbosity_error()
        torch.set_num_threads(max(1,threads))
        self.torch=torch
        self.model=SiglipModel.from_pretrained(str(root),local_files_only=True,
            trust_remote_code=False,use_safetensors=True).to('cpu').eval()
        tokenizer=AutoTokenizer.from_pretrained(str(root),local_files_only=True,trust_remote_code=False)
        groups=list(ACTION_PROMPTS.values())+list(CONTROL_PROMPTS.values())
        self.sizes=[len(group) for group in groups]
        texts=[text for group in groups for text in group]
        tokens=tokenizer(texts,padding='max_length',max_length=64,truncation=True,return_tensors='pt')
        with torch.inference_mode():
            features=self.model.get_text_features(**tokens)
            self.text_features=features/features.norm(dim=-1,keepdim=True)

    def score(self,images):
        torch=self.torch
        pixels=np.stack([(np.asarray(letterbox(image),dtype=np.float32)/255.-.5)/.5 for image in images])
        tensor=torch.from_numpy(pixels.transpose(0,3,1,2).copy()).to('cpu')
        with torch.inference_mode():
            features=self.model.get_image_features(pixel_values=tensor)
            features=features/features.norm(dim=-1,keepdim=True)
            prompt_scores=(features@self.text_features.T).cpu().numpy()
        starts=np.cumsum([0]+self.sizes)
        return np.stack([prompt_scores[:,a:b].mean(axis=1) for a,b in zip(starts,starts[1:])],axis=1)


def decode_point_frames(path,point,times):
    """Select source indices and preserve absolute PTS; never persist images."""
    from bisect import bisect_left
    requested=[max(0.,point+offset) for offset in (-2.,0.,2.)]
    indices=[min(len(times)-1,bisect_left(times,times[0]+stamp)) for stamp in requested]
    unique=sorted(set(indices))
    # PPM keeps original dimensions; image arrays never leave this local process.
    select='select='+ '+'.join(f'eq(n\\,{index})' for index in unique)
    process=subprocess.run(['ffmpeg','-nostdin','-v','error','-threads','1',
      '-protocol_whitelist','file,pipe','-i',str(path),'-map','0:v:0','-an','-vf',select,'-fps_mode','vfr',
      '-frames:v',str(len(unique)),'-c:v','ppm','-f','image2pipe','pipe:1'],
      capture_output=True,check=True,timeout=90)
    stream=io.BytesIO(process.stdout); images={}
    # PPM consists of exactly width*height*3 uncompressed bytes after its header.
    for index in unique:
        if stream.readline().strip()!=b'P6': raise ValueError('Unexpected local decoded frame format')
        line=stream.readline()
        while line.startswith(b'#'): line=stream.readline()
        width,height=map(int,line.split())
        if not 0<width*height<=3840*2160 or stream.readline().strip()!=b'255':
            raise ValueError('Unsupported decoded frame dimensions')
        pixels=stream.read(width*height*3)
        if len(pixels)!=width*height*3: raise ValueError('Truncated local frame')
        images[index]=Image.frombytes('RGB',(width,height),pixels)
    if stream.read(1): raise ValueError('Unexpected extra local frame bytes')
    return [images[index] for index in indices],[times[index]-times[0] for index in indices],[times[index] for index in indices]


def write_outputs(prefix,proposals,original_rows,excluded_reasons,summary):
    prefix=Path(prefix); prefix.parent.mkdir(parents=True,exist_ok=True)
    paths=[prefix.with_suffix(suffix) for suffix in ('.jsonl','.csv','.assisted-review.csv','.summary.json')]
    if any(path.exists() for path in paths): raise ValueError('Proposal outputs already exist; choose a new output prefix')
    by_event={row['event_id']:row for row in proposals}
    with paths[0].open('x',encoding='utf-8') as stream:
        for row in proposals: stream.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
    columns=['event_id','source_sha256','source_partition','point_sec','proposed_type_id',
             'suggested_action','proposal_status','reason','origin','confirmed','training_eligible',
             'visibility','start_sec','end_sec','candidate_rule_ids','model_revision','prompt_version']
    with paths[1].open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns); writer.writeheader()
        for row in proposals:
            simple={key:row.get(key) for key in columns}
            simple['reason']=row['decision']['reason']; simple['candidate_rule_ids']='[]'
            writer.writerow(simple)
    extra=['suggested_type_id','suggested_action','candidate_rule_ids','proposal_status','model_version','proposal_reason']
    with paths[2].open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(COLUMNS)+extra); writer.writeheader()
        for original in original_rows:
            result=dict(original); proposal=by_event.get(original['event_id'])
            result.update(dict.fromkeys(extra,''))
            if proposal:
                result.update(suggested_type_id=proposal['proposed_type_id'],suggested_action=proposal['suggested_action'],
                  candidate_rule_ids='[]',proposal_status=proposal['proposal_status'],
                  model_version=MODEL_ID+'@'+MODEL_REVISION,proposal_reason=proposal['decision']['reason'])
            else: result['proposal_reason']=excluded_reasons.get(original['event_id'],'no_original_point')
            writer.writerow(result)
    paths[3].write_text(json.dumps(summary,indent=2,allow_nan=False),encoding='utf-8')


def run(manifest_path,split_path,review_path,encoder_root,output_prefix,threads=4):
    """Network denial is activated before any manifest/source/model processing."""
    started=time.perf_counter()
    import psutil
    import torch
    process=psutil.Process(); peak=[process.memory_info().rss]; stop=threading.Event()
    def monitor():
        while not stop.wait(.1): peak[0]=max(peak[0],process.memory_info().rss)
    worker=threading.Thread(target=monitor,daemon=True); worker.start()
    try:
        with offline_network():
            # Verify Python network denial before reading any dataset metadata.
            try: socket.create_connection(('127.0.0.1',9),timeout=.01)
            except RuntimeError: pass
            else: raise ValueError('Network guard self-test failed')
            frozen=FrozenInputs({'manifest':manifest_path,'split':split_path,'review':review_path,
                                 'encoder_manifest':Path(encoder_root)/'manifest.json'})
            manifest,split=read_json(manifest_path),read_json(split_path)
            rows=read_csv(review_path,manifest,split)
            selected=eligible_points(rows,manifest,split)
            encoder=verify_encoder(encoder_root)
            if set(encoder['files'])!=ENCODER_FILES:
                raise ValueError('Complete pinned encoder content manifest required')
            expected_weight='2c63cb7d1f2e95ba501893cbb8faeb4ea9a3af295498d35097126228659c2af8'
            if encoder['files']['model.safetensors']!=expected_weight:
                raise ValueError('Pinned public weight hash mismatch')
            provenance=dict(frozen.hashes,model_id=MODEL_ID,model_revision=MODEL_REVISION,
                model_sha256=encoder['files']['model.safetensors'],
                network_policy='Python sockets denied; FFmpeg file/pipe only',device='cpu',
                implementation_sha256=sha256_file(__file__))
            model=LocalSiglip(encoder_root,threads)
            proposals=[]; hashes={}; cache={}
            from .video import frame_times
            for row,group,partition in selected:
                source_name=str(group['path'])
                path=Path(source_name)
                if source_name.startswith(('\\\\','//')) or '://' in source_name or path.is_symlink():
                    raise ValueError('Only local regular source files are supported')
                path=path.resolve()
                if str(path).startswith(('\\\\','//')):
                    raise ValueError('Only local regular source files are supported')
                digest=sha256_file(path)
                if digest!=group['sha256']: raise ValueError('Original source content hash mismatch')
                hashes[str(path)]=digest
                if digest not in cache: cache[digest]=frame_times(path)
                images,relative_pts,absolute_pts=decode_point_frames(path,float(row['point_sec']),cache[digest])
                scores=model.score(images)
                proposals.append(build_proposal(row,partition,scores,relative_pts,absolute_pts,provenance))
                for image in images: image.close()
            frozen.verify()
            for path,digest in hashes.items():
                if sha256_file(path)!=digest: raise ValueError('Source bytes changed during proposal processing')
            verify_encoder(encoder_root)
            counts=dict(Counter(row['proposed_type_id'] for row in proposals))
            peak[0]=max(peak[0],process.memory_info().rss)
            summary={'status':'research_proposals_complete','eligible_points':len(selected),
                'proposal_count':len(proposals),'original_review_rows':len(rows),'excluded_rows':len(rows)-len(selected),
                'counts':counts,'abstained':counts.get('unknown',0),
                'reasons':dict(Counter(row['decision']['reason'] for row in proposals)),
                'wall_sec':round(time.perf_counter()-started,3),'peak_process_rss_mb':round(peak[0]/1e6,2),
                'ram_scope':'proposal Python process, excluding FFmpeg subprocess peak',
                'source_content_hashes_verified':len(hashes),'input_hashes':frozen.hashes,
                'model_id':MODEL_ID,'model_revision':MODEL_REVISION,'model_sha256':expected_weight,
                'prompt_version':PROMPT_VERSION,'thresholds':THRESHOLDS,
                'device':'cpu','torch_version':torch.__version__,'network_guard_selftest_passed':True,
                'test_frames_read':0,'confirmed':False,'training_eligible':False,
                'limitations':['uncalibrated image-text matching','three still frames do not establish temporal actions',
                  'no human validation','no legal inference','no interval boundaries inferred','no active model changed']}
            trusted=_trusted_sources(manifest,split)
            excluded={row['event_id']:('excluded_heldout' if trusted[row['source_sha256']][1]=='test'
                       else 'excluded_external') for row in rows if row['event_id'] not in {p['event_id'] for p in proposals}}
            # Original scalar CSV values (including blank cells) are preserved in the assisted copy.
            with Path(review_path).open(encoding='utf-8-sig',newline='') as stream: raw_rows=list(csv.DictReader(stream))
            write_outputs(output_prefix,proposals,raw_rows,excluded,summary)
            return summary
    finally:
        stop.set(); worker.join(timeout=1)



