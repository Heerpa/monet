#!/usr/bin/env python
"""
    monet/beampath.py
    ~~~~~~~~~~~~~~~~~

    This module provides functionality to control things in the beam path
    other than those which are central to the functionality of monet. E.g.
    opening shutters or positioning dichroics.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import logging
from icecream import ic
import os
import abc

from pycromanager import Core
import pymmcore

import time
import numpy as np
import pandas as pd

from monet.util import load_class


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)

pycrocore = None
# or load specific config here: https://github.com/micro-manager/pymmcore/

def get_pycromgr(pycore_config=None):
    """Initialize the pycromanager core, either using a saved configuration,
    or the default.
    Args:
        pycrocore_config : None or dict
            if dict, with keys: 'micromanager_path', 'mmconfig_name'

    Returns:
        pycrocore : pycromanger.Core
            the global pycromanger core instance
    """
    global pycrocore
    if pycrocore is not None:
        logger.debug('Pycromanager Core already initialized. Returning.')
        return pycrocore

    if pycore_config is None:
        pycrocore = Core.core()
        logger.warning('TODO: Check pycromanger.Core.core() does the same as pymmcore.CMMCore()')
    else:
        pycrocore = pymmcore.CMMCore()
        pycrocore.setDeviceAdapterSearchPaths(
            [pycore_config['micromanager_path']])
        pycrocore.loadSystemConfiguration(
            os.path.join(pycore_config['micromanager_path'],
                         pycore_config['mmconfig_name']))

        logger.debug(pycrocore.getAvailablePropertyBlocks())
        logger.debug(pycrogore.getChannelGroup())
    return pycrocore

class BeamPath():
    """A class holding all objects in the beam path that can be opened
    or put into a correct position.
    Example config:
    {
        'DC': {
            'classpath': 'monet.beampath.NikonFilterWheel',
            init_kwargs: {'SN': 1234}},
        'shutter': {
            'classpath': 'monet.beampath.NikonShutter',
            init_kwargs: {'SN': 123456}},
    """
    def __init__(self, config, pycore_config=None):
        """
        Args:
            config : dict
                keys: BeamPathObject identifier, as used in protocol
            pycore_config : dict
                'micromanager_path', 'mmconfig_name'
        """
        get_pycromgr(pycore_config)
        self.objects = {
            obid: load_class(cfg['classpath'], cfg['init_kwargs'])
            for obid, cfg in config.items()
        }

    @property
    def positions(self):
        """Query the positions of the beam path objects.
        Returns:
            positions : dict
                keys object its as in self.objects
        """
        return {obid: obj.position for obid, obj in self.objects.items()}

    @positions.setter
    def positions(self, positions):
        """Set the position of beam path objects.
        Args:
            positions : dict
                keys: object ids as in self.objects
                values: position values compatible with the respective object.
        """
        for obid, pos in positions.items():
            self.objects[obid].position = pos


class AbstractBeamPathObject(abc.ABC):
    """The prototypic beam path object, with standard methods.
    """
    _position = None
    def __init__(self, config):
        pass

    @property
    @abc.abstractmethod
    def position(self):
        """Get the position of the beam path object
        """
        return self._position

    @position.setter
    @abc.abstractmethod
    def position(self, pos):
        """Set the position of the beam path object"""
        self._position = pos


class TestShutter(AbstractBeamPathObject):
    """Implments a test shutter.
    """
    def __init__(self, config):
        """
        Args:
            config : dict
                the configuration of the shutter. Keys
                'SN': serial number
                ...
        """
        super().__init__(config)
        logger.debug('initializing TestShutter')
        self.device = self._connect(config)

    def _connect(self, config):
        device = None
        logger.debug('connecting to TestShutter')
        return device

    @property
    def position(self):
        logger.debug('querying position of TestShutter.')
        return super().position

    @position.setter
    def position(self, pos):
        assert(isinstance(pos, bool))
        logger.debug('setting position of TestShutter to {:b}'.format(pos))
        super(self.__class__, self.__class__).position.__set__(self, pos)


class NikonShutter(AbstractBeamPathObject):
    """Implments the shutter of a Nikon Ti2 Microscope.
    """
    def __init__(self, config):
        """
        Args:
            config : dict
                the configuration of the shutter. Keys
                'SN': serial number
                ...
        """
        super().__init__(config)
        self.device = self._connect(config)

    def _connect(self, config):
        device = None
        core = get_pycromgr()
        core.set_property('Core', 'AutoShutter', 0)
        return device

    @property
    def position(self):
        return super().position

    @position.setter
    def position(self, pos):
        assert(isinstance(pos, bool))
        # if pos:
        #     self.device.open()
        #     # core.setShutterOpen(True)
        # else:
        #     self.device.close()
        # core.set_property('Core', 'ShutterOpen', pos)
        core = get_pycromgr()
        core.set_shutter_open(pos)
        super(self.__class__, self.__class__).position.__set__(self, pos)


class NikonFilterWheel(AbstractBeamPathObject):
    """Implments the filter wheel of a Nikon Ti2 Microscope.
    """
    def __init__(self, config):
        """
        Args:
            config : dict
                the configuration of the filter wheel. Keys:
                'SN': serial number
                ...
        """
        super().__init__(config)
        self.device = self._connect(config)

    def _connect(self, config):
        device = None
        return device

    @property
    def position(self):
        return super().position

    @position.setter
    def position(self, pos):
        assert(isinstance(pos, str))
        self.device.set_pos(pos)
        super(self.__class__, self.__class__).position.__set__(self, pos)
