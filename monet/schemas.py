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
    time_idx: Union[str, List, None] = 'last combinations'


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
