import csv
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


class DatasetReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dataset = self.root / 'local/dataset'
        self.dataset.mkdir(parents=True)
        self.groups = []
        self.ids = []
        for i, (part, label) in enumerate([('train', 1), ('validation', 0), ('test', 1)], 1):
            path = self.root / f'X-{i}.mov'
            path.write_bytes(b'fixture-movie'+bytes([i]))
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            self.ids.append(digest)
            self.groups.append(dict(group_id=digest, sha256=digest,
                                    file=path.name, path=str(path), files=[path.name],
                                    label=label, duration_sec=10, points_sec=[5] if label else []))
        (self.dataset / 'manifest.json').write_text(json.dumps(self.groups))
        (self.dataset / 'split.json').write_text(json.dumps(dict(train=[self.ids[0]], validation=[self.ids[1]], test=[self.ids[2]])))

    def tearDown(self):
        self.tmp.cleanup()

    def store(self):
        from cashframe.dataset_review import DatasetReviewStore
        return DatasetReviewStore(self.root)

    def payload(self, revision):
        return dict(revision=revision, reviewer='Reviewer', coverage_complete=False, events=[
            dict(start_sec=4, end_sec=6, type_id='visible_action', observed_action='Synthetic visible action', visibility='clear',target_confirmed=True)])

    def test_visible_observation_without_target_confirmation_is_not_training_event(self):
        store=self.store()
        payload=self.payload(store.snapshot()['revision'])
        payload['events'][0]['target_confirmed']=False
        saved=store.save(self.ids[0],payload)
        self.assertEqual(saved['confirmed_intervals'],0)
        observation=next(r for r in saved['sources'][0]['events'] if r['start_sec'] is not None)
        self.assertEqual(observation['annotation_status'],'draft')
        from cashframe.label_table import confirmed_annotations
        from cashframe.data import read_json
        self.assertEqual(confirmed_annotations(saved['sources'][0]['events'],self.groups,read_json(self.dataset/'split.json')),[])

    def test_lists_only_train_and_validation_without_absolute_paths(self):
        result = self.store().snapshot()
        self.assertEqual([g['partition'] for g in result['sources']], ['train', 'validation'])
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(result['confirmed_intervals'], 0)

    def test_saves_human_interval_without_inventing_coverage(self):
        store = self.store()
        result = store.save(self.ids[0], self.payload(store.snapshot()['revision']))
        self.assertEqual(result['confirmed_intervals'], 1)
        self.assertFalse(result['sources'][0]['coverage_complete'])
        from cashframe.label_table import read_csv, confirmed_annotations
        rows = read_csv(self.dataset/'events-review.csv', self.groups, json.loads((self.dataset/'split.json').read_text()))
        labels = confirmed_annotations(rows, self.groups, json.loads((self.dataset/'split.json').read_text()))
        self.assertTrue(labels[0]['confirmed'])
        self.assertEqual(labels[0]['origin'], 'human')
        self.assertEqual(labels[0]['source_partition'], 'train')

    def test_normal_coverage_needs_human_and_no_fabricated_event(self):
        store = self.store()
        payload = dict(revision=store.snapshot()['revision'], reviewer='Reviewer', coverage_complete=True, events=[])
        result = store.save(self.ids[1], payload)
        self.assertTrue(result['sources'][1]['coverage_complete'])
        self.assertEqual(result['sources'][1]['events'], [])
        with (self.dataset/'review-coverage.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(next(r for r in rows if r['source_sha256']==self.ids[1])['coverage_complete'], 'yes')

    def test_rejects_withheld_source_and_stale_revision(self):
        store = self.store()
        rev = store.snapshot()['revision']
        with self.assertRaises(KeyError):
            store.save(self.ids[2], self.payload(rev))
        store.save(self.ids[0], self.payload(rev))
        from cashframe.dataset_review import ReviewConflict
        with self.assertRaises(ReviewConflict):
            store.save(self.ids[0], self.payload(rev))
        with self.assertRaises(KeyError):
            store.video(self.ids[2])

    def test_rejects_false_confirmation_bounds_and_negative_events(self):
        store = self.store()
        for field, value in [('reviewer', ''), ('coverage_complete', 'yes')]:
            payload = self.payload(store.snapshot()['revision'])
            payload[field] = value
            with self.assertRaises(ValueError):
                store.save(self.ids[0], payload)
        payload = self.payload(store.snapshot()['revision'])
        payload['events'][0]['end_sec'] = 20
        with self.assertRaises(ValueError):
            store.save(self.ids[0], payload)
        with self.assertRaises(ValueError):
            store.save(self.ids[1], self.payload(store.snapshot()['revision']))

    def test_rejects_coverage_for_unannotated_positive(self):
        store = self.store()
        with self.assertRaises(ValueError):
            store.save(self.ids[0], dict(revision=store.snapshot()['revision'], reviewer='Reviewer', coverage_complete=True, events=[]))

    def test_original_points_and_stable_review_ids_survive_updates(self):
        store = self.store()
        saved = store.save(self.ids[0], self.payload(store.snapshot()['revision']))
        original = [r for r in saved['sources'][0]['events'] if r['annotation_status']=='unreviewed']
        self.assertEqual([r['point_sec'] for r in original], [5])
        reviewed = next(r for r in saved['sources'][0]['events'] if r['annotation_status']=='confirmed')
        payload = self.payload(saved['revision'])
        payload['events'][0]['event_id'] = reviewed['event_id']
        payload['events'][0]['observed_action'] = 'Updated visible action'
        updated = store.save(self.ids[0], payload)
        reviewed_again = next(r for r in updated['sources'][0]['events'] if r['annotation_status']=='confirmed')
        self.assertEqual(reviewed_again['event_id'], reviewed['event_id'])
        self.assertEqual(len(updated['sources'][0]['events']), 2)
        removed = store.save(self.ids[0], dict(revision=updated['revision'], reviewer='Reviewer', coverage_complete=False, events=[]))
        self.assertTrue(any(r['event_id']==reviewed['event_id'] and r['annotation_status']=='rejected' for r in removed['sources'][0]['events']))

    def test_api_repeated_events_keep_both_ids_and_original_points_after_reload_and_single_edit(self):
        from fastapi.testclient import TestClient
        from cashframe.api import create_app
        from cashframe.config import Settings
        self.groups[0].update(duration_sec=80, points_sec=[57, 68])
        (self.dataset/'manifest.json').write_text(json.dumps(self.groups))
        settings=Settings(root=self.root,api_key='test-key',dataset_review_enabled=True)
        headers={'X-API-Key':'test-key'}
        endpoint='/api/v1/dataset-review/'+self.ids[0]
        def source(snapshot):
            return next(item for item in snapshot['sources'] if item['source_sha256']==self.ids[0])
        def active(snapshot):
            return sorted((row for row in source(snapshot)['events'] if row['annotation_status']=='draft'),
                          key=lambda row:row['start_sec'])
        with TestClient(create_app(settings,analyzer=lambda *a:None)) as client:
            initial=client.get('/api/v1/dataset-review',headers=headers).json()
            original_points=source(initial)['events']
            events=[dict(start_sec=start,end_sec=end,type_id='visible_action',
                         observed_action='Repeated synthetic action',visibility='clear',target_confirmed=False)
                    for start,end in [(57,58),(66,67)]]
            response=client.put(endpoint,headers=headers,json=dict(revision=initial['revision'],
                                reviewer='Synthetic reviewer',coverage_complete=True,events=events))
            self.assertEqual(response.status_code,200,response.text)
            saved=active(response.json())
            self.assertEqual([(row['start_sec'],row['end_sec']) for row in saved],[(57,58),(66,67)])
            saved_ids=[row['event_id'] for row in saved]
            self.assertEqual(len(set(saved_ids)),2)
            self.assertEqual([row['point_sec'] for row in saved],[57,None])
        # A new app/store reads the actual persisted files rather than reusing a response.
        with TestClient(create_app(settings,analyzer=lambda *a:None)) as client:
            reloaded=client.get('/api/v1/dataset-review',headers=headers).json()
            self.assertEqual([row['event_id'] for row in active(reloaded)],saved_ids)
            self.assertEqual([row for row in source(reloaded)['events'] if row['annotation_status']=='unreviewed'],
                             original_points)
            edited=[{field:row[field] for field in ('event_id','start_sec','end_sec','type_id',
                                                   'observed_action','visibility')} | {'target_confirmed':False}
                    for row in active(reloaded)]
            edited[0].update(end_sec=58.5,observed_action='Edited repeated synthetic action')
            response=client.put(endpoint,headers=headers,json=dict(revision=reloaded['revision'],
                                reviewer='Synthetic reviewer',coverage_complete=True,events=edited))
            self.assertEqual(response.status_code,200,response.text)
            final=client.get('/api/v1/dataset-review',headers=headers).json()
            final_events=active(final)
            self.assertEqual([row['event_id'] for row in final_events],saved_ids)
            self.assertEqual([(row['start_sec'],row['end_sec']) for row in final_events],[(57,58.5),(66,67)])
            self.assertEqual(final_events[0]['observed_action'],'Edited repeated synthetic action')
            self.assertEqual(final_events[1]['observed_action'],'Repeated synthetic action')
            self.assertEqual([row['point_sec'] for row in final_events],[57,None])
            self.assertEqual([row for row in source(final)['events'] if row['annotation_status']=='unreviewed'],
                             original_points)
            self.assertEqual(source(final)['points_sec'],[57,68])
            self.assertEqual(final['confirmed_intervals'],0)
            self.assertTrue(source(final)['coverage_complete'])

    def test_organizer_point_id_cannot_be_reused_as_a_human_event_id(self):
        store=self.store()
        snapshot=store.snapshot()
        original=snapshot['sources'][0]['events'][0]
        payload=self.payload(snapshot['revision'])
        payload['events'][0]['event_id']=original['event_id']
        with self.assertRaises(ValueError):
            store.save(self.ids[0],payload)
        self.assertEqual(store.snapshot()['sources'][0]['events'],[original])

    def test_csv_confirmed_original_row_imports_without_losing_point_or_human_fact(self):
        from cashframe.label_table import export_rows,write_csv
        split=json.loads((self.dataset/'split.json').read_text())
        rows=export_rows(self.groups,split)
        original_id=rows[0]['event_id']
        rows[0].update(annotation_status='confirmed',start_sec=4,end_sec=6,type_id='visible_action',
                       observed_action='CSV human observation',visibility='clear',reviewer='CSV reviewer')
        write_csv(self.dataset/'events-review.csv',rows,self.groups,split)
        store=self.store()
        payload=self.payload(store.snapshot()['revision'])
        payload['events'][0].update(event_id=original_id,observed_action='Updated CSV human observation')
        saved=store.save(self.ids[0],payload)
        points=[row for row in saved['sources'][0]['events'] if row['event_id']==original_id]
        self.assertEqual(points[0]['point_sec'],5)
        self.assertEqual(points[0]['annotation_status'],'unreviewed')
        human=next(row for row in saved['sources'][0]['events'] if row['annotation_status']=='confirmed')
        self.assertNotEqual(human['event_id'],original_id)
        self.assertEqual(human['observed_action'],'Updated CSV human observation')

    def test_csv_draft_can_be_saved_and_confirmed_via_api_with_original_point_preserved(self):
        from fastapi.testclient import TestClient
        from cashframe.api import create_app
        from cashframe.config import Settings
        from cashframe.label_table import export_rows,write_csv
        split=json.loads((self.dataset/'split.json').read_text())
        rows=export_rows(self.groups,split)
        original=dict(rows[0])
        rows[0].update(annotation_status='draft',start_sec=4,end_sec=6,type_id='visible_action',
                       observed_action='CSV draft human observation',visibility='clear',reviewer='CSV reviewer')
        write_csv(self.dataset/'events-review.csv',rows,self.groups,split)
        headers={'X-API-Key':'test-key'}
        settings=Settings(root=self.root,api_key='test-key',dataset_review_enabled=True)
        with TestClient(create_app(settings,analyzer=lambda *a:None)) as client:
            snapshot=client.get('/api/v1/dataset-review',headers=headers).json()
            payload=self.payload(snapshot['revision'])
            payload['events'][0].update(event_id=original['event_id'],
                                        observed_action='CSV draft human observation',target_confirmed=False)
            response=client.put('/api/v1/dataset-review/'+self.ids[0],headers=headers,json=payload)
            self.assertEqual(response.status_code,200,response.text)
            saved=response.json()
            events=saved['sources'][0]['events']
            self.assertEqual(next(row for row in events if row['event_id']==original['event_id']),original)
            draft=next(row for row in events if row['annotation_status']=='draft')
            self.assertNotEqual(draft['event_id'],original['event_id'])
            self.assertEqual((draft['start_sec'],draft['end_sec'],draft['type_id'],draft['observed_action']),
                             (4,6,'visible_action','CSV draft human observation'))
            payload['revision']=saved['revision']
            payload['events'][0].update(event_id=draft['event_id'],target_confirmed=True)
            response=client.put('/api/v1/dataset-review/'+self.ids[0],headers=headers,json=payload)
            self.assertEqual(response.status_code,200,response.text)
            events=response.json()['sources'][0]['events']
            self.assertEqual(next(row for row in events if row['event_id']==original['event_id']),original)
            confirmed=next(row for row in events if row['annotation_status']=='confirmed')
            self.assertEqual(confirmed['event_id'],draft['event_id'])
            self.assertEqual(confirmed['observed_action'],'CSV draft human observation')

    def test_opt_in_api_video_auth_and_save_roundtrip(self):
        from fastapi.testclient import TestClient
        from cashframe.api import create_app
        from cashframe.config import Settings
        settings = Settings(root=self.root, api_key='test-key', dataset_review_enabled=True)
        headers = {'X-API-Key':'test-key'}
        with TestClient(create_app(settings, analyzer=lambda *a: None)) as client:
            snapshot = client.get('/api/v1/dataset-review', headers=headers).json()
            self.assertEqual(client.get('/api/v1/dataset-review/'+self.ids[0]+'/video').status_code, 401)
            self.assertEqual(client.get('/api/v1/dataset-review/'+self.ids[2]+'/video', headers=headers).status_code, 404)
            self.assertEqual(client.get('/api/v1/dataset-review/'+self.ids[0]+'/video', headers=headers).content, b'fixture-movie'+bytes([1]))
            response = client.put('/api/v1/dataset-review/'+self.ids[0], headers=headers, json=self.payload(snapshot['revision']))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['confirmed_intervals'], 1)
            self.assertEqual(client.put('/api/v1/dataset-review/'+self.ids[0], headers=headers, json=self.payload(snapshot['revision'])).status_code, 409)
            self.assertEqual(client.put('/api/v1/dataset-review/'+self.ids[0],headers=headers,content=b'x'*(600*1024)).status_code,413)

    def test_feature_is_disabled_by_default_and_always_authenticated(self):
        from fastapi.testclient import TestClient
        from cashframe.api import create_app
        from cashframe.config import Settings
        settings = Settings(root=self.root, api_key='test-key')
        with TestClient(create_app(settings, analyzer=lambda *a: None)) as client:
            self.assertEqual(client.get('/api/v1/dataset-review').status_code, 401)
            self.assertEqual(client.get('/api/v1/dataset-review', headers={'X-API-Key':'test-key'}).status_code, 404)

    def test_normal_coverage_rejects_formula_like_reviewer(self):
        store = self.store()
        for reviewer in ['=SUM(1,2)', '+SUM(1,2)', '@SUM(1,2)']:
            with self.assertRaises(ValueError):
                store.save(self.ids[1], dict(revision=store.snapshot()['revision'], reviewer=reviewer, coverage_complete=True, events=[]))

    def test_api_points_to_reviewer_field_without_echoing_private_input(self):
        from fastapi.testclient import TestClient
        from cashframe.api import create_app
        from cashframe.config import Settings
        with TestClient(create_app(Settings(root=self.root,api_key='test-key',dataset_review_enabled=True),analyzer=lambda *a:None)) as client:
            headers={'X-API-Key':'test-key'}
            payload=self.payload(client.get('/api/v1/dataset-review',headers=headers).json()['revision'])
            payload['reviewer']='-'
            response=client.put('/api/v1/dataset-review/'+self.ids[0],headers=headers,json=payload)
            self.assertEqual(response.status_code,422)
            detail=response.json()['detail']
            self.assertIsInstance(detail,dict)
            self.assertEqual(detail['field'],'reviewer')
            self.assertIn('Проверяющий 1',detail['message'])
            self.assertNotIn('Synthetic visible action',response.text)

    def test_replaced_video_is_rejected_by_source_digest(self):
        store = self.store()
        self.assertEqual(store.video(self.ids[0]), self.root/'X-1.mov')
        (self.root/'X-1.mov').write_bytes(b'replaced video')
        with self.assertRaises(ValueError):
            store.video(self.ids[0])

    def test_second_file_write_failure_restores_both_exact_files(self):
        from unittest.mock import patch
        import os
        store=self.store()
        store.save(self.ids[0], self.payload(store.snapshot()['revision']))
        before={path:path.read_bytes() for path in (store.table,store.coverage)}
        replace=os.replace
        def fail_second(source,target):
            if Path(target)==store.coverage:raise PermissionError('synthetic coverage lock')
            return replace(source,target)
        with patch('cashframe.dataset_review.os.replace',side_effect=fail_second):
            with self.assertRaises(PermissionError):
                store.save(self.ids[1],dict(revision=store.snapshot()['revision'],reviewer='Reviewer',coverage_complete=True,events=[]))
        self.assertEqual({path:path.read_bytes() for path in before},before)

    def test_two_stores_cannot_both_accept_the_same_revision(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from cashframe.dataset_review import ReviewConflict
        first,second=self.store(),self.store()
        revision=first.snapshot()['revision']
        reading=threading.Event(); release=threading.Event(); second_started=threading.Event()
        original=first._read
        def pause_read():
            result=original()
            if not reading.is_set():reading.set();release.wait(3)
            return result
        first._read=pause_read
        def save_other():
            second_started.set()
            try:
                return second.save(self.ids[1],dict(revision=revision,reviewer='Reviewer',coverage_complete=True,events=[]))
            except ReviewConflict:return 'conflict'
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(first.save,self.ids[0],self.payload(revision))
            self.assertTrue(reading.wait(3))
            b=pool.submit(save_other)
            self.assertTrue(second_started.wait(3));release.set()
            results=[]
            for future in (a,b):
                try:results.append(future.result(timeout=6))
                except ReviewConflict:results.append('conflict')
        self.assertEqual(results.count('conflict'),1)

    def test_external_edit_during_validation_is_not_overwritten(self):
        from unittest.mock import patch
        from cashframe import dataset_review
        store=self.store()
        store.save(self.ids[0],self.payload(store.snapshot()['revision']))
        revision=store.snapshot()['revision']
        previous=store.table.read_bytes()
        external=previous.replace(b'Synthetic visible action',b'External reviewer edit')
        render=dataset_review._csv_bytes
        def edit_before_commit(columns,rows):
            result=render(columns,rows)
            store.table.write_bytes(external)
            return result
        with patch('cashframe.dataset_review._csv_bytes',side_effect=edit_before_commit):
            with self.assertRaises(dataset_review.ReviewConflict):
                store.save(self.ids[1],dict(revision=revision,reviewer='Reviewer',coverage_complete=True,events=[]))
        self.assertEqual(store.table.read_bytes(),external)


if __name__ == '__main__':
    unittest.main()
