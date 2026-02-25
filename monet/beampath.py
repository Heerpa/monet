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
# import pymmcore

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
        # logger.debug('Pycromanager Core already initialized. Returning.')
        return pycrocore

    if pycore_config is None:
        try:
            pycrocore = Core()
        except TimeoutError as e:
            raise e
    else:
        # no need to specifically load the config
        logger.debug('Ignoring pycromanager configuration {:s}.'.format(str(pycore_config)))
        try:
            pycrocore = Core()
        except TimeoutError as e:
            raise e
        # raise NotImplementedError('Loading pycromanager from pymmcore is not implemented.')
        # pycrocore = pymmcore.CMMCore()
        # pycrocore.setDeviceAdapterSearchPaths(
        #     [pycore_config['micromanager_path']])
        # pycrocore.loadSystemConfiguration(
        #     os.path.join(pycore_config['micromanager_path'],
        #                  pycore_config['mmconfig_name']))

        # logger.debug(pycrocore.getAvailablePropertyBlocks())
        # logger.debug(pycrogore.getChannelGroup())
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
        self._position = 0
        self._autoshutter = True
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
        self._autoshutter = True

    @property
    def autoshutter(self):
        """Get the whether shutter is on autoshutter
        """
        return self._autoshutter

    @autoshutter.setter
    def autoshutter(self, pos):
        """Set the autoshutter state"""
        self._autoshutter = pos

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
        self._connect(config)

    def _connect(self, config):
        self.core = get_pycromgr()
        self.core.set_property('Core', 'AutoShutter', '0')

    @property
    def autoshutter(self):
        return self.core.get_property('Core', 'AutoShutter')

    @autoshutter.setter
    def autoshutter(self, val):
        if val:
            val = '1'
        else:
            val = '0'
        self.core.set_property('Core', 'AutoShutter', val)

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
        self.core.set_shutter_open(pos)
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
        self._connect(config)

    def _connect(self, config):
        self.core = get_pycromgr()
        # find the correct filter config name
        filter_config_name = 'Filter turret'
        cfg_groups = self.core.get_available_config_groups()
        config_names = [
            cfg_groups.get(i)
            for i in range(cfg_groups.size())]
        if filter_config_name not in config_names:
            config_names_upper = [it.upper() for it in config_names]
            if filter_config_name.upper() in config_names_upper:
                filter_config_name = config_names[
                    config_names_upper.index(filter_config_name.upper())]
            else:
                # try the parts
                name_candidates = []
                for test_cn in filter_config_name.split(' '):
                    found = [test_cn.upper() in cn for cn in config_names_upper]
                    if sum(found) > 0:
                        name_candidates.append(config_names[found.index(True)])
                if len(name_candidates) == 1:
                    filter_config_name = name_candidates[0]
                elif len(name_candidates) > 1:
                    logger.debug(
                        'Multiple configs could be the ' + filter_config_name +
                        ': ' + ', '.join(name_candidates) + '. Choosing the first.')
                    filter_config_name = name_candidates[0]
                else:
                    raise KeyError(
                        'Cannot find configuration for ' + filter_config_name +
                        '.')
        self.filter_config_name = filter_config_name
        # load the options
        configopts = self.core.get_available_configs(filter_config_name)
        self.filter_options = [configopts.get(i) for i in range(configopts.size())]

    @property
    def position(self):
        curr_pos = self.core.get_current_config(self.filter_config_name)
        return curr_pos
        return super().position

    @position.setter
    def position(self, pos):
        assert(isinstance(pos, str))
        assert(pos in self.filter_options)
        self.core.set_config(self.filter_config_name, pos)

        super(self.__class__, self.__class__).position.__set__(self, pos)
