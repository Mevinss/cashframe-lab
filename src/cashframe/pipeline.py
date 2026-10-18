from pathlib import Path
import time

import numpy as np

from .data import read_json,write_json
from .events import propose_events
from .features import Encoder,extract_features,FeatureSequence
from .learning import load_model,model_scores,window_features,video_score,inference_window_parameters
from .video import evidence_frames,probe_video,validate_decode,iter_samples
from .provenance import capture_versions

_NETWORK_BLOCKED=False


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
    return FeatureSequence(np.array([r[0] for r in ordered]),np.stack([r[1] for r in ordered]),base.wall_sec,base.source_times,base.observations)


def block_outgoing_network():
    global _NETWORK_BLOCKED
    import socket
    def denied(*args,**kwargs):raise RuntimeError('Outgoing network is disabled during analysis')
    socket.socket.connect=denied
    socket.socket.connect_ex=denied
    socket.create_connection=denied
    socket.getaddrinfo=denied
    _NETWORK_BLOCKED=True


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
    progress_callback=getattr(stage_callback,'progress',None)
    sequence=extract_features(path,encoder,progress_callback=progress_callback)
    type_sequence=sequence
    stage_callback('scoring_windows')
    window_parameters=inference_window_parameters(config)
    centers,windows=window_features(sequence.times,sequence.values,info.duration_sec,**window_parameters)
    scores=model_scores(model,windows)
    event_threshold=config.get('event_threshold',threshold) if config['selected_variant']=='trained' else threshold
    classifier_status='original'
    if config.get('video_classifier'):
        if config['video_classifier']!='selected.npz':raise ValueError('Unsupported video classifier artifact')
        from .research import load_frozen,score_videos,meets_threshold
        candidate,candidate_threshold,candidate_metadata=load_frozen(model_dir,expected_encoder=encoder.config)
        if candidate_metadata.get('encoder')!=encoder.config:raise ValueError('Video classifier encoder mismatch')
        positive=bool(meets_threshold(score_videos(candidate,[windows])[0],candidate_threshold))
        classifier_status='experimental-v2'
    else:positive=video_score(model,windows)>=threshold
    versions=capture_versions(model_dir,encoder.path,config,video_threshold=candidate_threshold if config.get('video_classifier') else None)
    versions['parameters']['event_threshold']=float(event_threshold)
    versions['parameters']['window_aggregation']='input-dtype-preserved'
    if config.get('video_classifier'):versions['parameters']['threshold_roundoff_epsilon_multiplier']=32
    versions['parameters']['localization_guard']={'flat_range':1e-6,'max_coverage_fraction':.8,'max_events':12}
    point_enabled=bool(config.get('point_localizer'))
    if point_enabled:
        if config['point_localizer']!='point-localizer.npz':raise ValueError('Unsupported point localizer artifact')
        from .point_localization import localized_events,load_point_localizer
        events=localized_events(model_dir,sequence.times,sequence.values,info.duration_sec,positive,encoder.config)
        _,point_metadata=load_point_localizer(model_dir,encoder.config)
        versions['parameters']['point_localizer_guard']=point_metadata['guard']
        versions['parameters']['point_localizer_score_kind']='uncalibrated temporal proposal score'
    else:events=propose_events(centers,scores,event_threshold,info.duration_sec,positive)
    refinement_status='not_needed'
    if not point_enabled and events and all(e['localization_status']!='unresolved' for e in events) and should_refine(sequence.wall_sec,time.perf_counter()-start,info.duration_sec,events,budget_sec):
        refinement_status='performed'
        stage_callback('refining_intervals')
        sequence=refine_features(sequence,iter_samples(path,8.,encoder.config['input_size'],timestamps=sequence.source_times),encoder,events)
        centers,windows=window_features(sequence.times,sequence.values,info.duration_sec,**window_parameters)
        scores=model_scores(model,windows)
        events=propose_events(centers,scores,event_threshold,info.duration_sec,positive)
    elif events:refinement_status='point_localizer_4fps' if point_enabled else 'unresolved_localization' if events[0]['localization_status']=='unresolved' else 'skipped_budget'
    versions['parameters']['refinement_fps']=8. if refinement_status=='performed' else 0.
    stage_callback('evidence')
    violations=[]
    requests=[]
    for event in events:
        if event['localization_status']=='unresolved':
            points=[0.,info.duration_sec/2,max(0,info.duration_sec-.05)]
        else:
            points=[max(0,event['start_sec']-1),min(event['timecode_sec'],info.duration_sec-.05),min(info.duration_sec-.05,event['end_sec']+1)]
        requests.extend(points)
    evidence_start=time.perf_counter()
    extracted=evidence_frames(path,requests,times=sequence.source_times) if requests else []
    if requests and progress_callback:progress_callback(len(extracted),len(requests),'frames')
    evidence_sec=time.perf_counter()-evidence_start
    for index,event in enumerate(events):
        from .taxonomy import classify_interval
        typed=classify_interval(model_dir/'types.npz',type_sequence.times,type_sequence.values,event['start_sec'],event['end_sec'],encoder_config=encoder.config) if event['localization_status']!='unresolved' else None
        if typed:
            event['type']=typed['type'];event['type_prediction']=typed
            event['description']='Модель предлагает тип наблюдаемого действия: '+typed['type']+'. Конкретный факт и применимость правила требуют подтверждения проверяющим.'
        point=min(event['timecode_sec'],max(0,info.duration_sec-.05))
        evidence=[]
        for frame in extracted[index*3:index*3+3]:
            if not any(f['timestamp_sec']==frame['timestamp_sec'] for f in evidence):evidence.append(frame)
        event['timecode_sec']=min(evidence,key=lambda f:abs(f['timestamp_sec']-point))['timestamp_sec']
        violations.append({k:event[k] for k in ('type','timecode_sec','description')}|{'evidence_frames':evidence})
    elapsed=time.perf_counter()-start
    review={'events':events,'duration_sec':info.duration_sec,'variant':config['selected_variant'],'taxonomy_confirmed':False,
            'motion_spectrum':{'method':'mean_absolute_luminance_change_per_second','samples':sequence.observations or [],
                               'interpretation':'Изменение изображения включает движение камеры и освещение. Это не оценка нарушения.'},
            'visibility_guard':{'object_visibility':'not_assessed',
                                'flagged_samples':sum(bool(row['technical_flags']) for row in sequence.observations or []),
                                'sample_count':len(sequence.observations or []),
                                'notice':'Техническое качество кадров не подтверждает видимость действия и условий нормы.'},
            'video_classifier_status':classifier_status,
            'localization_profile':'weak-point-v1' if point_enabled else 'window-v1',
            'preview_status':'pending',
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
                            'evidence_wall_sec':round(evidence_sec,3),
                            'rss_mb':round(psutil.Process().memory_info().rss/1e6,1),'samples':len(sequence.times),'outgoing_network_blocked':_NETWORK_BLOCKED}}
    result={'verdict':'violation' if violations else 'no_violation','violations':violations}
    if refinement_status=='skipped_budget':review['limitations'].append('Дополнительное уточнение 8 кадр/с пропущено по бюджету времени; основная выборка всего видео обработана полностью.')
    return result,review
