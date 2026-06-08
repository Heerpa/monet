#!/usr/bin/env python
"""
monet/util.py
~~~~~~~~~~~~~

Utility functions for the monet package

:authors: Heinrich Grabmayr, 2022
:copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""

from importlib import import_module


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
    p, m = classpath.rsplit('.', 1)
    mod = import_module(p)
    Met = getattr(mod, m)
    if settings:
        met = Met(init_kwargs, **settings)
    else:
        met = Met(init_kwargs)
    return met


def update_mm_acquisition_comment(
    laser, measured, unit, att_pos=None, laser_pwr=None
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

    Returns
    -------
    str or None
        None on success or if pycromanager is not installed; an error
        string if the update failed.
    """
    import re

    def _replace_or_append(text, pattern, new_str):
        result, count = re.subn(
            r'^' + re.escape(pattern) + r'.*$',
            new_str,
            text,
            flags=re.MULTILINE,
        )
        if count == 0:
            result = (
                text
                + ('\n' if text and not text.endswith('\n') else '')
                + new_str
            )
        return result

    pwr_str = 'Power {}nm: {:.3f} {}'.format(laser, measured, unit)
    if att_pos is not None:
        pwr_str += ' @ att={:.4f}'.format(att_pos)
    if laser_pwr is not None:
        pwr_str += ' lp={:.1f}mW'.format(laser_pwr)
    pattern = 'Power {}nm:'.format(laser)

    try:
        from pycromanager import Studio

        studio = Studio()
        acqmgr = studio.acquisitions()
        curr_settings = acqmgr.get_acquisition_settings()
        curr_comment = str(curr_settings.comment() or '')
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
