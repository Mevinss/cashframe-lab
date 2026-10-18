import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from cashframe.api import create_app
from cashframe.config import Settings


class ActionCatalogTests(unittest.TestCase):
    def test_catalog_is_protected_and_does_not_claim_a_trained_classifier(self):
        with TemporaryDirectory() as folder:
            with TestClient(create_app(Settings(root=Path(folder), api_key='catalog-fixture'),
                                       analyzer=lambda *args: None)) as client:
                url = '/api/v1/action-types'
                self.assertEqual(client.get(url).status_code, 401)
                response = client.get(url, headers={'X-API-Key': 'catalog-fixture'})
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data['status'], 'taxonomy_not_trained')
                classes = data['classes']
                self.assertEqual(len({item['id'] for item in classes}), len(classes))
                self.assertTrue({'leave_workstation', 'count_cash', 'unknown'} <= {r['id'] for r in classes})
                for item in classes:
                    self.assertEqual(set(item['labels']), {'ru', 'kk', 'en'})
                    self.assertTrue(item['limits'])
                    self.assertFalse(item['automatic_violation'])

    def test_each_candidate_rule_exists_and_keeps_applicability_conditions(self):
        from cashframe.data import read_json
        from cashframe.config import ROOT
        registry = {r['id'] for r in read_json(ROOT/'config/rules.json')['rules'] if r.get('verified')}
        catalog = read_json(ROOT/'config/action-types.json')
        for item in catalog['classes']:
            self.assertLessEqual(set(item['candidate_rule_ids']), registry)
            if item['candidate_rule_ids']:
                self.assertTrue(item['requirements'])
        unknown = next(r for r in catalog['classes'] if r['id'] == 'unknown')
        self.assertEqual(unknown['candidate_rule_ids'], [])
