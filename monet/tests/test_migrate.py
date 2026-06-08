"""
    monet/tests/test_migrate.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Tests for the Excel -> SQLite migration utility.

    :authors: Heinrich Grabmayr, 2024
    :copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""
import json
import os
import tempfile
import unittest

import pandas as pd
from sqlalchemy.orm import Session

from monet import DATABASE_INDEXLEVELS
from monet.migrate import migrate_excel_to_sqlite
from monet.models import Calibration, get_engine


def _make_excel(path):
    """Write a small calibration database matching DATABASE_INDEXLEVELS."""
    index = pd.MultiIndex.from_tuples(
        [
            ('TestScope', 488.0, 100.0, '2024-01-01', '12:00:00'),
            ('TestScope', 561.0, 50.0, '2024-01-02', '09:30:00'),
        ],
        names=DATABASE_INDEXLEVELS,
    )
    df = pd.DataFrame(
        {
            'bkg': [0.1, 0.2],
            'amp': [10.0, 5.0],
            'phi': [30.0, float('nan')],  # NaN should be dropped per-row
        },
        index=index,
    )
    df.to_excel(path)


class TestMigrate(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.excel_path = os.path.join(self.tmpdir, 'power_database.xlsx')
        self.db_path = os.path.join(self.tmpdir, 'calibrations.db')
        _make_excel(self.excel_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_migrate_inserts_all_rows(self):
        migrate_excel_to_sqlite(self.excel_path, self.db_path)

        engine = get_engine(self.db_path)
        with Session(engine) as session:
            rows = session.query(Calibration).order_by(
                Calibration.wavelength_nm).all()

        self.assertEqual(len(rows), 2)

    def test_migrate_preserves_index_fields(self):
        migrate_excel_to_sqlite(self.excel_path, self.db_path)

        engine = get_engine(self.db_path)
        with Session(engine) as session:
            row = session.query(Calibration).filter_by(
                wavelength_nm=488.0).one()

        self.assertEqual(row.device_name, 'TestScope')
        self.assertEqual(row.laser_power_mw, 100.0)
        self.assertEqual(row.calibration_date, '2024-01-01')
        self.assertEqual(row.calibration_time, '12:00:00')

    def test_migrate_stores_parameters_json(self):
        migrate_excel_to_sqlite(self.excel_path, self.db_path)

        engine = get_engine(self.db_path)
        with Session(engine) as session:
            row_488 = session.query(Calibration).filter_by(
                wavelength_nm=488.0).one()
            row_561 = session.query(Calibration).filter_by(
                wavelength_nm=561.0).one()

        params_488 = json.loads(row_488.parameters_json)
        self.assertEqual(params_488['bkg'], 0.1)
        self.assertEqual(params_488['amp'], 10.0)
        self.assertEqual(params_488['phi'], 30.0)

        # The NaN 'phi' for the 561 row must be dropped, not serialized.
        params_561 = json.loads(row_561.parameters_json)
        self.assertNotIn('phi', params_561)
        self.assertEqual(params_561['amp'], 5.0)


if __name__ == '__main__':
    unittest.main()
