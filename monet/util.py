#!/usr/bin/env python
"""
monet/util.py
~~~~~~~~~~~~~

Utility functions for the monet package

:authors: Heinrich Grabmayr, 2022
:copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""

import logging
from importlib import import_module

logger = logging.getLogger(__name__)


def release_hardware(obj, _seen=None):
    """Best-effort release of a hardware object's OS handles.

    Closes serial ports, SDK sessions and motor connections so the device
    can be re-opened by a fresh connection (e.g. when reconnecting in the
    GUI after a laser was switched on). Tries the object's own
    ``stop_polling`` / ``close`` / ``disconnect`` methods and recurses into
    common wrapped handles (``las``, ``laser``, ``lowlvl``, ``device``,
    ``pm``). All errors are swallowed, and ``None`` / simulated ``Test*``
    devices are no-ops.

    Parameters
    ----------
    obj : object or None
        The hardware wrapper to release.
    """
    if obj is None:
        return
    if _seen is None:
        _seen = set()
    if id(obj) in _seen:
        return
    _seen.add(id(obj))

    for name in ("stop_polling", "close", "disconnect"):
        fn = getattr(obj, name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                logger.debug(
                    "release_hardware: %s.%s() failed",
                    type(obj).__name__,
                    name,
                    exc_info=True,
                )
    for attr in ("las", "laser", "lowlvl", "device", "pm"):
        inner = getattr(obj, attr, None)
        if inner is not None and inner is not obj:
            release_hardware(inner, _seen)


def load_class(classpath, init_kwargs={}, settings=None):
    """Load a class by classpath string.

    Parameters
    ----------
    classpath : str
        The path in the package, e.g.
        'monet.attenuation.KinesisAttenuator'.
    init_kwargs : dict
        The first argument to __init__ of the class, being a dict.
    settings : dict
        Keyword arguments to __init__ of the class.
    """
    p, m = classpath.rsplit(".", 1)
    mod = import_module(p)
    Met = getattr(mod, m)
    if settings:
        met = Met(init_kwargs, **settings)
    else:
        met = Met(init_kwargs)
    return met


def update_mm_acquisition_comment(
    laser,
    measured,
    unit,
    att_pos=None,
    laser_pwr=None,
    raw_power=None,
    transmission=None,
):
    """Write a measured-power line into the MicroManager acquisition comment.

    Replaces an existing line for this laser so repeated measurements don't
    keep appending. Requires the optional `pycromanager` package and a running
    MicroManager instance; if `pycromanager` is not installed this is a no-op.

    Parameters
    ----------
    laser : int or str
        Laser wavelength, used to label the comment line.
    measured : float
        Measured power.
    unit : str
        Power unit string (e.g. 'mW').
    att_pos : float, optional
        Attenuator position to record alongside the power.
    laser_pwr : float, optional
        Laser power set-point to record alongside the power.
    raw_power : float, optional
        Raw power-meter reading before the objective transmission factor was
        applied. Only recorded if `transmission` differs from 1.
    transmission : float, optional
        Objective transmission factor (P_sample / P_bfp) applied to
        `raw_power` to obtain `measured`. A value of 1 or None means no
        factor was used, which is stated explicitly in the comment.

    Returns
    -------
    str or None
        None on success or if pycromanager is not installed; an error
        string if the update failed.
    """
    import re

    def _replace_or_append(text, pattern, new_str):
        result, count = re.subn(
            r"^" + re.escape(pattern) + r".*$",
            new_str,
            text,
            flags=re.MULTILINE,
        )
        if count == 0:
            result = (
                text
                + ("\n" if text and not text.endswith("\n") else "")
                + new_str
            )
        return result

    pwr_str = "Power {}nm: {:.3f} {}".format(laser, measured, unit)
    if att_pos is not None:
        pwr_str += " @ att={:.4f}".format(att_pos)
    if laser_pwr is not None:
        pwr_str += ' lp={:.1f}mW'.format(laser_pwr)
    if transmission is not None and transmission != 1:
        pwr_str += ' [BFP reading {:.3f} {} x T_obj={:.4f}]'.format(
            raw_power if raw_power is not None else measured / transmission,
            unit,
            transmission,
        )
    else:
        pwr_str += ' [no objective transmission factor]'
    pattern = 'Power {}nm:'.format(laser)

    try:
        from pycromanager import Studio

        studio = Studio()
        acqmgr = studio.acquisitions()
        curr_settings = acqmgr.get_acquisition_settings()
        curr_comment = str(curr_settings.comment() or "")
        new_comment = _replace_or_append(curr_comment, pattern, pwr_str)
        new_settings = (
            curr_settings.copy_builder().comment(new_comment).build()
        )
        acqmgr.set_acquisition_settings(new_settings)
        return None  # no error
    except ImportError:
        return None  # pycromanager not installed
    except Exception as exc:
        return str(exc)


def refresh_mm_gui():
    """Make MicroManager re-read the hardware state into its GUI.

    Device changes issued through the pycromanager Core (shutter, filter
    turret, nosepiece, ...) are not reflected in the MicroManager main
    window until its GUI is refreshed. No-op if pycromanager is not
    installed or MicroManager is not running.

    Returns
    -------
    str or None
        None on success or if pycromanager is not installed; an error
        string if the refresh failed.
    """
    try:
        from pycromanager import Studio

        Studio().app().refresh_gui()
        return None
    except ImportError:
        return None  # pycromanager not installed
    except Exception as exc:
        logger.debug('Could not refresh MicroManager GUI: %s', exc)
        return str(exc)
