# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Monet is a Python-based laser power calibration and control suite for microscopy systems. It automates calibration of laser power through attenuators (rotation mounts, NIDAQ, AOTF) and power meters, storing results in either an Excel database (legacy) or a SQLite database behind a FastAPI server (recommended for multi-microscope setups). Named after the impressionist painter for his mastery of light intensity.

## Common Commands

```bash
# Install (development mode)
pip install -r requirements.txt
python setup.py develop

# Run tests (uses pytest with coverage)
pytest
pytest monet/tests/test_analysis.py       # single test file

# Run the application (interactive CLI)
python -m monet calibrate <MicroscopeName>    # calibration mode
python -m monet caliaotf <MicroscopeName>     # AOTF calibration
python -m monet adjust <MicroscopeName>       # power adjustment mode
python -m monet set <MicroscopeName>          # simple power setting

# Run the database server
python -m monet serve --db-path calibrations.db --host 0.0.0.0 --port 8000

# Migrate an Excel database to SQLite
python -m monet migrate --source path/to/power_database.xlsx --db-path calibrations.db
```

## Architecture

### Configuration-Driven Design
All hardware components are instantiated dynamically via `load_class()` in `util.py`, which takes a classpath string (e.g. `"monet.attenuation.KinesisAttenuator"`) and kwargs from YAML config dicts. Configs are loaded from `env.yaml`-referenced paths or fall back to built-in defaults in `__init__.py`. This allows swapping hardware implementations without code changes.

### Hardware Abstraction Layer
Each hardware type has an abstract base class with multiple implementations:
- **Lasers** (`laser.py`): `AbstractLaser` → `Toptica`, `MPBVFL`, `CoboltLaser`, `TestLaser`
- **Attenuators** (`attenuation.py`): `AbstractAttenuator` → `KinesisAttenuator`, `NIDAQAttenuator`, `AOTFAttenuator`, `TestAttenuator`
- **Power meters** (`powermeter.py`): `AbstractPowerMeter` → `ThorlabsPowerMeter`, `TestPowerMeter`
- **Beam path** (`beampath.py`): Filter wheels and shutters via Micro-Manager

`Test*` implementations provide simulated hardware for testing without physical devices.

### Core Workflow
1. **Calibration** (`calibrate.py`): `CalibrationProtocol1D` sweeps attenuator positions while reading power; `CalibrationProtocol2D` extends this across multiple lasers and power levels
2. **Analysis** (`analysis.py`): Fits attenuation curves (sinusoidal or polynomial models via `lmfit`) to map attenuator position → output power
3. **Control** (`control.py`): `IlluminationControl` (single laser) and `IlluminationLaserControl` (multi-laser) use stored calibration to set desired power by computing the required attenuator position
4. **Persistence** (`io.py`): Calibration parameters stored in either:
   - **Excel database** (legacy): Multi-level index with device, wavelength, laser_power, date, time
   - **SQLite + FastAPI server** (recommended): Set `database: http://server:8000` in config to use HTTP mode

### Database Server Architecture
- **`models.py`**: SQLAlchemy `Calibration` model with `parameters_json` column storing fit parameters as JSON
- **`schemas.py`**: Pydantic request/response models for the API
- **`server.py`**: FastAPI server with 4 endpoints:
  - `POST /calibrations` — save a calibration (replaces `save_calibration()`)
  - `POST /calibrations/query` — query calibrations (replaces `load_calibration()` and `load_database()`)
  - `POST /database/restart` — backup and prune database (replaces `restart_database()`)
  - `GET /health` — health check
- **`io.py`**: Dispatch layer — if `database` config value starts with `http://` or `https://`, routes through HTTP; otherwise uses Excel file I/O unchanged
- **`migrate.py`**: One-time migration from Excel to SQLite

### Interactive CLI
`__main__.py` uses Python's `cmd.Cmd` to provide interactive shells (`MonetCalibrateInteractive`, `MonetAdjustInteractive`, `MonetSetInteractive`) with commands like `calibrate`, `set <power>`, and `config --param: value`.

### Key Constants
Defined in `__init__.py`: `DEVICE_TAG`, `LASER_TAG`, `POWER_TAG`, `DATABASE_INDEXLEVELS` — used throughout for consistent database indexing.

## Testing

Tests use `unittest.TestCase` with pytest as the runner. Test hardware is simulated via `TestPowerMeter`, `TestAttenuator`, and `TestLaser` classes. Test fixtures live in `monet/tests/TestData/`.

Server tests (`test_server.py`) use FastAPI's `TestClient` for in-process testing. HTTP I/O tests (`test_io_http.py`) monkey-patch `requests.post` to route through TestClient.
