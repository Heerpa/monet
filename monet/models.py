"""
monet/models.py
~~~~~~~~~~~~~~~

SQLAlchemy models for the calibration database.

:authors: Heinrich Grabmayr, 2024
:copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""

from sqlalchemy import Column, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Factor(Base):
    __tablename__ = 'factors'

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_name = Column(String, nullable=False, index=True)
    wavelength_nm = Column(Float, nullable=False, index=True)
    calibration_date = Column(String, nullable=False, index=True)
    transmission_objective_mean = Column(Float, nullable=False)
    transmission_objective_std = Column(Float, nullable=False)
    n_points = Column(Integer, nullable=False)


class Calibration(Base):
    __tablename__ = 'calibrations'

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_name = Column(String, nullable=False, index=True)
    wavelength_nm = Column(Float, nullable=False, index=True)
    laser_power_mw = Column(Float, nullable=False, index=True)
    calibration_date = Column(String, nullable=False, index=True)
    calibration_time = Column(String, nullable=False, index=True)
    parameters_json = Column(Text, nullable=False)


def get_engine(db_path):
    """Create a SQLAlchemy engine and ensure all tables exist.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.

    Returns
    -------
    engine : sqlalchemy.Engine
        SQLAlchemy Engine instance.
    """
    engine = create_engine(
        f'sqlite:///{db_path}',
        connect_args={'check_same_thread': False},
    )
    # Enable WAL mode for concurrent read safety
    with engine.connect() as conn:
        conn.exec_driver_sql('PRAGMA journal_mode=WAL')
        conn.commit()
    Base.metadata.create_all(engine)
    return engine
