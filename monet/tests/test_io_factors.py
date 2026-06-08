"""
    monet/tests/test_io_factors.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Tests for the Excel power-meter correction-factor functions and the
    device-history plotting helpers in monet.io.

    :authors: Heinrich Grabmayr, 2024
    :copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""
import os
import tempfile
import unittest
from datetime import datetime

import pandas as pd

import monet.io as mio
import monet.analysis as man
from monet import DATABASE_INDEXLEVELS


def _write_calib_db(path, rows):
    """Write the calibration sheet (sheet 0) of an Excel database."""
    index_tuples = [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
    midx = pd.MultiIndex.from_tuples(index_tuples, names=DATABASE_INDEXLEVELS)
    df = pd.DataFrame([r[5] for r in rows], index=midx)
    df.to_excel(path)


class TestFactorsExcel(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, 'db.xlsx')
        # _save_factor_excel opens the file in append mode, so it must already
        # exist with at least one sheet.
        _write_calib_db(self.db, [
            ('TestScope', 488.0, 100.0, '2024-01-01', '10:00', {'amp': 40.0}),
        ])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load_factor(self):
        mio._save_factor_excel(
            self.db, 'TestScope', 488, '2024-06-01', 0.82, 0.04, 30)
        df = mio.load_factors(self.db, device='TestScope', laser=488)
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertAlmostEqual(row['transmission_objective_mean'], 0.82)
        self.assertAlmostEqual(row['transmission_objective_std'], 0.04)
        self.assertEqual(int(row['n_points']), 30)

    def test_save_factor_updates_existing(self):
        mio._save_factor_excel(
            self.db, 'TestScope', 488, '2024-06-01', 0.80, 0.04, 30)
        mio._save_factor_excel(
            self.db, 'TestScope', 488, '2024-06-01', 0.90, 0.02, 50)
        df = mio.load_factors(self.db, device='TestScope', laser=488)
        # Same (device, wavelength, date) -> one row, updated value.
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(df.iloc[0]['transmission_objective_mean'], 0.90)

    def test_load_factors_filters(self):
        mio._save_factor_excel(self.db, 'ScopeA', 488, '2024-06-01', 0.8, 0.0, 10)
        mio._save_factor_excel(self.db, 'ScopeB', 561, '2024-06-01', 0.7, 0.0, 10)
        self.assertEqual(len(mio.load_factors(self.db, device='ScopeA')), 1)
        self.assertEqual(len(mio.load_factors(self.db, laser=561)), 1)
        self.assertEqual(len(mio.load_factors(self.db)), 2)

    def test_load_factors_missing_file(self):
        df = mio.load_factors(os.path.join(self.tmpdir, 'nope.xlsx'))
        self.assertTrue(df.empty)

    def test_load_factors_no_factor_sheet(self):
        # db exists but has no 'factors' sheet yet -> empty.
        df = mio.load_factors(self.db, device='TestScope')
        self.assertTrue(df.empty)


class TestPlotDeviceHistory(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, 'db.xlsx')
        self.plot_dir = os.path.join(self.tmpdir, 'plots')
        os.mkdir(self.plot_dir)
        _write_calib_db(self.db, [
            ('TestScope', 488.0, 100.0, '2024-01-01', '10:00',
             {'bkg': 1.0, 'amp': 40.0}),
            ('TestScope', 488.0, 100.0, '2024-02-01', '10:00',
             {'bkg': 1.1, 'amp': 41.0}),
            ('TestScope', 488.0, 200.0, '2024-01-01', '10:00',
             {'bkg': 2.0, 'amp': 60.0}),
        ])

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_plot_device_history_creates_png(self):
        mio.plot_device_history(self.db, 'TestScope', self.plot_dir)
        pngs = [f for f in os.listdir(self.plot_dir) if f.endswith('.png')]
        self.assertTrue(any(p.startswith('history_') for p in pngs))

    def test_plot_device_history_no_dir_is_noop(self):
        # Empty plot_dir -> skip without error.
        mio.plot_device_history(self.db, 'TestScope', '')

    def test_plot_amplitude_history_creates_png(self):
        analyzer = man.LinearCurveAnalyzer({'min': 0.0, 'max': 10.0})
        mio.plot_device_amplitude_history(
            self.db, 'TestScope', self.plot_dir, analyzer)
        pngs = [f for f in os.listdir(self.plot_dir) if f.endswith('.png')]
        self.assertTrue(any(p.startswith('history_amplitude_') for p in pngs))

    def test_plot_amplitude_history_no_dir_is_noop(self):
        analyzer = man.LinearCurveAnalyzer({'min': 0.0, 'max': 10.0})
        mio.plot_device_amplitude_history(self.db, 'TestScope', '', analyzer)


class TestComputeAndSaveFactor(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, 'db.xlsx')
        self.ana_config = {
            'classpath': 'monet.analysis.LinearCurveAnalyzer',
            'init_kwargs': {'min': 0.0, 'max': 180.0},
        }

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_file_is_noop(self):
        # No exception, just an early return.
        mio.compute_and_save_factor(
            self.db, 'TestScope', 488, self.ana_config)
        self.assertFalse(os.path.exists(self.db))

    def test_missing_powermeter_type_column(self):
        _write_calib_db(self.db, [
            ('TestScope', 488.0, 100.0, '2024-01-01', '10:00',
             {'bkg': 0.1, 'amp': 40.0}),
        ])
        mio.compute_and_save_factor(
            self.db, 'TestScope', 488, self.ana_config)
        # No 'factors' sheet should have been created.
        self.assertTrue(mio.load_factors(self.db).empty)

    def test_happy_path_writes_factor(self):
        today = datetime.now().strftime('%Y-%m-%d')
        _write_calib_db(self.db, [
            ('TestScope', 488.0, 100.0, today, '10:00',
             {'bkg': 0.1, 'amp': 40.0, 'powermeter_type': 'sample'}),
            ('TestScope', 488.0, 100.0, today, '10:05',
             {'bkg': 0.05, 'amp': 20.0, 'powermeter_type': 'bfp'}),
        ])
        mio.compute_and_save_factor(
            self.db, 'TestScope', 488, self.ana_config)
        df = mio.load_factors(self.db, device='TestScope', laser=488)
        self.assertEqual(len(df), 1)
        # sample/bfp amplitude ratio is ~2.0.
        self.assertAlmostEqual(
            df.iloc[0]['transmission_objective_mean'], 2.0, places=1)


if __name__ == '__main__':
    unittest.main()
