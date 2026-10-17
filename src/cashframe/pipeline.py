from pathlib import Path
import time

import numpy as np

from .data import read_json,write_json
from .events import merge_events
from .features import Encoder,extract_features,FeatureSequence
from .learning import load_model,model_scores,window_features,video_score
from .video import browser_copy,evidence_frame,probe_video,validate_decode,iter_samples
from .provenance import capture_versions


def should_refine(features_sec,elapsed_sec,duration_sec,events,budget_sec):
    coverage=min(1.,sum(max(0,e['end_sec']-e['start_sec']) for e in events)/max(duration_sec,.001))
    estimated=max(1.,features_sec)*2*coverage+2
    return budget_sec-elapsed_sec-60>estimated


def refine_features(base,samples,encoder,events):
    rows={round(float(t),6):(float(t),v) for t,v in zip(base.times,base.values)}
    for timestamp,frame in samples:
        if any(e['start_sec']<=timestamp<=e['end_sec'] for e in events):
            key=round(float(timestamp),6)
            if key not in rows:rows[key]=(timestamp,encoder.encode(frame))
    ordered=sorted(rows.values(),key=lambda r:r[0])
    return FeatureSequence(np.array([r[0] for r in ordered]),np.stack([r[1] for r in ordered]),base.wall_sec,base.source_times)


def block_outgoing_network():
    import socket
    def denied(*args,**kwargs):raise RuntimeError('Outgoing network is disabled during analysis')
    socket.socket.connect=denied
    socket.socket.connect_ex=denied
    socket.create_connection=denied
    socket.getaddrinfo=denied


def analyze_video(path,model_dir,work_dir,stage_callback=lambda s:None,encoder_dir=None,budget_sec=240):
    import psutil
    start=time.perf_counter()
    path,model_dir,work_dir=Path(path),Path(model_dir),Path(work_dir)
    work_dir.mkdir(parents=True,exist_ok=True)
    stage_callback('validating')
    info=probe_video(path)
    validate_decode(path)
    stage_callback('loading_model')
    model,config,threshold=load_model(model_dir)
    encoder=Encoder(encoder_dir or model_dir.parent/'encoder')
    if encoder.config!=config['encoder']:raise ValueError('Trained model and encoder/config do not match')
    stage_callback('extracting_features')
    sequence=extract_features(path,encoder)
    type_sequence=sequence
    stage_callback('scoring_windows')
    centers,windows=window_features(sequence.times,sequence.values,info.duration_sec)
    scores=model_scores(model,windows)
    event_threshold=config.get('event_threshold',threshold) if config['selected_variant']=='trained' else threshold
    classifier_status='original'
    if config.get('video_classifier'):
        if config['video_classifier']!='selected.npz':raise ValueError('Unsupported video classifier artifact')
        from .research import load_frozen,score_videos
        candidate,candidate_threshold,candidate_metadata=load_frozen(model_dir,expected_encoder=encoder.config)
        if candidate_metadata.get('encoder')!=encoder.config:raise ValueError('Video classifier encoder mismatch')
        positive=float(score_videos(candidate,[windows])[0])>=candidate_threshold
        classifier_status='experimental-v2'
    else:positive=video_score(model,windows)>=threshold
    versions=capture_versions(model_dir,encoder.path,config,video_threshold=candidate_threshold if config.get('video_classifier') else None)
    events=merge_events(centers,scores,event_threshold,info.duration_sec) if positive else []
    if positive and not events:
        peak=int(np.argmax(scores))
        events=merge_events(centers,scores,float(scores[peak]),info.duration_sec)
    refinement_status='not_needed'
    if events and should_refine(sequence.wall_sec,time.perf_counter()-start,info.duration_sec,events,budget_sec):
        refinement_status='performed'
        stage_callback('refining_intervals')
        sequence=refine_features(sequence,iter_samples(path,8.,encoder.config['input_size'],timestamps=sequence.source_times),encoder,events)
        centers,windows=window_features(sequence.times,sequence.values,info.duration_sec)
        scores=model_scores(model,windows)
        refined_events=merge_events(centers,scores,event_threshold,info.duration_sec)
        if refined_events:events=refined_events
    elif events:refinement_status='skipped_budget'
    versions['parameters']['refinement_fps']=8. if refinement_status=='performed' else 0.
    stage_callback('evidence')
    violations=[]
    for event in events:
        from .taxonomy import classify_interval
        typed=classify_interval(model_dir/'types.npz',type_sequence.times,type_sequence.values,event['start_sec'],event['end_sec'],encoder_config=encoder.config)
        if typed:
            event['type']=typed['type'];event['type_prediction']=typed
            event['description']='Модель предлагает тип наблюдаемого действия: '+typed['type']+'. Конкретный факт и применимость правила требуют подтверждения проверяющим.'
        point=min(event['timecode_sec'],max(0,info.duration_sec-.05))
        evidence=[]
        for timestamp in [max(0,point-1),point,min(info.duration_sec-.05,point+1)]:
            frame=evidence_frame(path,timestamp,times=sequence.source_times)
            if not any(f['timestamp_sec']==frame['timestamp_sec'] for f in evidence):evidence.append(frame)
        event['timecode_sec']=min(evidence,key=lambda f:abs(f['timestamp_sec']-point))['timestamp_sec']
        violations.append({k:event[k] for k in ('type','timecode_sec','description')}|{'evidence_frames':evidence})
    stage_callback('preparing_video')
    browser_copy(path,work_dir/'preview.mp4')
    elapsed=time.perf_counter()-start
    review={'events':events,'duration_sec':info.duration_sec,'variant':config['selected_variant'],'taxonomy_confirmed':False,
            'video_classifier_status':classifier_status,
            'refinement_status':refinement_status,
            'metadata':{'size_bytes':path.stat().st_size,'duration_sec':info.duration_sec,'width':info.width,'height':info.height,
                        'codec':info.codec,'average_fps':info.average_fps,'nominal_fps':info.nominal_fps,'frame_rate_mode':info.frame_rate_mode},
            'version_snapshot':versions,
            'coverage':[{'start_sec':0,'end_sec':info.duration_sec,'status':'sampled','sample_fps':encoder.config['sample_fps']}],
            'limitations':['Типы событий не подтверждены; нормативное сопоставление недоступно.',
                           'Вывод модели требует проверки человеком. Отсутствие сигнала не гарантирует отсутствие нарушения.',
                           'Анализ выполнен по временным выборкам; действия короче шага выборки могут быть пропущены.',
                           'Видимость объектов и устойчивость к другому ракурсу не оценены.'],
            'measurements':{'analysis_wall_sec':round(elapsed,3),'features_wall_sec':round(sequence.wall_sec,3),
                            'rss_mb':round(psutil.Process().memory_info().rss/1e6,1),'samples':len(sequence.times),'outgoing_network_blocked':True}}
    result={'verdict':'violation' if violations else 'no_violation','violations':violations}
    if refinement_status=='skipped_budget':review['limitations'].append('Дополнительное уточнение 8 кадр/с пропущено по бюджету времени; основная выборка всего видео обработана полностью.')
    return result,review
