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
import shutil

import time
from datetime import datetime
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from monet import LASER_TAG, POWER_TAG, DEVICE_TAG
from monet.util import load_class
import monet.io as io
from monet.control import IlluminationControl, IlluminationLaserControl


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
        try:
            self.powermeter = load_class(
                pwrconfig['classpath'], pwrconfig['init_kwargs'])
            self.powermeter_available = True
        except Exception as exc:
            logger.warning('PowerMeter not available: %s', exc)
            self.powermeter = None
            self.powermeter_available = False

    def calibrate(self, wait_time=0.1, dry_run=False, powermeter_type='manual'):
        """Calibrate power, with parameters according to the
        configuration file.

        Args:
            wait_time : float
                time to wait between attenuator steps [s]
            dry_run : bool
                if True, calibration is performed but not saved to the database
            powermeter_type : str
                'manual' or 'beampath' — annotated in the database

        Returns:
            control_par_vals : 1D np array
                the control values (e.g. angles)
            powers : 1D np array
                the measured power
        """
        minval = self.instrument.config['analysis']['init_kwargs']['min']
        if np.isnan(minval):
            minval = 0
        maxval = self.instrument.config['analysis']['init_kwargs']['max']
        step = self.instrument.config['analysis']['init_kwargs']['step']

        # acquire power data
        control_par_vals = np.arange(minval, maxval + step, step)
        powers = np.zeros_like(control_par_vals, dtype=np.float64)
        for i, ctrlval in enumerate(control_par_vals):
            self.instrument.attenuator.set(ctrlval)
            time.sleep(wait_time)
            powers[i] = self.powermeter.read()
            # print('Position: {:.1f}, Power: {:f}'.format(ctrlval, powers[i]))

        # analyze
        self.instrument.analyzer.fit(control_par_vals, powers)
        # print(self.instrument.analyzer.fit_result.fit_report())
        self.instrument.is_calibrated = True

        self.save_calibration(dry_run=dry_run, powermeter_type=powermeter_type)

        return control_par_vals, powers

    def save_calibration(self, save_plot=True, dry_run=False, powermeter_type='manual'):
        """Save the calibration to the database

        Args:
            save_plot : bool
                whether to save a plot of the calibration
            dry_run : bool
                if True, skip writing to the database
            powermeter_type : str
                'manual' or 'beampath' — stored as a column in the database
        """
        cali_pars = self.instrument.analyzer.get_model()
        cali_pars['powermeter_type'] = powermeter_type

        fname = self.instrument.config['database']
        # print('saving calibration into index', self.instrument.config['index'])
        # print('calibration pars: ', cali_pars)
        if not dry_run:
            indexnames, indexvals = io.save_calibration(
                fname, self.instrument.config['index'], cali_pars)
        else:
            indexnames = (list(self.instrument.config['index'].keys())
                          + ['date', 'time'])
            indexvals = tuple(
                list(self.instrument.config['index'].values()) + ['dry', 'run'])

        if save_plot:
            folder = self.instrument.config.get('dest_calibration_plot')
            if folder is None:
                folder = os.path.split(fname)[0]
            fnplot = os.path.join(
                folder, '_'.join(
                    [str(k) + '-' + str(v)
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

        # if not all lasers are present
        lasers_present = list(self.instrument.lasers.keys())
        self.protocol['laser_sequence'] = [
            it for it in self.protocol['laser_sequence']
            if it in lasers_present]
        self.protocol['laser_powers'] = {
            k: v for k, v in self.protocol['laser_powers'].items()
            if k in lasers_present}
        self.protocol['beampath'] = {
            k: v for k, v in self.protocol['beampath'].items()
            if k in lasers_present or k=='end' or k=='start_calibrate' or k=='end_calibrate'}

        super().__init__(config, load_instrument=False)

    def run_protocol(self, wait_time=0, switch_time=10,
                     laser_filter=None, dry_run=False,
                     progress_callback=None, manage_laser_state=True,
                     powermeter_type='manual'):
        """Run a protocol: loop through lasers and respective power settings,
        doing calibrations, and saving them for every combination.

        Args:
            wait_time : float
                time to wait between attenuator steps [s]
            switch_time : float
                time to wait after switching laser [s]
            laser_filter : list or None
                if not None, only calibrate lasers in this list
            dry_run : bool
                if True, calibration is performed but not saved to the database
            progress_callback : callable or None
                called after each power step with (step, total, laser, lpwr)
            manage_laser_state : bool
                if True (CLI mode), switch off all lasers at start and after
                each wavelength. If False (GUI mode), leave laser state as-is.
            powermeter_type : str
                'manual' or 'beampath' — annotated in every saved calibration
        """
        plotfolder = self.instrument.config.get('dest_calibration_plot')

        lasers = [l for l in self.protocol['laser_sequence']
                  if laser_filter is None or l in laser_filter]

        # delete only plots belonging to the lasers being calibrated
        # calibration-curve files accumulate across runs (timestamp in name) and
        # must be removed; model/meas plots are overwritten but pruned for tidiness
        laser_ints = {int(l) for l in lasers}
        for fname in os.listdir(plotfolder):
            matched = any(
                fname in (
                    '{:d}nm.png'.format(li),
                    'pwrmeasured_{:d}nm.png'.format(li),
                    'pwrmeasured_{:d}nm.xlsx'.format(li),
                ) or 'wavelength (nm)-{:d}_'.format(li) in fname
                for li in laser_ints
            )
            if matched:
                try:
                    os.remove(os.path.join(plotfolder, fname))
                except Exception:
                    pass
        total = sum(len(self.protocol['laser_powers'][l]) for l in lasers)
        step = 0

        if manage_laser_state:
            # switch off all lasers
            for laser in self.protocol['laser_sequence']:
                self.instrument.laser = laser
                self.instrument.laser_enabled = False

        # now start calibration
        for laser in lasers:
            print('switching to laser', laser)
            self.instrument.laser = laser
            self.instrument.laser_enabled = True
            laserpowers = self.protocol['laser_powers'][laser]
            if self.instrument.use_beampath:
                self.instrument.beampath.positions = self.protocol['beampath'][laser]
                if powermeter_type == 'beampath':
                    start_cal_pos = self.protocol['beampath'].get('start_calibrate')
                    if start_cal_pos:
                        self.instrument.beampath.positions = start_cal_pos
            self.instrument.attenuator.set_wavelength(laser)
            modelpars = pd.DataFrame(index=laserpowers)
            measpwrs = pd.DataFrame(columns=laserpowers)
            # set powermeter setting
            self.powermeter.wavelength = int(laser)
            # self.instrument.config['index'][LASER_TAG] = laser
            time.sleep(switch_time)
            for lpwr in laserpowers:
                print('setting laser power to', lpwr, 'mW')
                self.instrument.laserpower = lpwr

                if 'amp' in self.powermeter.config.keys():
                    # this is a test powermeter. set amplitude
                    self.powermeter.config['amp'] = lpwr

                angles, powers = self.calibrate(wait_time=wait_time,
                                                dry_run=dry_run,
                                                powermeter_type=powermeter_type)
                for an, pw in zip(angles, powers):
                    measpwrs.loc[an, lpwr] = pw

                # get model parameters for plotting
                model_dict = self.instrument.analyzer.get_model()
                for k, v in model_dict.items():
                    modelpars.loc[lpwr, k] = v
                # calibration state is always set True in each 1D calibration
                self.instrument.is_calibrated = False

                step += 1
                if progress_callback:
                    progress_callback(step, total, laser, lpwr)

            self.instrument.laserpower = min(laserpowers)
            if manage_laser_state:
                self.instrument.laser_enabled = False
            self.plot_model(modelpars, laser)
            self.save_measvals(measpwrs, laser)
            if not dry_run:
                io.compute_and_save_factor(
                    self.instrument.config['database'],
                    self.instrument.config['index'][DEVICE_TAG],
                    laser,
                    self.instrument.config['analysis'])
        self.plot_device_history()
        # post-actions
        # move beampath to end_calibrate position
        if end_pos := self.protocol['beampath'].get('end_calibrate'):
            self.instrument.beampath.positions = end_pos
        # move beampath to general end position (which is also used for shutdowbn)
        if self.instrument.use_beampath and 'end' in self.protocol['beampath'].keys():
            self.instrument.beampath.positions = self.protocol['beampath']['end']
        # self.instrument.is_calibrated = True
        # self.instrument.load_calibration_database()

        # copy all plots from local folder onto the server (file-based DB only)
        if not io._is_server_url(self.instrument.config['database']):
            device = self.instrument.config['index'][DEVICE_TAG]
            sfolder = os.path.join(
                os.path.split(self.instrument.config['database'])[0],
                'Calibrations',
                datetime.now().strftime('%y%m%d-%H%M') + '_' + device)
            try:
                os.makedirs(sfolder)
            except:
                pass
            lfolder = self.instrument.config.get('dest_calibration_plot')
            shutil.copytree(lfolder, sfolder)

    def plot_model(self, modeldf, laser):
        plt.switch_backend('agg')
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
            folder = os.getcwd() if io._is_server_url(fname) else os.path.split(fname)[0]
        fnplot = os.path.join(
            folder, '{:d}nm'.format(int(laser)) + '.png')
        fig.savefig(fnplot)
        plt.close(fig)

    def save_measvals(self, measdf, laser):
        """Save measured values as excel sheet and png
        """
        fname = self.instrument.config['database']
        folder = self.instrument.config.get('dest_calibration_plot')
        if folder is None:
            folder = os.getcwd() if io._is_server_url(fname) else os.path.split(fname)[0]
        fnplot = os.path.join(
            folder, 'pwrmeasured_{:d}nm'.format(int(laser)) + '.xlsx')
        measdf.to_excel(fnplot)

        plt.switch_backend('agg')
        fig, ax = plt.subplots()
        ax.xaxis.set_visible(False)
        ax.yaxis.set_visible(False)
        ax.axis('off')
        tab = pd.plotting.table(ax, measdf, loc='center')
        for c in tab.get_celld().values():
            c.visible_edges = 'horizontal'
        fig.tight_layout()
        ax.set_title('measured powers in mW')
        fnplot = os.path.join(
            folder, 'pwrmeasured_{:d}nm'.format(int(laser)) + '.png')
        fig.tight_layout()
        plt.savefig(fnplot)
        plt.close(fig)

    def plot_device_history(self):
        """Plot the historic evolution of model parameters
        """
        plt.switch_backend('agg')
        device = self.instrument.config['index'][DEVICE_TAG]
        plot_dir = self.instrument.config.get('dest_calibration_plot')
        db_fname = self.instrument.config['database']
        io.plot_device_history(db_fname, device, plot_dir)
        anaconfig = self.instrument.config['analysis']
        analyzer = load_class(
                anaconfig['classpath'], anaconfig['init_kwargs'])
        io.plot_device_amplitude_history(db_fname, device, plot_dir, analyzer)
