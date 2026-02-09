# Monet

A Python-based laser power calibration and control suite for microscopy systems. Monet automates calibration of laser power through attenuators (rotation mounts, NIDAQ, AOTF) and power meters, storing results in a SQLite database behind a FastAPI server or in a legacy Excel file. Named after the impressionist painter for his mastery of light intensity.

## Features

- Automated power calibration across multiple lasers and power levels
- Sinusoidal and polynomial curve fitting for attenuation mapping
- Interactive CLI for calibration, adjustment, and power setting
- Centralized database server for concurrent multi-microscope access
- Hardware abstraction layer with pluggable laser, attenuator, and power meter drivers
- Migration tooling from legacy Excel databases to SQLite

## Prerequisites

**Hardware drivers** (install as needed for your setup):

- Kinesis Rotation Mount: Thorlabs Kinesis software
- Thorlabs PowerMeter: Thorlabs Optical Power Monitor software
- NI DAQ: NI-DAQmx driver
- Micro-Manager: for beam path control (filter wheels, shutters)

**Python >= 3.10** with Anaconda (recommended):

```bash
conda create -n monet python=3.10
conda activate monet
```

## Installation

```bash
pip install -r requirements.txt
python setup.py develop
```

## Quick Start

### Single-microscope setup (Excel database)

Place the power meter head above the objective, connect the power meter, and switch on the laser.

```bash
# Calibrate a microscope
python -m monet calibrate Voyager

# Set power interactively
python -m monet set Voyager
```

Inside the interactive shell:

```
(monet set) laser 561
(monet set) power 50
(monet set) status
(monet set) exit
```

### Multi-microscope setup (database server)

Start the centralized database server on one lab machine:

```bash
python -m monet serve --db-path /shared/calibrations.db --host 0.0.0.0 --port 8000
```

Then configure each microscope's YAML config to point at the server:

```yaml
database: http://server-hostname:8000
```

All `calibrate`, `set`, and `adjust` commands work unchanged -- `monet` automatically routes through HTTP when the database path is a URL.

## Usage

| Mode | Command | Description |
|---|---|---|
| Calibrate | `python -m monet calibrate <Name>` | Run power calibration protocol |
| AOTF Calibrate | `python -m monet caliaotf <Name>` | Calibrate AOTF frequency and power |
| Adjust | `python -m monet adjust <Name>` | Interactive laser alignment and adjustment |
| Set | `python -m monet set <Name>` | Set laser power from existing calibration |
| Serve | `python -m monet serve` | Start the database server |
| Migrate | `python -m monet migrate --source <xlsx> --db-path <db>` | Migrate Excel database to SQLite |

### Server options

```bash
python -m monet serve --host 0.0.0.0 --port 8000 --db-path calibrations.db
```

### Migration from Excel

If you have an existing Excel calibration database, migrate it to SQLite:

```bash
python -m monet migrate --source power_database.xlsx --db-path calibrations.db
```

This preserves all historical calibration dates and times.

## API Reference

When running in server mode, monet exposes these HTTP endpoints:

| Endpoint | Method | Description |
|---|---|---|
| `/calibrations` | POST | Save a new calibration record |
| `/calibrations/query` | POST | Query calibration records (supports filtering and time modes) |
| `/database/restart` | POST | Backup current database and prune to latest entries |
| `/health` | GET | Health check |

## Configuration

Microscope configurations are defined in YAML files referenced by `env.yaml`. Each config specifies:

- `database` -- file path (`.xlsx`) or server URL (`http://...`)
- `index` -- microscope name, wavelength, laser power
- `powermeter` -- classpath and init kwargs
- `attenuation` -- classpath and init kwargs
- `analysis` -- classpath and init kwargs (curve fitting parameters)
- `lasers` -- per-wavelength laser definitions (for 2D protocols)
- `beampath` -- filter wheel and shutter definitions

See `monet/__init__.py` for example configurations.

## Architecture

```
monet/
├── __init__.py        # Configuration loading, constants
├── __main__.py        # CLI entry point (calibrate, set, adjust, serve, migrate)
├── calibrate.py       # CalibrationProtocol1D / 2D
├── analysis.py        # Curve fitting (sinusoidal, polynomial, point)
├── control.py         # IlluminationControl / IlluminationLaserControl
├── io.py              # Database I/O with Excel/HTTP dispatch
├── server.py          # FastAPI database server
├── models.py          # SQLAlchemy models
├── schemas.py         # Pydantic request/response schemas
├── migrate.py         # Excel → SQLite migration
├── laser.py           # Laser drivers (Toptica, MPBVFL, Cobolt, Test)
├── attenuation.py     # Attenuator drivers (Kinesis, NIDAQ, AOTF, Test)
├── powermeter.py      # Power meter drivers (Thorlabs, Test)
├── beampath.py        # Beam path control (filter wheels, shutters)
├── aotf_cali.py       # AOTF frequency/power calibration
└── util.py            # Dynamic class loading
```

## Testing

```bash
# Run all tests
pytest

# Run a specific test file
pytest monet/tests/test_server.py -v

# Run with coverage report
pytest --cov=monet
```

Tests use `unittest.TestCase` with pytest as the runner. Hardware is simulated via `TestPowerMeter`, `TestAttenuator`, and `TestLaser` classes. Server tests use FastAPI's `TestClient` for in-process testing without a running server.

## License

BSD 2-Clause. See [LICENSE.md](LICENSE.md) for details.
