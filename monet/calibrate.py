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
import matplotlib.pyplot as plt

from monet import LASER_TAG, POWER_TAG
from monet.util import load_class
import monet.io as io
from monet.control import IlluminationControl, IlluminationLaserControl
from monet.beampath import BeamPath


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


class CalibrationProtocol1D():
    """Calibrates the power of an instrument with one laser power input,
    varying the attenuator.

    Additional entries of the config, compared to IlluminationControl:
    'powermeter': {
        'classpath': 'monet.powermeter.TestPowerMeter',
        'init_kwargs': {
            'address': 'find connection',}
        },

    """
    def __init__(self, config, load_instrument=True):
        """Initialize the analyzer, powermeter, and attenuator classes
        defined in the configuration file.

        Args:
            config : dict
                keys: 'analysis', 'attenuation', 'powermeter'
                with sub-keys each: 'classpath', and 'init_kwargs'
            load_instrument : bool
                whether to load the instrument hardware. For inheriting
                classes this option should be disabled.
        """
        # load analysis and attenuation
        if load_instrument:
            self.instrument = IlluminationControl(config, do_load_cal=False)

        pwrconfig = config['powermeter']
        self.powermeter = load_class(
            pwrconfig['classpath'], pwrconfig['init_kwargs'])

    def calibrate(self, wait_time=0.1):
        """Calibrate power, with parameters according to the
        configuration file.
        """
        minval = self.instrument.config['analysis']['init_kwargs']['min']
        if np.isnan(minval):
            minval = 0
        maxval = self.instrument.config['analysis']['init_kwargs']['max']
        step = self.instrument.config['analysis']['init_kwargs']['step']

        # acquire power data
        control_par_vals = np.arange(minval, maxval+step, step)
        powers = np.zeros_like(control_par_vals, dtype=np.float64)
        for i, ctrlval in enumerate(control_par_vals):
            self.instrument.attenuator.set(ctrlval)
            time.sleep(wait_time)
            powers[i] = self.powermeter.read()
            print('Position: {:.1f}, Power: {:f}'.format(ctrlval, powers[i]))


        # analyze
        self.instrument.analyzer.fit(control_par_vals, powers)
        # print(self.instrument.analyzer.fit_result.fit_report())
        self.instrument.is_calibrated = True

        self.save_calibration()

    def save_calibration(self, save_plot=True):
        """Save the calibration to the database
        """
        cali_pars = self.instrument.analyzer.get_model()

        fname = self.instrument.config['database']
        # print('saving calibration into index', self.instrument.config['index'])
        # print('calibration pars: ', cali_pars)
        indexnames, indexvals = io.save_calibration(
            fname, self.instrument.config['index'], cali_pars)

        if save_plot:
            folder = self.instrument.config.get('dest_calibration_plot')
            if folder is None:
                folder = os.path.split(fname)[0]
            fnplot = os.path.join(
                folder, '_'.join(
                    [str(k)+'-'+str(v)
                     for k, v in zip(indexnames, indexvals)]) + '.png')
            # colons are allowed in second position
            fnplot = fnplot[:2] + fnplot[2:].replace(':', '-')
            fnplot = fnplot.replace('[', '(')
            fnplot = fnplot.replace(']', ')')
            self.instrument.analyzer.plot(
                fnplot,
                ylabel='Power [{:s}]'.format(self.powermeter.unit),
                title='power calibration curve')


class CalibrationProtocol2D(CalibrationProtocol1D):
    """Calibrates different lasers at different power settings
    """
    def __init__(self, config, protocol):
        """
        Args:
            config : dict
                the configuration, with entries the union of those necessary
                for CalibrationProtocol1D and IlluminationLaserControl
            protocol : dict
                keys:
                    required: 'laser_sequence', 'laser_powers',
                    optional: 'beapath'
                values:
                    'laser_sequence': list of lasers matching 'laser' keys
                        in config
                    'laser_powers': dict of laser keys and
                        list of respective laser powers
                    'beampath': dict of laser keys and dict of respective
                        beampath object settings for object ids as set in
                        'beampath' section of config
        """
        self.protocol = protocol

        self.instrument = IlluminationLaserControl(config, do_load_cal=False)

        if 'beampath' in config.keys() and 'beampath' in protocol.keys():
            self.beampath = BeamPath(config['beampath'])
            self.use_beampath = True
        else:
            self.use_beampath = False

        super().__init__(config, load_instrument=False)

    def run_protocol(self, wait_time=0):
        """Run a protocol: loop through lasers and respective power settings,
        doing calibrations, and saving them for every combination.
        """
        # delete previous calibration plots
        plotfolder = self.instrument.config.get('dest_calibration_plot')
        for f in os.listdir(plotfolder):
            try:
                os.remove(os.path.join(plotfolder, f))
            except:
                pass
        # now start calibration
        for laser in self.protocol['laser_sequence']:
            print('switching to laser', laser)
            self.instrument.laser = laser
            laserpowers = self.protocol['laser_powers'][laser]
            if self.use_beampath:
                self.beampath.positions = self.protocol['beampath'][laser]
            modelpars = pd.DataFrame(index=laserpowers)
            # set powermeter setting
            self.powermeter.wavelength = int(laser)
            # self.instrument.config['index'][LASER_TAG] = laser
            for lpwr in laserpowers:
                print('setting laser power to', lpwr, 'mW')
                self.instrument.laserpower = lpwr

                if 'amp' in self.powermeter.config.keys():
                    # this is a test powermeter. set amplitude
                    self.powermeter.config['amp'] = lpwr

                self.calibrate(wait_time=wait_time)
                self.save_calibration()

                # get model parameters for plotting
                model_dict = self.instrument.analyzer.get_model()
                for k, v in model_dict.items():
                    modelpars.loc[lpwr, k] = v
                # calibration state is always set True in each 1D calibration
                self.instrument.is_calibrated = False

            self.instrument.laserpower = min(laserpowers)
            self.instrument.laser_enabled = False
            self.plot_model(modelpars, laser)
        # post-actions
        if self.use_beampath and 'end' in self.protocol['beampath'].keys():
            self.beampath.positions = self.protocol['beampath']['end']
        # self.instrument.is_calibrated = True
        # self.instrument.load_calibration_database()

    def plot_model(self, modeldf, laser):
        fig, ax = plt.subplots(nrows=len(modeldf.columns), sharex=True, squeeze=False)
        for i, col in enumerate(modeldf.columns):
            ax[i, 0].plot(modeldf.index.to_numpy(), modeldf[col].to_numpy(),
                          marker='x')
            ax[i, 0].set_ylabel(str(col))
        ax[-1, 0].set_xlabel('laser power [mW]')
        fig.suptitle('laser {:d} nm'.format(int(laser)))

        fname = self.instrument.config['database']
        folder = self.instrument.config.get('dest_calibration_plot')
        if folder is None:
            folder = os.path.split(fname)[0]
        fnplot = os.path.join(
            folder, '{:d}nm'.format(int(laser)) + '.png')
        fig.savefig(fnplot)
        plt.close(fig)
