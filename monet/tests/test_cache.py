"""
monet/tests/test_cache.py
~~~~~~~~~~~~~~~~~~~~~~~~~

Tests for LocalCache (local SQLite mirror + outbox).
"""

import os
import tempfile
import unittest

from monet import cache as mcache
from monet.cache import LocalCache


def _make_cache(tmpdir):
    """Return a fresh LocalCache backed by a temp directory."""
    return LocalCache(os.path.join(tmpdir, 'test_cache.db'))


class TestLocalCacheCalibrations(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = _make_cache(self.tmpdir)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_record(self, date='2024-01-01', time_='10:00', bkg=1.0):
        return {
            'device_name': 'TestScope',
            'wavelength_nm': 488.0,
            'laser_power_mw': 100.0,
            'calibration_date': date,
            'calibration_time': time_,
            'parameters': {'bkg': bkg, 'amp': 40.0},
        }

    # ── upsert / query ──

    def test_upsert_and_query_latest(self):
        self.cache.upsert_calibration(self._make_record(bkg=1.0))
        self.cache.upsert_calibration(
            self._make_record(date='2024-01-02', bkg=2.0)
        )
        records = self.cache.query_calibrations(
            {
                'name': 'TestScope',
                'wavelength [nm]': 488.0,
                'laser_power [mW]': 100.0,
            },
            time_idx='latest',
        )
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]['parameters']['bkg'], 2.0)

    def test_upsert_updates_existing(self):
        self.cache.upsert_calibration(self._make_record(bkg=1.0))
        # Same key → should update parameters
        self.cache.upsert_calibration(self._make_record(bkg=99.0))
        records = self.cache.query_calibrations(
            {'name': 'TestScope'}, time_idx='all'
        )
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]['parameters']['bkg'], 99.0)

    def test_query_all(self):
        self.cache.upsert_calibration(
            self._make_record(date='2024-01-01', bkg=1.0)
        )
        self.cache.upsert_calibration(
            self._make_record(date='2024-01-02', bkg=2.0)
        )
        records = self.cache.query_calibrations(
            {'name': 'TestScope'}, time_idx='all'
        )
        self.assertEqual(len(records), 2)

    def test_query_last_date(self):
        self.cache.upsert_calibration(
            self._make_record(date='2024-01-01', bkg=1.0)
        )
        self.cache.upsert_calibration(
            self._make_record(date='2024-01-02', bkg=2.0)
        )
        # Add a second entry on the last date
        r2 = self._make_record(date='2024-01-02', bkg=3.0)
        r2['laser_power_mw'] = 200.0
        self.cache.upsert_calibration(r2)
        records = self.cache.query_calibrations(
            {'name': 'TestScope'}, time_idx='last date'
        )
        self.assertEqual(len(records), 2)
        for r in records:
            self.assertEqual(r['calibration_date'], '2024-01-02')

    def test_query_last_combinations(self):
        self.cache.upsert_calibration(
            self._make_record(date='2024-01-01', time_='09:00', bkg=1.0)
        )
        self.cache.upsert_calibration(
            self._make_record(date='2024-01-02', time_='10:00', bkg=2.0)
        )
        r2 = self._make_record(date='2024-01-02', time_='10:00', bkg=5.0)
        r2['laser_power_mw'] = 200.0
        self.cache.upsert_calibration(r2)
        records = self.cache.query_calibrations(
            {'name': 'TestScope'}, time_idx='last combinations'
        )
        # Two unique (device, wavelength, power) combos → two records
        self.assertEqual(len(records), 2)
        # The 100 mW entry should be the latest (2024-01-02)
        rec_100 = next(r for r in records if r['laser_power_mw'] == 100.0)
        self.assertAlmostEqual(rec_100['parameters']['bkg'], 2.0)

    def test_query_by_date_list(self):
        self.cache.upsert_calibration(
            self._make_record(date='2024-01-01', bkg=1.0)
        )
        self.cache.upsert_calibration(
            self._make_record(date='2024-01-02', bkg=2.0)
        )
        records = self.cache.query_calibrations(
            {'name': 'TestScope'}, time_idx=['2024-01-01']
        )
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]['parameters']['bkg'], 1.0)

    def test_wildcard_query(self):
        self.cache.upsert_calibration(self._make_record())
        r2 = self._make_record()
        r2['wavelength_nm'] = 561.0
        self.cache.upsert_calibration(r2)
        records = self.cache.query_calibrations(
            {'name': 'TestScope'}, time_idx='all'
        )
        self.assertEqual(len(records), 2)

    def test_delete_calibrations(self):
        self.cache.upsert_calibration(self._make_record())
        count = self.cache.delete_calibrations({'name': 'TestScope'})
        self.assertEqual(count, 1)
        records = self.cache.query_calibrations(
            {'name': 'TestScope'}, time_idx='all'
        )
        self.assertEqual(len(records), 0)

    def test_empty_query_returns_empty_list(self):
        records = self.cache.query_calibrations(
            {'name': 'NoSuchScope'}, time_idx='all'
        )
        self.assertEqual(records, [])


class TestLocalCacheFactors(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = _make_cache(self.tmpdir)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_factor(self, date='2024-01-01', mean=0.9):
        return {
            'device_name': 'TestScope',
            'wavelength_nm': 488.0,
            'calibration_date': date,
            'transmission_objective_mean': mean,
            'transmission_objective_std': 0.01,
            'n_points': 50,
        }

    def test_upsert_and_query(self):
        self.cache.upsert_factor(self._make_factor())
        records = self.cache.query_factors('TestScope', 488)
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]['transmission_objective_mean'], 0.9)

    def test_upsert_updates_existing(self):
        self.cache.upsert_factor(self._make_factor(mean=0.9))
        self.cache.upsert_factor(self._make_factor(mean=0.95))
        records = self.cache.query_factors('TestScope', 488)
        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]['transmission_objective_mean'], 0.95)

    def test_wildcard_device(self):
        self.cache.upsert_factor(self._make_factor())
        records = self.cache.query_factors(None, None)
        self.assertGreaterEqual(len(records), 1)


class TestLocalCacheOutbox(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = _make_cache(self.tmpdir)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_and_get_pending(self):
        eid = self.cache.add_to_outbox(
            '/calibrations',
            {'index': {'name': 'S'}, 'parameters': {'bkg': 1.0}},
            local_key={'name': 'S', 'date': '2024-01-01', 'time': '10:00'},
        )
        pending = self.cache.get_pending_outbox()
        self.assertEqual(len(pending), 1)
        entry_id, endpoint, payload, local_key = pending[0]
        self.assertEqual(entry_id, eid)
        self.assertEqual(endpoint, '/calibrations')
        self.assertIn('parameters', payload)
        self.assertEqual(local_key['name'], 'S')

    def test_remove_entry(self):
        eid = self.cache.add_to_outbox(
            '/calibrations', {'index': {}, 'parameters': {}}
        )
        self.cache.remove_outbox_entry(eid)
        self.assertEqual(self.cache.pending_outbox_count(), 0)

    def test_record_failure(self):
        eid = self.cache.add_to_outbox(
            '/calibrations', {'index': {}, 'parameters': {}}
        )
        self.cache.record_outbox_failure(eid, 'Connection refused')
        pending = self.cache.get_pending_outbox()
        # Entry is still there after failure
        self.assertEqual(len(pending), 1)

    def test_pending_count(self):
        self.assertEqual(self.cache.pending_outbox_count(), 0)
        self.cache.add_to_outbox(
            '/calibrations', {'index': {}, 'parameters': {}}
        )
        self.cache.add_to_outbox('/factors', {'device_name': 'S'})
        self.assertEqual(self.cache.pending_outbox_count(), 2)

    def test_no_local_key(self):
        self.cache.add_to_outbox('/calibrations/delete', {'device_name': 'S'})
        pending = self.cache.get_pending_outbox()
        _, _, _, local_key = pending[0]
        self.assertIsNone(local_key)


class TestGetCacheRegistry(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        mcache._set_cache_dir(self.tmpdir)
        mcache._clear_cache_registry()

    def tearDown(self):
        mcache._clear_cache_registry()
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_same_url_returns_same_instance(self):
        c1 = mcache._get_cache('http://localhost:8000')
        c2 = mcache._get_cache('http://localhost:8000')
        self.assertIs(c1, c2)

    def test_different_urls_return_different_instances(self):
        c1 = mcache._get_cache('http://server1:8000')
        c2 = mcache._get_cache('http://server2:8000')
        self.assertIsNot(c1, c2)

    def test_cache_file_created_in_cache_dir(self):
        mcache._get_cache('http://localhost:9999')
        files = list(mcache._DEFAULT_CACHE_DIR.iterdir())
        self.assertTrue(any(f.suffix == '.db' for f in files))
