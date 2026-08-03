"""
monet/tests/test_io_excel.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tests for the legacy Excel persistence path in monet.io: save/load/delete
calibrations and the time-index selection logic, plus the small pure
helpers (_is_server_url, _records_to_pandas).

:authors: Heinrich Grabmayr, 2024
:copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""

import os
import tempfile
import unittest

import numpy as np
import pandas as pd

import monet.io as mio
from monet import DATABASE_INDEXLEVELS


def _write_db(path, rows):
    """Write an Excel database from a list of (name, wl, power, date, time,
    params) tuples."""
    index_tuples = [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
    midx = pd.MultiIndex.from_tuples(index_tuples, names=DATABASE_INDEXLEVELS)
    df = pd.DataFrame([r[5] for r in rows], index=midx)
    df.to_excel(path)


class TestPureHelpers(unittest.TestCase):

    def test_is_server_url(self):
        self.assertTrue(mio._is_server_url("http://localhost:8000"))
        self.assertTrue(mio._is_server_url("https://example.com"))
        self.assertFalse(mio._is_server_url("/tmp/db.xlsx"))
        self.assertFalse(mio._is_server_url("relative/db.xlsx"))

    def test_records_to_pandas_latest(self):
        records = [{"parameters": {"bkg": 1.0, "amp": 40.0}}]
        out = mio._records_to_pandas(records, "latest")
        self.assertIsInstance(out, pd.Series)
        self.assertEqual(out["amp"], 40.0)

    def test_records_to_pandas_all(self):
        records = [
            {
                "device_name": "S",
                "wavelength_nm": 488,
                "laser_power_mw": 100,
                "calibration_date": "2024-01-01",
                "calibration_time": "10:00",
                "parameters": {"bkg": "1.0", "amp": "40.0"},
            },
            {
                "device_name": "S",
                "wavelength_nm": 561,
                "laser_power_mw": 50,
                "calibration_date": "2024-01-02",
                "calibration_time": "11:00",
                "parameters": {"bkg": "2.0", "amp": "50.0"},
            },
        ]
        out = mio._records_to_pandas(records, "all")
        self.assertIsInstance(out, pd.DataFrame)
        self.assertEqual(len(out), 2)
        # String parameters get coerced to numeric.
        self.assertTrue(np.issubdtype(out["amp"].dtype, np.floating))


class TestSaveLoadExcel(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "power_database.xlsx")

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _index(self, name="TestScope", wl=488, power=100):
        return {"name": name, "wavelength [nm]": wl, "laser_power [mW]": power}

    def test_save_creates_file_and_loads_back(self):
        mio.save_calibration(self.db, self._index(), {"bkg": 0.5, "amp": 45.0})
        self.assertTrue(os.path.exists(self.db))

        pars = mio.load_calibration(self.db, self._index(), time_idx="latest")
        self.assertAlmostEqual(pars["bkg"], 0.5)
        self.assertAlmostEqual(pars["amp"], 45.0)

    def test_save_appends_second_combination(self):
        mio.save_calibration(self.db, self._index(wl=488), {"amp": 45.0})
        mio.save_calibration(self.db, self._index(wl=561), {"amp": 30.0})

        allrows = mio.load_database(
            self.db, {"name": "TestScope"}, time_idx="all"
        )
        self.assertEqual(len(allrows), 2)

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            mio.load_database(
                os.path.join(self.tmpdir, "nope.xlsx"), {}, time_idx="all"
            )

    def test_load_unknown_index_raises(self):
        mio.save_calibration(self.db, self._index(), {"amp": 45.0})
        with self.assertRaises(KeyError):
            mio.load_calibration(
                self.db, self._index(name="Ghost"), time_idx="latest"
            )


class TestTimeIndexSelection(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "db.xlsx")
        _write_db(
            self.db,
            [
                # combo A (488/100): two dates -> newer is 2024-01-02 bkg=2
                (
                    "TestScope",
                    488.0,
                    100.0,
                    "2024-01-01",
                    "10:00",
                    {"bkg": 1.0},
                ),
                (
                    "TestScope",
                    488.0,
                    100.0,
                    "2024-01-02",
                    "11:00",
                    {"bkg": 2.0},
                ),
                # combo B (561/50): single entry
                (
                    "TestScope",
                    561.0,
                    50.0,
                    "2024-01-01",
                    "09:00",
                    {"bkg": 3.0},
                ),
            ],
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_all(self):
        out = mio.load_database(self.db, {}, time_idx="all")
        self.assertEqual(len(out), 3)

    def test_last_combinations(self):
        out = mio.load_database(self.db, {}, time_idx="last combinations")
        # One row per (name, wavelength, power) combination.
        self.assertEqual(len(out), 2)
        bkgs = sorted(out["bkg"].tolist())
        # combo A keeps the newer (2.0), combo B keeps 3.0
        self.assertEqual(bkgs, [2.0, 3.0])

    def test_last_date(self):
        out = mio.load_database(self.db, {}, time_idx="last date")
        self.assertEqual(len(out), 1)
        self.assertEqual(out["bkg"].iloc[0], 2.0)

    def test_latest_collapses_to_newest(self):
        pars = mio.load_calibration(
            self.db,
            {
                "name": "TestScope",
                "wavelength [nm]": 488.0,
                "laser_power [mW]": 100.0,
            },
            time_idx="latest",
        )
        self.assertEqual(pars["bkg"], 2.0)

    def test_specific_date_time(self):
        out = mio.load_database(
            self.db,
            {
                "name": "TestScope",
                "wavelength [nm]": 488.0,
                "laser_power [mW]": 100.0,
            },
            time_idx=["2024-01-01", "10:00"],
        )
        # Series for a single matched row.
        self.assertEqual(out["bkg"], 1.0)


class TestDeleteExcel(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = os.path.join(self.tmpdir, "db.xlsx")
        _write_db(
            self.db,
            [
                (
                    "TestScope",
                    488.0,
                    100.0,
                    "2024-01-01",
                    "10:00",
                    {"bkg": 1.0},
                ),
                (
                    "TestScope",
                    561.0,
                    50.0,
                    "2024-01-01",
                    "09:00",
                    {"bkg": 3.0},
                ),
                (
                    "OtherScope",
                    488.0,
                    100.0,
                    "2024-01-01",
                    "10:00",
                    {"bkg": 9.0},
                ),
            ],
        )

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_delete_by_wavelength_wildcard(self):
        # Delete all 488 nm records regardless of device (name wildcard).
        deleted = mio.delete_calibration(self.db, {"wavelength [nm]": 488.0})
        self.assertEqual(deleted, 2)
        remaining = mio.load_database(self.db, {}, time_idx="all")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining["bkg"].iloc[0], 3.0)

    def test_delete_specific_device(self):
        deleted = mio.delete_calibration(self.db, {"name": "OtherScope"})
        self.assertEqual(deleted, 1)

    def test_delete_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            mio.delete_calibration(
                os.path.join(self.tmpdir, "nope.xlsx"), {"name": "x"}
            )


if __name__ == "__main__":
    unittest.main()
