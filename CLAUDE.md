# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Monet is a Python-based laser power calibration and control suite for microscopy systems. It automates calibration of laser power through attenuators (rotation mounts, NIDAQ, AOTF) and power meters, storing results in either an Excel database (legacy) or a SQLite database behind a FastAPI server (recommended for multi-microscope setups). It exposes the same functionality through three surfaces: an interactive CLI (`cmd.Cmd`), a PyQt5 GUI, and an embeddable widget API (`monet.qt`) for integration into other Qt applications. Named after the impressionist painter for his mastery of light intensity.

## Common Commands

```bash
# Install (development mode)
pip install -r requirements.txt
pip install -e .

# Run tests (uses pytest with coverage)
pytest
pytest monet/tests/test_control.py        # single test file

# Run the application (interactive CLI)
python -m monet calibrate <MicroscopeName>    # 1D / 2D calibration
python -m monet caliaotf <MicroscopeName>     # AOTF frequency/power calibration
python -m monet adjust <MicroscopeName>       # alignment / direct hardware control
python -m monet set <MicroscopeName>          # power setting (open- and closed-loop)

# Run the PyQt5 GUI
python -m monet gui <MicroscopeName>

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
- **Lasers** (`laser.py`): `AbstractLaser` → `Toptica`, `MPBVFL`, `Cobolt`, `Cobolt_OEM`, `LaserQuantum`, `TestLaser`
- **Attenuators** (`attenuation.py`): `AbstractAttenuator` → `KinesisAttenuator`, `AAAOTFAttenuator`, `NIdaqmxAOAttenuator`, `TestAttenuator`
- **Power meters** (`powermeter.py`): `AbstractPowerMeter` → `ThorlabsPowerMeter` (legacy VISA/USBTMC via `ThorlabsPM100`), `ThorlabsTLPMPowerMeter` (Thorlabs TLPM driver / Optical Power Monitor, e.g. PM100D2 or any PMxxx on the TLPM driver), `TestPowerMeter`
- **Beam path** (`beampath.py`): `BeamPath` orchestrates `AbstractBeamPathObject` implementations (`NikonShutter`, `NikonFilterWheel`, `NikonNosepiece`, `TestShutter`) via Micro-Manager

`Test*` implementations provide simulated hardware. Hardware-SDK imports (`pycromanager`, `pycobolt`, `microscope`, `msl.equipment`, `nidaqmx`, `ThorlabsPM100`) and the `TLPM_64.dll` ctypes load (used by `ThorlabsTLPMPowerMeter`) are loaded **lazily** inside the constructors / methods that need them — the package can be imported and the `Test*` classes used on machines without any of those SDKs installed.

### Core Workflow
1. **Calibration** (`calibrate.py`): `CalibrationProtocol1D` sweeps attenuator positions while reading power; `CalibrationProtocol2D` extends this across multiple lasers and power levels.
2. **Analysis** (`analysis.py`): Fits attenuation curves (sinusoidal, linear, polynomial, or point models via `lmfit`) to map attenuator position → output power.
3. **Control** (`control.py`):
   - `IlluminationControl` (single-laser) and `IlluminationLaserControl` (multi-laser) use stored calibrations.
   - Three power-setting paths: the `power` property (combined mode — adjust both), `set_power_fixed_laser` (adjust attenuator only), `set_power_fixed_attenuator` (adjust laser only). `accessible_power_range(mode, laser)` reports the reachable range per mode.
   - `run_power_feedback(instrument, powermeter, target_pwr, laser, mode, ...)` is a module-level function implementing the closed-loop PI controller (proportional in `fixed_attenuator` mode, full PI with anti-windup in `fixed_laser` mode). Used by both the CLI `feedback` command and the GUI Set Power tab — single source of truth.
4. **Persistence** (`io.py`): Calibration parameters stored in either:
   - **Excel database** (legacy): Multi-level index with device, wavelength, laser_power, date, time.
   - **SQLite + FastAPI server** (recommended): Set `database: http://server:8000` in config to use HTTP mode. `cache.py` provides a local SQLite mirror + outbox so calibrations can be saved offline and replayed when the server is reachable.

### Database Server Architecture
- **`models.py`**: SQLAlchemy `Calibration` model with `parameters_json` column storing fit parameters as JSON.
- **`schemas.py`**: Pydantic request/response models for the API.
- **`server.py`**: FastAPI server with endpoints:
  - `POST /calibrations` — save a calibration (replaces `save_calibration()`).
  - `POST /calibrations/query` — query calibrations (replaces `load_calibration()` and `load_database()`).
  - `POST /database/restart` — backup and prune database (replaces `restart_database()`).
  - `GET /dashboard` — HTML overview (see `dashboard.py`).
  - `GET /health` — health check.
- **`io.py`**: Dispatch layer — if `database` config value starts with `http://` or `https://`, routes through HTTP (with offline fallback via `cache.py`); otherwise uses Excel file I/O.
- **`migrate.py`**: One-time migration from Excel to SQLite.

### Interactive CLI
`__main__.py` uses Python's `cmd.Cmd` to provide interactive shells:
- `MonetCalibrateInteractive`: `calibrate`, `calibrate_aotf`, `set`, `config --param: value`, `load_config`, `save_config`, etc.
- `MonetAdjustInteractive`: `laser`, `laser_power`, `attenuate`, `open`, `close`, `autoshutter`, `restartdb`, `py`.
- `MonetSetInteractive`: `laser`, `laser_power`, `power`, `attenuate`, `open`, `close`, `autoshutter`, `multi_laser`, `status`, **`mode`**, **`range`**, **`feedback <power> [tol]`**, **`feedback_config --kp: --ki: --tol: --max_iter:`**, `measure` (now reports calibration deviation and writes the result into the MicroManager acquisition comment when `pycromanager` is available).

### GUI (`gui.py`, `qt.py`)
- **Four tab widgets** — `CalibrateTab`, `SetPowerTab`, `AdjustTab`, `DatabaseTab`. Each is a self-contained `QWidget` that emits `status = pyqtSignal(str, int)` (via `_emit_status`) instead of writing to a status bar — this is what makes them embeddable. All hardware operations run in `GenericWorker(QThread)` so the UI never blocks.
- **`MonetWidget(QWidget)`** — the embeddable container. Holds a `QTabWidget` + an optional microscope-picker toolbar. Re-emits tab status signals as `status_changed`. Public API: `set_microscope`, `connect_microscope`, `set_pc`, `shutdown`, `tab(key)`; signals: `status_changed`, `connected`, `connect_error`, `calibration_started`, `calibration_finished`. Constructor options: `show_toolbar`, `tabs=('set_power','calibrate','database'[,'adjust'])`, `initial_microscope`.
- **`MonetMainWindow(QMainWindow)`** — thin top-level wrapper used by `python -m monet gui`; just hosts a `MonetWidget` and pipes `status_changed` into the status bar.
- **`monet.qt`** — public re-export module (`MonetWidget`, `MonetMainWindow`, individual tab classes). Host applications should import from here so the rest of the package stays Qt-free.
- The GUI's feedback loop is **not duplicated** — it calls `control.run_power_feedback(...)` with a `progress_callback` that pipes points into a `FeedbackPlotDialog`.
- `util.update_mm_acquisition_comment(...)` (used by both GUI and CLI) writes measured power into the MicroManager acquisition comment; no-ops gracefully when `pycromanager` is absent.

### Key Constants
Defined in `__init__.py`: `DEVICE_TAG`, `LASER_TAG`, `POWER_TAG`, `DATABASE_INDEXLEVELS` — used throughout for consistent database indexing.

## Testing

Tests use `unittest.TestCase` with pytest as the runner. Test hardware is simulated via `TestPowerMeter`, `TestAttenuator`, and `TestLaser` classes. Test fixtures live in `monet/tests/TestData/`.

- Server tests (`test_server.py`) use FastAPI's `TestClient` for in-process testing.
- HTTP I/O tests (`test_io_http.py`) monkey-patch `requests.post` to route through TestClient.
- `test_control.py` exercises `run_power_feedback` (both modes, convergence asserted) and `accessible_power_range` with simulated hardware (`time.sleep` is patched out so the PI loop runs instantly).
- `test_gui_widget.py` smoke-tests `MonetWidget` and the tabs under `QT_QPA_PLATFORM=offscreen`; auto-skips when PyQt5 is not installed.
