#!/usr/bin/env python
"""
    monet/calibrate.py
    ~~~~~~~~~~~~~~~~~~

    Here, the calibration is performed. This orchestrates attenuation,
    power measurement and analysis.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import logging
from icecream import ic
import os

import time
from datetime import datetime
import numpy as np
import pandas as pd
from importlib import import_module

import monet.io as io


logger = logging.getLogger(__name__)
# ic.configureOutput(outputFunction=logger.debug)


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


class PowerCalibrator():
    def __init__(self, config, do_load_cal=True):
        """Initialize the analyzer, powermeter, and attenuator classes
        defined in the configuration file.

        Args:
            config : dict
                keys: 'analysis', 'attenuation', 'powermeter'
                with sub-keys each: 'classpath', and 'init_kwargs'
            do_load_cal : bool
                whether or not to load the latest calibration
        """
        self.is_calibrated = False

        self.config = config
        anaconfig = config['analysis']
        self.analyzer = load_class(
            anaconfig['classpath'], anaconfig['init_kwargs'])

        attconfig = config['attenuation']
        self.attenuator = load_class(
            attconfig['classpath'], attconfig['init_kwargs'])

        pwrconfig = config['powermeter']
        self.powermeter = load_class(
            pwrconfig['classpath'], pwrconfig['init_kwargs'])

        if do_load_cal:
            self.load_calibration()

    def calibrate(self, wait_time=0.1):
        """Calibrate power, with parameters according to the
        configuration file.
        """
        minval = self.config['analysis']['init_kwargs']['min']
        maxval = self.config['analysis']['init_kwargs']['max']
        step = self.config['analysis']['init_kwargs']['step']

        # acquire power data
        control_par_vals = np.arange(minval, maxval+step, step)
        powers = np.zeros_like(control_par_vals, dtype=np.float64)
        for i, ctrlval in enumerate(control_par_vals):
            self.attenuator.set(ctrlval)
            time.sleep(wait_time)
            powers[i] = self.powermeter.read()
            print('Position: {:.1f}, Power: {:f}'.format(ctrlval, powers[i]))


        # analyze
        self.analyzer.fit(control_par_vals, powers)
        print(self.analyzer.fit_result.fit_report())
        self.is_calibrated = True

        self.save_calibration()

    def set_power(self, power):
        """Set a power level once the power has been calibrated.

        Args:
            power : float
                the power to set in mW
        """
        if not self.is_calibrated:
            raise ValueError('No calibration present. Please calibrate first.')
        ctrlval = self.analyzer.estimate(power)
        self.attenuator.set(ctrlval)

    def save_calibration(self, save_plot=True):
        """Save the calibration to the database
        """
        cali_pars = self.analyzer.get_model()

        fname = self.config['database']
        io.save_calibration(fname, self.config['index'], cali_pars)

        if save_plot:
            folder = self.config.get('dest_calibration_plot')
            if folder is None:
                folder = os.path.split(fname)[0]
            fnplot = os.path.join(
                folder, '_'.join(
                    [str(k)+'-'+str(v)
                     for k, v in zip(indexnames, indexvals)]) + '.png')
            fnplot = fnplot.replace(':', '-')
            fnplot = fnplot.replace('[', '(')
            fnplot = fnplot.replace(']', ')')
            self.analyzer.plot(
                fnplot,
                ylabel='Power [{:s}]'.format(self.powermeter.unit),
                title='power calibration curve')

    def load_calibration(self, time_idx='latest'):
        """Load a calibration from the database, and set the analyzer
        model accordingly

        Args:
            idx : None, 'latest', or list, len 2
                loads either the latest (if idx is None or a string)
                or a specific date and time
        """
        fname = self.config['database']
        cali_pars = io.load_calibration(
            fname, self.config['index'], time_idx=time_idx)

        self.analyzer.load_model(cali_pars)
        self.is_calibrated = True


class CalibrationProtocol2D:
    """Calibrates different lasers at different power settings
    """
    def __init__(self, config, protocol):
        """
        Args:
            config : dict
                the configuration, with keys
                    'analysis', 'attenuation', 'powermeter'
                        dicts with sub-keys: 'classpath', and 'init_kwargs'
                    'lasers'
                        dict with keys: laser name (e.g. '561') and vals:
                        dict with sub-keys 'classpath', and 'init_kwargs'
            protocol : dict
                keys: laser names
                vals: list of power values
        """
        self.protocol = protocol
        self.config = config

        self.calibrator = PowerCalibrator(config)
        self.lasers = {}
        for laser, lconf in config['lasers'].items():
            self.lasers[laser] = load_class(
                    lconf['classpath'], lconf['init_kwargs'])
            self.lasers[laser].enabled = False

    def run_protocol(self):
        """Run a protocol: loop through lasers and respective power settings,
        doing calibrations, and saving them for every combination.
        """
        for laser, powers in self.protocol.items():
            self.lasers[laser].enabled=True
            self.calibrator.config['index'][LASER_TAG] = laser
            for pwr in powers:
                self.lasers[laser].power = pwr
                self.calibrator.config['index'][POWER_TAG] = pwr
                self.calibrator.calibrate()
                self.calibrator.save_calibration()
            self.lasers[laser].power = min(powers)
            self.lasers[laser].enabled = False
