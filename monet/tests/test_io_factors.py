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

import numpy as np
import pandas as pd

import monet.analysis as man
import monet.io as mio
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
        self.db = os.path.join(self.tmpdir, "db.xlsx")
        # _save_factor_excel opens the file in append mode, so it must already
        # exist with at least one sheet.
        _write_calib_db(
            self.db,
            [
                (
                    "TestScope",
                    488.0,
                    100.0,
                    "2024-01-01",
                    "10:00",
                    {"amp": 40.0},
                ),
            ],
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load_factor(self):
        mio._save_factor_excel(
            self.db, "TestScope", 488, "2024-06-01", 0.82, 0.04, 30
        )
        df = mio.load_factors(self.db, device="TestScope", laser=488)
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertAlmostEqual(row["transmission_objective_mean"], 0.82)
        self.assertAlmostEqual(row["transmission_objective_std"], 0.04)
        self.assertEqual(int(row["n_points"]), 30)

    def test_save_factor_updates_existing(self):
        mio._save_factor_excel(
            self.db, "TestScope", 488, "2024-06-01", 0.80, 0.04, 30
        )
        mio._save_factor_excel(
            self.db, "TestScope", 488, "2024-06-01", 0.90, 0.02, 50
        )
        df = mio.load_factors(self.db, device="TestScope", laser=488)
        # Same (device, wavelength, date) -> one row, updated value.
        self.assertEqual(len(df), 1)
        self.assertAlmostEqual(df.iloc[0]["transmission_objective_mean"], 0.90)

    def test_load_factors_filters(self):
        mio._save_factor_excel(
            self.db, "ScopeA", 488, "2024-06-01", 0.8, 0.0, 10
        )
        mio._save_factor_excel(
            self.db, "ScopeB", 561, "2024-06-01", 0.7, 0.0, 10
        )
        self.assertEqual(len(mio.load_factors(self.db, device="ScopeA")), 1)
        self.assertEqual(len(mio.load_factors(self.db, laser=561)), 1)
        self.assertEqual(len(mio.load_factors(self.db)), 2)

    def test_load_factors_missing_file(self):
        df = mio.load_factors(os.path.join(self.tmpdir, "nope.xlsx"))
        self.assertTrue(df.empty)

    def test_load_factors_no_factor_sheet(self):
        # db exists but has no 'factors' sheet yet -> empty.
        df = mio.load_factors(self.db, device="TestScope")
        self.assertTrue(df.empty)


class TestPlotDeviceHistory(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "db.xlsx")
        self.plot_dir = os.path.join(self.tmpdir, "plots")
        os.mkdir(self.plot_dir)
        _write_calib_db(
            self.db,
            [
                (
                    "TestScope",
                    488.0,
                    100.0,
                    "2024-01-01",
                    "10:00",
                    {"bkg": 1.0, "amp": 40.0},
                ),
                (
                    "TestScope",
                    488.0,
                    100.0,
                    "2024-02-01",
                    "10:00",
                    {"bkg": 1.1, "amp": 41.0},
                ),
                (
                    "TestScope",
                    488.0,
                    200.0,
                    "2024-01-01",
                    "10:00",
                    {"bkg": 2.0, "amp": 60.0},
                ),
            ],
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_plot_device_history_creates_png(self):
        mio.plot_device_history(self.db, "TestScope", self.plot_dir)
        pngs = [f for f in os.listdir(self.plot_dir) if f.endswith(".png")]
        self.assertTrue(any(p.startswith("history_") for p in pngs))

    def test_plot_device_history_no_dir_is_noop(self):
        # Empty plot_dir -> skip without error.
        mio.plot_device_history(self.db, "TestScope", "")

    def test_plot_amplitude_history_creates_png(self):
        analyzer = man.LinearCurveAnalyzer({"min": 0.0, "max": 10.0})
        mio.plot_device_amplitude_history(
            self.db, "TestScope", self.plot_dir, analyzer
        )
        pngs = [f for f in os.listdir(self.plot_dir) if f.endswith(".png")]
        self.assertTrue(any(p.startswith("history_amplitude_") for p in pngs))

    def test_plot_amplitude_history_no_dir_is_noop(self):
        analyzer = man.LinearCurveAnalyzer({"min": 0.0, "max": 10.0})
        mio.plot_device_amplitude_history(self.db, "TestScope", "", analyzer)


class TestComputeAndSaveFactor(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "db.xlsx")
        self.ana_config = {
            "classpath": "monet.analysis.LinearCurveAnalyzer",
            "init_kwargs": {"min": 0.0, "max": 180.0},
        }

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_missing_file_is_noop(self):
        # No exception, just an early return.
        mio.compute_and_save_factor(self.db, "TestScope", 488, self.ana_config)
        self.assertFalse(os.path.exists(self.db))

    def test_missing_powermeter_type_column(self):
        _write_calib_db(
            self.db,
            [
                (
                    "TestScope",
                    488.0,
                    100.0,
                    "2024-01-01",
                    "10:00",
                    {"bkg": 0.1, "amp": 40.0},
                ),
            ],
        )
        mio.compute_and_save_factor(self.db, "TestScope", 488, self.ana_config)
        # No 'factors' sheet should have been created.
        self.assertTrue(mio.load_factors(self.db).empty)

    def test_happy_path_writes_factor(self):
        today = datetime.now().strftime("%Y-%m-%d")
        _write_calib_db(
            self.db,
            [
                (
                    "TestScope",
                    488.0,
                    100.0,
                    today,
                    "10:00",
                    {"bkg": 0.1, "amp": 40.0, "powermeter_type": "sample"},
                ),
                (
                    "TestScope",
                    488.0,
                    100.0,
                    today,
                    "10:05",
                    {"bkg": 0.05, "amp": 20.0, "powermeter_type": "bfp"},
                ),
            ],
        )
        mio.compute_and_save_factor(self.db, "TestScope", 488, self.ana_config)
        df = mio.load_factors(self.db, device="TestScope", laser=488)
        self.assertEqual(len(df), 1)
        # sample/bfp amplitude ratio is ~2.0.
        self.assertAlmostEqual(
            df.iloc[0]["transmission_objective_mean"], 2.0, places=1
        )


class TestMadOutlierMask(unittest.TestCase):

    def test_flags_single_outlier(self):
        vals = [1.0, 1.1, 0.9, 1.05, 0.95, 10.0]
        mask = mio.mad_outlier_mask(vals)
        self.assertTrue(mask[-1])
        self.assertFalse(mask[:-1].any())

    def test_too_few_points_no_flags(self):
        self.assertFalse(mio.mad_outlier_mask([1.0, 100.0]).any())

    def test_zero_mad_no_flags(self):
        # Constant data -> MAD 0 -> nothing flagged (never drops everything).
        self.assertFalse(mio.mad_outlier_mask([2.0, 2.0, 2.0, 2.0]).any())

    def test_handles_nan(self):
        mask = mio.mad_outlier_mask([1.0, 1.1, 0.9, 1.0, np.nan, 9.0])
        self.assertFalse(bool(mask[4]))  # NaN is never an outlier
        self.assertTrue(bool(mask[5]))


class TestFlagAmplitudeOutliers(unittest.TestCase):

    def test_healthy_linear_no_flags(self):
        self.assertEqual(
            mio.flag_amplitude_outliers(
                [10, 20, 30, 40, 50], [100, 200, 300, 400, 500]
            ),
            {},
        )

    def test_single_high_outlier_collinear(self):
        # Perfectly collinear but one failed point: OLS+MAD would miss this
        # (the fit gets dragged toward the outlier); the robust fit + fallback
        # catches it.
        out = mio.flag_amplitude_outliers(
            [10, 20, 30, 40, 50], [100, 200, 900, 400, 500]
        )
        self.assertEqual(set(out), {2})

    def test_single_low_outlier(self):
        out = mio.flag_amplitude_outliers(
            [10, 20, 30, 40, 50], [100, 200, 300, 400, 50]
        )
        self.assertEqual(set(out), {4})

    def test_noisy_but_ok_no_flags(self):
        self.assertEqual(
            mio.flag_amplitude_outliers(
                [10, 20, 30, 40, 50], [102, 199, 301, 398, 503]
            ),
            {},
        )

    def test_noisy_with_outlier(self):
        out = mio.flag_amplitude_outliers(
            [10, 20, 30, 40, 50], [102, 199, 900, 398, 503]
        )
        self.assertEqual(set(out), {2})

    def test_too_few_points(self):
        self.assertEqual(mio.flag_amplitude_outliers([10, 20], [100, 200]), {})


class TestLoadAmplitudeHistory(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "db.xlsx")
        self.analyzer = man.LinearCurveAnalyzer({"min": 0.0, "max": 180.0})

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _rows(self, dates, ptype=None):
        rows = []
        for d in dates:
            for lpwr, amp in ((100.0, 40.0), (200.0, 60.0)):
                params = {"bkg": 0.0, "amp": amp}
                if ptype is not None:
                    params["powermeter_type"] = ptype
                rows.append(("TestScope", 488.0, lpwr, d, "10:00", params))
        return rows

    def test_returns_amplitudes_per_run(self):
        _write_calib_db(self.db, self._rows(["2024-01-01"]))
        hist = mio.load_amplitude_history(self.db, "TestScope", self.analyzer)
        self.assertIn("488", hist)
        runs = hist["488"]
        self.assertEqual(len(runs), 1)
        amps = runs[0]["amplitudes"]
        # output_range max = bkg + amp*max = 40*180 and 60*180.
        self.assertAlmostEqual(amps[100.0], 40.0 * 180.0, places=3)
        self.assertAlmostEqual(amps[200.0], 60.0 * 180.0, places=3)

    def test_max_runs_keeps_latest(self):
        _write_calib_db(
            self.db,
            self._rows(
                ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01"]
            ),
        )
        hist = mio.load_amplitude_history(
            self.db, "TestScope", self.analyzer, max_runs=2
        )
        dates = [run["date"] for run in hist["488"]]
        self.assertEqual(dates, ["2024-03-01", "2024-04-01"])

    def test_powermeter_type_filter(self):
        rows = self._rows(["2024-01-01"], ptype="sample")
        rows += self._rows(["2024-02-01"], ptype="bfp")
        _write_calib_db(self.db, rows)
        hist = mio.load_amplitude_history(
            self.db, "TestScope", self.analyzer, powermeter_type="bfp"
        )
        dates = [run["date"] for run in hist["488"]]
        self.assertEqual(dates, ["2024-02-01"])

    def test_missing_file_empty(self):
        hist = mio.load_amplitude_history(
            os.path.join(self.tmpdir, "nope.xlsx"), "TestScope", self.analyzer
        )
        self.assertEqual(hist, {})


class TestComputeFactorBreakdown(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "db.xlsx")
        self.ana_config = {
            "classpath": "monet.analysis.LinearCurveAnalyzer",
            "init_kwargs": {"min": 0.0, "max": 180.0},
        }

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _pair_rows(self, date, lpwr, amp_sample, amp_bfp):
        return [
            (
                "TestScope",
                488.0,
                lpwr,
                date,
                "10:00",
                {"bkg": 0.1, "amp": amp_sample, "powermeter_type": "sample"},
            ),
            (
                "TestScope",
                488.0,
                lpwr,
                date,
                "10:05",
                {"bkg": 0.05, "amp": amp_bfp, "powermeter_type": "bfp"},
            ),
        ]

    def test_one_row_per_input(self):
        rows = self._pair_rows("2024-06-01", 100.0, 40.0, 20.0)
        rows += self._pair_rows("2024-06-01", 200.0, 40.0, 20.0)
        rows += self._pair_rows("2024-07-01", 100.0, 40.0, 20.0)
        _write_calib_db(self.db, rows)
        df = mio.compute_factor_breakdown(
            self.db, "TestScope", self.ana_config
        )
        self.assertEqual(len(df), 3)
        self.assertTrue((df["factor"] > 1.9).all())
        self.assertTrue((df["factor"] < 2.1).all())
        self.assertEqual(
            set(zip(df["date"], df["laser_power"])),
            {
                ("2024-06-01", 100.0),
                ("2024-06-01", 200.0),
                ("2024-07-01", 100.0),
            },
        )

    def test_unpaired_date_skipped(self):
        # Only a sample-plane calibration on this date -> nothing to pair.
        rows = [
            (
                "TestScope",
                488.0,
                100.0,
                "2024-06-01",
                "10:00",
                {"bkg": 0.1, "amp": 40.0, "powermeter_type": "sample"},
            )
        ]
        _write_calib_db(self.db, rows)
        df = mio.compute_factor_breakdown(
            self.db, "TestScope", self.ana_config
        )
        self.assertTrue(df.empty)

    def test_missing_file_empty(self):
        df = mio.compute_factor_breakdown(
            os.path.join(self.tmpdir, "nope.xlsx"),
            "TestScope",
            self.ana_config,
        )
        self.assertTrue(df.empty)


class TestComputeAndSaveFactorOutliers(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "db.xlsx")
        self.ana_config = {
            "classpath": "monet.analysis.LinearCurveAnalyzer",
            "init_kwargs": {"min": 0.0, "max": 180.0},
        }

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _pair(self, lpwr, amp_sample, amp_bfp, today):
        return [
            (
                "TestScope",
                488.0,
                lpwr,
                today,
                "10:00",
                {"bkg": 0.2, "amp": amp_sample, "powermeter_type": "sample"},
            ),
            (
                "TestScope",
                488.0,
                lpwr,
                today,
                "10:05",
                {"bkg": 0.1, "amp": amp_bfp, "powermeter_type": "bfp"},
            ),
        ]

    def test_outlier_power_dropped(self):
        today = datetime.now().strftime("%Y-%m-%d")
        # Two consistent powers (ratio ~2.0, 2.05) and one failed run (~5.0).
        rows = self._pair(100.0, 40.0, 20.0, today)
        rows += self._pair(200.0, 41.0, 20.0, today)
        rows += self._pair(300.0, 100.0, 20.0, today)  # failed calibration
        _write_calib_db(self.db, rows)
        mio.compute_and_save_factor(self.db, "TestScope", 488, self.ana_config)
        df = mio.load_factors(self.db, device="TestScope", laser=488)
        self.assertEqual(len(df), 1)
        # Robust mean should stay near the good cluster (~2.0), not be pulled
        # toward 3.0 by the failed run.
        self.assertLess(df.iloc[0]["transmission_objective_mean"], 2.5)


if __name__ == "__main__":
    unittest.main()
