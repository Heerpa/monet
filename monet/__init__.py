#!/usr/bin/env python
"""
    monet/__init__.py
    ~~~~~~~~~~~~~~~~~

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import os.path as _ospath
import yaml as _yaml

import logging
from logging import handlers


# configure logger and log that this shouldn't be done here
def config_logger():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s | %(name)s | %(levelname)s -> %(message)s')
    file_handler = handlers.RotatingFileHandler(
        'monet.log', maxBytes=1e6, backupCount=5)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.WARNING)
    logger.addHandler(file_handler)
    # logger.addHandler(stream_handler)

config_logger()

DEVICE_TAG = 'name'
LASER_TAG = 'wavelength [nm]'
POWER_TAG = 'laser_power [mW]'
DATABASE_INDEXLEVELS = [
    DEVICE_TAG, LASER_TAG, POWER_TAG, 'date', 'time'
]


###########################################################
#
# Example configurations and protocols are defined in the
# following section.
#
###########################################################

default_config = {
    'database': '../power_database.xlsx',
    'index': {
        'name': 'DefaultMicroscope',
        LASER_TAG: 488,
        POWER_TAG: 100},
    'powermeter': {
        'classpath': 'monet.powermeter.ThorlabsPowerMeter',
        'init_kwargs': {
            'address': 'find connection',}
        },
    'attenuation' : {
        'classpath': 'monet.attenuation.KinesisAttenuator',
        'init_kwargs': {
            'serial': '27257033',},},
    'analysis': {
        'classpath': 'monet.analysis.SinusAttenuationCurveAnalyzer',
        'init_kwargs': {
            'min': 40,
            'max': 100,
            'step': 5,}
        }
}


test_config = {
    'database': 'power_database.xlsx',
    'index': {
        'name': 'DefaultMicroscope',
        LASER_TAG: 488,
        POWER_TAG: 100},
    'powermeter': {
        'classpath': 'monet.powermeter.TestPowerMeter',
        'init_kwargs': {
            'address': 'find connection',}
        },
    'attenuation' : {
        'classpath': 'monet.attenuation.TestAttenuator',
        'init_kwargs': {
            'bkg': 0,
            'amp': 50,
            'phi': 30,
            'start': 10,
            'step': 5},},
    'analysis': {
        'classpath': 'monet.analysis.SinusAttenuationCurveAnalyzer',
        'init_kwargs': {
            'min': 30,
            'max': 100,
            'step': 5,}
        }
}

calibration_protocol = {
    488: [100, 200, 500, 1000],
    561: [200, 500, 1000, 2000],
    640: [200, 500, 1000, 2000],
}

test_config_2d = {
    'database': 'power_database.xlsx',
    'dest_calibration_plot': './',
    'index': {
        'name': 'DefaultMicroscope',
        },
    'powermeter': {
        'classpath': 'monet.powermeter.TestPowerMeter',
        'init_kwargs': {
            'address': 'find connection',}
        },
    'attenuation' : {
        'classpath': 'monet.attenuation.TestAttenuator',
        'init_kwargs': {
            'bkg': 0,
            'amp': 50,
            'phi': 30,
            'start': 10,
            'step': 5},
        'analysis': {
            'classpath': 'monet.analysis.SinusAttenuationCurveAnalyzer',
            'init_kwargs': {
                'min': 30,
                'max': 100,
                'step': 5,}
            },
        },
    'lasers' : {
        488: {
            'classpath': 'monet.laser.Toptica',
            'init_kwargs': {'port': 'COM4'},
            },
        561: {
            'classpath': 'monet.laser.MPBVFL',
            'init_kwargs': {'port': 'COM7'},
            },
        640: {
            'classpath': 'monet.laser.MPBVFL',
            'init_kwargs': {'port': 'COM8'},
            },
        },}

###########################################################
#
# Configs and protocols used by default in the interactive
# command line mode are loaded from default file in the
# following. If this is not possible, the example and test
# protocols defined above are used.
#
###########################################################

default_config_paths = [
    'Z:/users/grabmayr/test_powerbase/configs.yaml']
default_protocol_paths = [
    'Z:/users/grabmayr/test_powerbase/protocols.yaml']


CONFIGS = {}
CONFIGS_PATH = ''

# load configs from file
for defpath in default_config_paths:
    try:
        with open(defpath, 'r') as configs_file:
            CONFIGS = _yaml.full_load(configs_file)
        if CONFIGS is not None:
            print('Loaded configurations from ' + defpath)
            CONFIGS_PATH = defpath
            break
    except:
        pass
if CONFIGS =={}:
    CONFIGS = {
        'default': default_config,
        'test': test_config,
        'test_2D': test_config_2d}


# load protocols from file
for defpath in default_protocol_paths:
    try:
        with open(defpath, 'r') as protocols_file:
            PROTOCOLS = _yaml.full_load(protocols_file)
        if PROTOCOLS is not None:
            print('Loaded protocols from ' + defpath)
            PROTOCOLS_PATH = defpath
            break
    except:
        pass
if PROTOCOLS =={}:
    PROTOCOLS = {
        'test_2D': calibration_protocol}
