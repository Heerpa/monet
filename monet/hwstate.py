#!/usr/bin/env python
"""
monet/hwstate.py
~~~~~~~~~~~~~~~~

Persistence of per-laser hardware settings (laser power set-point and
attenuator position) so they can be restored after an unexpected restart
(e.g. following a MicroManager crash or a computer reboot).

Settings are stored as JSON in the user's home directory, keyed by microscope
name and laser wavelength::

    {
        "MicroscopeName": {
            "488": {"laser_power": 100.0, "attenuator": 12.34},
            "561": {"laser_power": 200.0, "attenuator": 45.6}
        }
    }

Only the laser power set-point and the attenuator position are stored; the
laser on/off state is deliberately not persisted, so a laser is never switched
on automatically at startup. The number of lasers stored may differ from the
number connected in any given session — restoring simply skips lasers that
are not currently present.

:authors: Heinrich Grabmayr, 2026
:copyright: Copyright (c) 2026 Jungmann Lab, MPI of Biochemistry
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Directory and file the persistent hardware state is stored in.
STATE_DIR = Path.home() / ".monet"
STATE_FILE = STATE_DIR / "hardware_state.json"


def _state_path():
    """Return the path of the hardware-state file (overridable in tests)."""
    return STATE_FILE


def load_state():
    """Return the full persisted state as a dict, or ``{}`` if missing/unreadable."""
    path = _state_path()
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning("Hardware state file %s is not a dict; ignoring.", path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("Could not read hardware state %s: %s", path, exc)
    return {}


def _write_state(data):
    """Write the full state dict atomically; failures are logged, not raised."""
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and replace, so an interrupted write never
        # leaves a half-written (corrupt) state file behind.
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("Could not write hardware state %s: %s", path, exc)


def get_laser_state(microscope, laser):
    """Return ``{'laser_power': ..., 'attenuator': ...}`` for one laser, or None.

    Parameters
    ----------
    microscope : str
        Microscope name (the ``DEVICE_TAG`` value).
    laser : str or int
        Laser wavelength.
    """
    data = load_state()
    scope = data.get(str(microscope))
    if not isinstance(scope, dict):
        return None
    entry = scope.get(str(laser))
    return entry if isinstance(entry, dict) else None


def save_laser_state(microscope, laser, laser_power=None, attenuator=None):
    """Persist the laser power and/or attenuator position for one laser line.

    Only the arguments that are not ``None`` are updated; an existing value for
    the other field is preserved. The call is a no-op if both are ``None``.

    Parameters
    ----------
    microscope : str
        Microscope name (the ``DEVICE_TAG`` value).
    laser : str or int
        Laser wavelength.
    laser_power : float, optional
        Laser power set-point in mW.
    attenuator : float, optional
        Attenuator position.
    """
    if laser_power is None and attenuator is None:
        return
    data = load_state()
    scope = data.get(str(microscope))
    if not isinstance(scope, dict):
        scope = {}
        data[str(microscope)] = scope
    entry = scope.get(str(laser))
    if not isinstance(entry, dict):
        entry = {}
        scope[str(laser)] = entry
    if laser_power is not None:
        entry["laser_power"] = float(laser_power)
    if attenuator is not None:
        entry["attenuator"] = float(attenuator)
    _write_state(data)
