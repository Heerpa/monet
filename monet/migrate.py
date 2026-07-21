"""
monet/migrate.py
~~~~~~~~~~~~~~~~

Migration utility to convert Excel calibration databases to SQLite.

:authors: Heinrich Grabmayr, 2024
:copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""

import json

import pandas as pd
from sqlalchemy.orm import Session

from monet import DATABASE_INDEXLEVELS
from monet.models import Calibration, get_engine


def migrate_excel_to_sqlite(excel_path, db_path):
    """Read an existing Excel database and insert all records into SQLite.

    Preserves original dates and times from the Excel database.

    Parameters
    ----------
    excel_path : str
        Path to the source Excel database.
    db_path : str
        Path to the destination SQLite database file.
    """
    n_index = len(DATABASE_INDEXLEVELS)
    df = pd.read_excel(excel_path, index_col=list(range(n_index)))

    engine = get_engine(db_path)
    inserted = 0

    with Session(engine) as session:
        for idx, row in df.iterrows():
            # idx is a tuple matching DATABASE_INDEXLEVELS
            if not isinstance(idx, tuple):
                idx = (idx,)

            params = {col: row[col] for col in row.index if pd.notna(row[col])}

            cal = Calibration(
                device_name=str(idx[0]),
                wavelength_nm=float(idx[1]),
                laser_power_mw=float(idx[2]),
                calibration_date=str(idx[3]),
                calibration_time=str(idx[4]),
                parameters_json=json.dumps(params),
            )
            session.add(cal)
            inserted += 1

        session.commit()

    print(f"Migrated {inserted} records from {excel_path} to {db_path}")
