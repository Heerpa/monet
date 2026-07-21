"""
monet/schemas.py
~~~~~~~~~~~~~~~~

Pydantic schemas for the FastAPI server.

:authors: Heinrich Grabmayr, 2024
:copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""

from typing import List, Optional, Union

from pydantic import BaseModel


class CalibrationCreate(BaseModel):
    index: dict
    parameters: dict


class CalibrationQuery(BaseModel):
    index: dict
    time_idx: Union[str, List, None] = "last combinations"


class CalibrationRecord(BaseModel):
    device_name: str
    wavelength_nm: float
    laser_power_mw: float
    calibration_date: str
    calibration_time: str
    parameters: dict


class DatabaseResponse(BaseModel):
    records: List[CalibrationRecord]


class RestartResponse(BaseModel):
    backup_path: str
    remaining_records: int


class CalibrationDeleteQuery(BaseModel):
    device_name: Optional[str] = None
    wavelength_nm: Optional[float] = None
    laser_power_mw: Optional[float] = None
    calibration_date: Optional[str] = None
    calibration_time: Optional[str] = None


class CalibrationDeleteResponse(BaseModel):
    deleted_count: int


class FactorCreate(BaseModel):
    device_name: str
    wavelength_nm: float
    calibration_date: str
    transmission_objective_mean: float
    transmission_objective_std: float
    n_points: int


class FactorRecord(BaseModel):
    device_name: str
    wavelength_nm: float
    calibration_date: str
    transmission_objective_mean: float
    transmission_objective_std: float
    n_points: int


class FactorQuery(BaseModel):
    device_name: Optional[str] = None
    wavelength_nm: Optional[float] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None


class FactorListResponse(BaseModel):
    records: List[FactorRecord]
