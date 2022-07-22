#!/usr/bin/env python
"""
    monet/util.py
    ~~~~~~~~~~~~~

    Utility functions for the monet package

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
from importlib import import_module


def load_class(classpath, init_kwargs={}):
    """Load a class by classpath string

    Args:
        classpath : str
            the path in the package.
            E.g. 'monet.attenuation.KinesisAttenuator'
        init_kwargs : dict
            the arguments to __init__ of the class
    """
    p, m = classpath.rsplit('.', 1)
    mod = import_module(p)
    Met = getattr(mod, m)
    return Met(init_kwargs)
