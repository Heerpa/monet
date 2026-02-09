"""
    monet/tests/test_server.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~

    Test the FastAPI server endpoints.
"""
import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from monet.models import get_engine


class TestServer(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, 'test.db')
        os.environ['MONET_DB_PATH'] = self.db_path
        # Import after setting env var so lifespan picks it up
        from monet.server import app
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _save_record(self, name='TestScope', wavelength=488,
                     laser_power=100, params=None):
        if params is None:
            params = {'bkg': 0.5, 'amp': 45.0, 'phi': 32.0}
        resp = self.client.post('/calibrations', json={
            'index': {
                'name': name,
                'wavelength [nm]': wavelength,
                'laser_power [mW]': laser_power,
            },
            'parameters': params,
        })
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_health(self):
        resp = self.client.get('/health')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'status': 'ok'})

    def test_save_calibration(self):
        record = self._save_record()
        self.assertEqual(record['device_name'], 'TestScope')
        self.assertEqual(record['wavelength_nm'], 488)
        self.assertEqual(record['laser_power_mw'], 100)
        self.assertEqual(record['parameters']['bkg'], 0.5)
        self.assertIn('calibration_date', record)
        self.assertIn('calibration_time', record)

    def test_query_latest(self):
        self._save_record(params={'bkg': 1.0, 'amp': 40.0, 'phi': 30.0})
        self._save_record(params={'bkg': 2.0, 'amp': 50.0, 'phi': 35.0})

        resp = self.client.post('/calibrations/query', json={
            'index': {'name': 'TestScope', 'wavelength [nm]': 488,
                      'laser_power [mW]': 100},
            'time_idx': 'latest',
        })
        self.assertEqual(resp.status_code, 200)
        records = resp.json()['records']
        self.assertEqual(len(records), 1)
        # Should be the second record saved
        self.assertEqual(records[0]['parameters']['bkg'], 2.0)

    def test_query_all(self):
        self._save_record(params={'bkg': 1.0, 'amp': 40.0, 'phi': 30.0})
        self._save_record(params={'bkg': 2.0, 'amp': 50.0, 'phi': 35.0})

        resp = self.client.post('/calibrations/query', json={
            'index': {'name': 'TestScope'},
            'time_idx': 'all',
        })
        self.assertEqual(resp.status_code, 200)
        records = resp.json()['records']
        self.assertEqual(len(records), 2)

    def test_query_last_combinations(self):
        # Save records for two different laser powers
        self._save_record(laser_power=100,
                          params={'bkg': 1.0, 'amp': 40.0, 'phi': 30.0})
        self._save_record(laser_power=200,
                          params={'bkg': 1.5, 'amp': 42.0, 'phi': 31.0})
        # Save a newer record for laser_power=100
        self._save_record(laser_power=100,
                          params={'bkg': 2.0, 'amp': 50.0, 'phi': 35.0})

        resp = self.client.post('/calibrations/query', json={
            'index': {'name': 'TestScope', 'wavelength [nm]': 488},
            'time_idx': 'last combinations',
        })
        self.assertEqual(resp.status_code, 200)
        records = resp.json()['records']
        # Should have 2 records: one per (device, wavelength, power) combo
        self.assertEqual(len(records), 2)

        # The record for power=100 should be the newer one
        rec_100 = [r for r in records if r['laser_power_mw'] == 100][0]
        self.assertEqual(rec_100['parameters']['bkg'], 2.0)

    def test_query_wildcard_index(self):
        self._save_record(wavelength=488)
        self._save_record(wavelength=561)

        # Query with None wavelength (wildcard)
        resp = self.client.post('/calibrations/query', json={
            'index': {'name': 'TestScope', 'wavelength [nm]': None},
            'time_idx': 'all',
        })
        self.assertEqual(resp.status_code, 200)
        records = resp.json()['records']
        self.assertEqual(len(records), 2)

    def test_query_not_found(self):
        resp = self.client.post('/calibrations/query', json={
            'index': {'name': 'NonexistentScope'},
            'time_idx': 'all',
        })
        self.assertEqual(resp.status_code, 404)

    def test_restart_database(self):
        # Save multiple records for same combo
        self._save_record(laser_power=100,
                          params={'bkg': 1.0, 'amp': 40.0, 'phi': 30.0})
        self._save_record(laser_power=100,
                          params={'bkg': 2.0, 'amp': 50.0, 'phi': 35.0})
        self._save_record(laser_power=200,
                          params={'bkg': 3.0, 'amp': 55.0, 'phi': 36.0})

        resp = self.client.post('/database/restart')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('backup_path', data)
        self.assertEqual(data['remaining_records'], 2)
        self.assertTrue(os.path.exists(data['backup_path']))

    def test_restart_database_duplicate_backup(self):
        self._save_record()
        # First restart should succeed
        resp = self.client.post('/database/restart')
        self.assertEqual(resp.status_code, 200)
        # Second restart same day should fail (backup already exists)
        resp = self.client.post('/database/restart')
        self.assertEqual(resp.status_code, 409)

    def test_query_with_time_list(self):
        self._save_record()
        record = self._save_record()
        date = record['calibration_date']
        time_val = record['calibration_time']

        resp = self.client.post('/calibrations/query', json={
            'index': {'name': 'TestScope'},
            'time_idx': [date, time_val],
        })
        self.assertEqual(resp.status_code, 200)
        records = resp.json()['records']
        self.assertGreaterEqual(len(records), 1)
