"""
monet/cache.py
~~~~~~~~~~~~~~

Local SQLite cache with outbox for offline-first server connectivity.

When the remote server is unreachable, writes are queued in an outbox and
reads fall back to the last known local state.  The outbox is replayed
automatically the next time a successful connection is established.

:authors: Heinrich Grabmayr, 2024
:copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import Column, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from monet.models import Base as _DataBase
from monet.models import Calibration, Factor

logger = logging.getLogger(__name__)

# ── Module-level registry ──

_DEFAULT_CACHE_DIR: Path = Path.home() / '.monet'
_cache_registry: Dict[str, 'LocalCache'] = {}


def _get_cache(server_url: str) -> 'LocalCache':
    """Return the singleton LocalCache for *server_url*.

    Creates it if it does not exist yet.
    """
    if server_url not in _cache_registry:
        import hashlib

        url_hash = hashlib.md5(server_url.encode()).hexdigest()[:8]
        db_path = _DEFAULT_CACHE_DIR / f'cache_{url_hash}.db'
        _cache_registry[server_url] = LocalCache(db_path)
    return _cache_registry[server_url]


def _set_cache_dir(path: Path) -> None:
    """Override the default cache directory (primarily for tests)."""
    global _DEFAULT_CACHE_DIR
    _DEFAULT_CACHE_DIR = Path(path)


def _clear_cache_registry() -> None:
    """Discard all cached LocalCache instances (used in tests)."""
    _cache_registry.clear()


# ── Outbox SQLAlchemy model ──


class _OutboxBase(DeclarativeBase):
    pass


class OutboxEntry(_OutboxBase):
    __tablename__ = 'outbox'

    id = Column(Integer, primary_key=True, autoincrement=True)
    # HTTP endpoint path, e.g. '/calibrations', '/factors'
    endpoint = Column(String, nullable=False)
    # JSON-serialised request body
    payload_json = Column(Text, nullable=False)
    # ISO datetime string of when this entry was created locally
    created_at = Column(String, nullable=False)
    # For '/calibrations' saves: JSON dict identifying the locally-generated
    # cache entry so it can be cleaned up after a successful sync.
    local_key_json = Column(Text)
    # Diagnostics
    failed_attempts = Column(Integer, default=0, nullable=False)
    last_error = Column(Text)


# ── LocalCache ──


class LocalCache:
    """SQLite-backed mirror of remote calibration data with an outbox queue."""

    def __init__(self, db_path: Path) -> None:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path

        engine = create_engine(
            f'sqlite:///{db_path}',
            connect_args={'check_same_thread': False},
        )
        with engine.connect() as conn:
            conn.exec_driver_sql('PRAGMA journal_mode=WAL')
            conn.commit()
        # Create both data tables and the outbox in the same file
        _DataBase.metadata.create_all(engine)
        _OutboxBase.metadata.create_all(engine)
        self._Session = sessionmaker(bind=engine)

    # ── Calibration CRUD ──

    def upsert_calibration(self, record: Dict) -> None:
        """Insert or update a calibration record in the local cache."""
        with self._Session() as session:
            existing = (
                session.query(Calibration)
                .filter_by(
                    device_name=record['device_name'],
                    wavelength_nm=float(record['wavelength_nm']),
                    laser_power_mw=float(record['laser_power_mw']),
                    calibration_date=record['calibration_date'],
                    calibration_time=record['calibration_time'],
                )
                .first()
            )
            if existing:
                existing.parameters_json = json.dumps(record['parameters'])
            else:
                session.add(
                    Calibration(
                        device_name=record['device_name'],
                        wavelength_nm=float(record['wavelength_nm']),
                        laser_power_mw=float(record['laser_power_mw']),
                        calibration_date=record['calibration_date'],
                        calibration_time=record['calibration_time'],
                        parameters_json=json.dumps(record['parameters']),
                    )
                )
            session.commit()

    def delete_calibrations(self, index: Dict) -> int:
        """Delete calibrations matching *index*.

        None or missing values act as wildcards. Returns the number of
        deleted rows.
        """
        with self._Session() as session:
            q = session.query(Calibration)
            if index.get('name') is not None:
                q = q.filter_by(device_name=index['name'])
            if index.get('wavelength [nm]') is not None:
                q = q.filter_by(wavelength_nm=float(index['wavelength [nm]']))
            if index.get('laser_power [mW]') is not None:
                q = q.filter_by(
                    laser_power_mw=float(index['laser_power [mW]'])
                )
            if index.get('date') is not None:
                q = q.filter_by(calibration_date=index['date'])
            if index.get('time') is not None:
                q = q.filter_by(calibration_time=index['time'])
            count = q.count()
            q.delete()
            session.commit()
            return count

    def query_calibrations(self, index: Dict, time_idx: Any) -> List[Dict]:
        """Query calibrations, replicating the server's *time_idx* semantics.

        Returns a list of record dicts ordered by (date, time).
        """
        with self._Session() as session:
            q = session.query(Calibration)
            # Filter by provided index fields (None / missing = wildcard)
            if index.get('name') is not None:
                q = q.filter_by(device_name=index['name'])
            if index.get('wavelength [nm]') is not None:
                q = q.filter_by(wavelength_nm=float(index['wavelength [nm]']))
            if index.get('laser_power [mW]') is not None:
                q = q.filter_by(
                    laser_power_mw=float(index['laser_power [mW]'])
                )

            # List time_idx: filter by exact date (and optionally time)
            if isinstance(time_idx, (list, tuple)):
                if len(time_idx) >= 1:
                    q = q.filter_by(calibration_date=time_idx[0])
                if len(time_idx) >= 2:
                    q = q.filter_by(calibration_time=time_idx[1])

            q = q.order_by(
                Calibration.calibration_date, Calibration.calibration_time
            )
            all_records = [self._cal_to_dict(r) for r in q.all()]

        if not all_records:
            return []

        if time_idx is None or time_idx == 'latest':
            return [all_records[-1]]

        if time_idx == 'last date':
            last_date = max(r['calibration_date'] for r in all_records)
            return [
                r for r in all_records if r['calibration_date'] == last_date
            ]

        if time_idx == 'last combinations':
            # For each (device, wavelength, power) keep the latest entry
            seen: Dict[Tuple, Dict] = {}
            for r in all_records:
                key = (
                    r['device_name'],
                    r['wavelength_nm'],
                    r['laser_power_mw'],
                )
                seen[key] = (
                    r  # later entries overwrite earlier ones (sorted above)
                )
            return list(seen.values())

        # 'all' or list-filtered (already filtered in the query above)
        return all_records

    def _cal_to_dict(self, r: Calibration) -> Dict:
        return {
            'device_name': r.device_name,
            'wavelength_nm': r.wavelength_nm,
            'laser_power_mw': r.laser_power_mw,
            'calibration_date': r.calibration_date,
            'calibration_time': r.calibration_time,
            'parameters': json.loads(r.parameters_json),
        }

    # ── Factor CRUD ──

    def upsert_factor(self, record: Dict) -> None:
        """Insert or update a factor record in the local cache."""
        with self._Session() as session:
            existing = (
                session.query(Factor)
                .filter_by(
                    device_name=record['device_name'],
                    wavelength_nm=float(record['wavelength_nm']),
                    calibration_date=record['calibration_date'],
                )
                .first()
            )
            if existing:
                existing.transmission_objective_mean = record[
                    'transmission_objective_mean'
                ]
                existing.transmission_objective_std = record[
                    'transmission_objective_std'
                ]
                existing.n_points = record['n_points']
            else:
                session.add(
                    Factor(
                        device_name=record['device_name'],
                        wavelength_nm=float(record['wavelength_nm']),
                        calibration_date=record['calibration_date'],
                        transmission_objective_mean=record[
                            'transmission_objective_mean'
                        ],
                        transmission_objective_std=record[
                            'transmission_objective_std'
                        ],
                        n_points=record['n_points'],
                    )
                )
            session.commit()

    def query_factors(
        self,
        device: Optional[str],
        laser: Optional[Any],
    ) -> List[Dict]:
        """Query factors from local cache, returning a list of record dicts."""
        with self._Session() as session:
            q = session.query(Factor)
            if device is not None:
                q = q.filter_by(device_name=device)
            if laser is not None:
                try:
                    q = q.filter_by(wavelength_nm=float(int(laser)))
                except (ValueError, TypeError):
                    pass
            return [
                {
                    'device_name': r.device_name,
                    'wavelength_nm': r.wavelength_nm,
                    'calibration_date': r.calibration_date,
                    'transmission_objective_mean': r.transmission_objective_mean,  # noqa: E501
                    'transmission_objective_std': r.transmission_objective_std,
                    'n_points': r.n_points,
                }
                for r in q.order_by(Factor.calibration_date).all()
            ]

    # ── Outbox ──

    def add_to_outbox(
        self,
        endpoint: str,
        payload: Dict,
        local_key: Optional[Dict] = None,
    ) -> int:
        """Queue *payload* for *endpoint*, to replay when the server is back.

        *local_key* identifies the corresponding locally-generated cache entry
        so it can be cleaned up after a successful sync (only used for
        '/calibrations' saves that were assigned a local timestamp).

        Returns the outbox entry id.
        """
        from datetime import datetime

        with self._Session() as session:
            entry = OutboxEntry(
                endpoint=endpoint,
                payload_json=json.dumps(payload),
                created_at=datetime.utcnow().isoformat(),
                local_key_json=(
                    json.dumps(local_key) if local_key is not None else None
                ),
            )
            session.add(entry)
            session.commit()
            eid = entry.id
        logger.warning(
            'Server unreachable — queued %s to local outbox (id=%d)',
            endpoint,
            eid,
        )
        return eid

    def get_pending_outbox(
        self,
    ) -> List[Tuple[int, str, Dict, Optional[Dict]]]:
        """Return pending entries as (id, endpoint, payload, local_key)."""
        with self._Session() as session:
            entries = (
                session.query(OutboxEntry)
                .order_by(OutboxEntry.created_at)
                .all()
            )
            return [
                (
                    e.id,
                    e.endpoint,
                    json.loads(e.payload_json),
                    json.loads(e.local_key_json) if e.local_key_json else None,
                )
                for e in entries
            ]

    def remove_outbox_entry(self, entry_id: int) -> None:
        """Delete a successfully synced outbox entry."""
        with self._Session() as session:
            e = session.get(OutboxEntry, entry_id)
            if e:
                session.delete(e)
                session.commit()

    def record_outbox_failure(self, entry_id: int, error: str) -> None:
        """Increment the failure counter on an outbox entry."""
        with self._Session() as session:
            e = session.get(OutboxEntry, entry_id)
            if e:
                e.failed_attempts += 1
                e.last_error = str(error)[:512]
                session.commit()

    def pending_outbox_count(self) -> int:
        """Return the number of entries waiting to be synced."""
        with self._Session() as session:
            return session.query(OutboxEntry).count()
