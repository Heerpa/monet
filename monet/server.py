"""
    monet/server.py
    ~~~~~~~~~~~~~~~

    FastAPI server for the calibration database.

    :authors: Heinrich Grabmayr, 2024
    :copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""
import json
import os
import shutil
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from monet.models import Calibration, get_engine
from monet.schemas import (
    CalibrationCreate,
    CalibrationQuery,
    CalibrationRecord,
    DatabaseResponse,
    RestartResponse,
)

# Module-level engine/session factory, set during lifespan
_engine = None
_SessionLocal = None


def _get_session():
    return _SessionLocal()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine, _SessionLocal
    db_path = os.environ.get('MONET_DB_PATH', 'calibrations.db')
    _engine = get_engine(db_path)
    _SessionLocal = sessionmaker(bind=_engine)
    yield
    if _engine:
        _engine.dispose()


app = FastAPI(title='Monet Calibration Server', lifespan=lifespan)


def _record_from_row(row: Calibration) -> CalibrationRecord:
    return CalibrationRecord(
        device_name=row.device_name,
        wavelength_nm=row.wavelength_nm,
        laser_power_mw=row.laser_power_mw,
        calibration_date=row.calibration_date,
        calibration_time=row.calibration_time,
        parameters=json.loads(row.parameters_json),
    )


@app.post('/calibrations', response_model=CalibrationRecord)
def save_calibration(data: CalibrationCreate):
    """Save a calibration record. Date/time are auto-generated."""
    now = datetime.now()
    index = data.index
    row = Calibration(
        device_name=str(index.get('name', '')),
        wavelength_nm=float(index.get('wavelength [nm]', 0)),
        laser_power_mw=float(index.get('laser_power [mW]', 0)),
        calibration_date=now.strftime('%Y-%m-%d'),
        calibration_time=now.strftime('%H:%M'),
        parameters_json=json.dumps(data.parameters),
    )
    with _get_session() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        return _record_from_row(row)


@app.post('/calibrations/query', response_model=DatabaseResponse)
def query_calibrations(query: CalibrationQuery):
    """Query calibration records.

    Index values of None act as wildcards (match all).
    time_idx modes: 'latest', 'last date', 'last combinations', 'all',
    or a list of [date] or [date, time].
    """
    index = query.index
    time_idx = query.time_idx

    with _get_session() as session:
        stmt = select(Calibration)

        # Apply index filters (None = wildcard)
        device = index.get('name')
        if device is not None:
            stmt = stmt.where(Calibration.device_name == str(device))

        wavelength = index.get('wavelength [nm]')
        if wavelength is not None:
            stmt = stmt.where(Calibration.wavelength_nm == float(wavelength))

        laser_power = index.get('laser_power [mW]')
        if laser_power is not None:
            stmt = stmt.where(Calibration.laser_power_mw == float(laser_power))

        # Handle time_idx as list: [date] or [date, time]
        if isinstance(time_idx, (list, tuple)):
            if len(time_idx) >= 1:
                stmt = stmt.where(Calibration.calibration_date == str(time_idx[0]))
            if len(time_idx) >= 2:
                stmt = stmt.where(Calibration.calibration_time == str(time_idx[1]))
            # With explicit date/time, just return sorted
            stmt = stmt.order_by(
                Calibration.device_name,
                Calibration.wavelength_nm,
                Calibration.laser_power_mw,
                Calibration.calibration_date,
                Calibration.calibration_time,
            )
            rows = session.execute(stmt).scalars().all()
            return DatabaseResponse(records=[_record_from_row(r) for r in rows])

        # Apply date/time filter from index if present
        date_val = index.get('date')
        if date_val is not None:
            stmt = stmt.where(Calibration.calibration_date == str(date_val))

        time_val = index.get('time')
        if time_val is not None:
            stmt = stmt.where(Calibration.calibration_time == str(time_val))

        # Sort by all index columns + date/time
        stmt = stmt.order_by(
            Calibration.device_name,
            Calibration.wavelength_nm,
            Calibration.laser_power_mw,
            Calibration.calibration_date,
            Calibration.calibration_time,
        )
        rows = session.execute(stmt).scalars().all()

        if not rows:
            raise HTTPException(status_code=404, detail='No matching calibrations found.')

        if time_idx is None or time_idx == 'latest':
            # Return only the last record overall
            return DatabaseResponse(records=[_record_from_row(rows[-1])])

        elif time_idx == 'last date':
            # Find the latest date, return all records from that date
            last_date = max(r.calibration_date for r in rows)
            filtered = [r for r in rows if r.calibration_date == last_date]
            return DatabaseResponse(records=[_record_from_row(r) for r in filtered])

        elif time_idx == 'last combinations':
            # For each (device, wavelength, power) combo, keep only the last entry
            seen = {}
            for r in rows:
                key = (r.device_name, r.wavelength_nm, r.laser_power_mw)
                seen[key] = r  # later entries overwrite earlier (sorted asc)
            result = list(seen.values())
            return DatabaseResponse(records=[_record_from_row(r) for r in result])

        elif time_idx == 'all':
            return DatabaseResponse(records=[_record_from_row(r) for r in rows])

        else:
            raise HTTPException(
                status_code=400,
                detail=f'Unknown time_idx mode: {time_idx}',
            )


@app.post('/database/restart', response_model=RestartResponse)
def restart_database():
    """Backup the current database and prune to only the latest entries."""
    db_path = os.environ.get('MONET_DB_PATH', 'calibrations.db')
    today = datetime.now().strftime('%Y-%m-%d')
    root, ext = os.path.splitext(db_path)
    backup_path = f'{root}_{today}{ext}'

    if os.path.exists(backup_path):
        raise HTTPException(
            status_code=409,
            detail=f'Backup file already exists: {backup_path}',
        )

    # Copy current DB as backup
    shutil.copy2(db_path, backup_path)

    # Keep only last combination per (device, wavelength, power)
    with _get_session() as session:
        all_rows = session.execute(
            select(Calibration).order_by(
                Calibration.device_name,
                Calibration.wavelength_nm,
                Calibration.laser_power_mw,
                Calibration.calibration_date,
                Calibration.calibration_time,
            )
        ).scalars().all()

        # Find rows to keep
        keep = {}
        for r in all_rows:
            key = (r.device_name, r.wavelength_nm, r.laser_power_mw)
            keep[key] = r.id

        keep_ids = set(keep.values())

        # Delete rows not in keep set
        for r in all_rows:
            if r.id not in keep_ids:
                session.delete(r)
        session.commit()

        remaining = session.execute(select(Calibration)).scalars().all()
        return RestartResponse(
            backup_path=backup_path,
            remaining_records=len(remaining),
        )


@app.get('/health')
def health():
    """Health check endpoint."""
    return {'status': 'ok'}
