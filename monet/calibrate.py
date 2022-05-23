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



logger = logging.getLogger(__name__)
# ic.configureOutput(outputFunction=logger.debug)


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
        self.analyzer = self.load_class(
            anaconfig['classpath'], anaconfig['init_kwargs'])

        attconfig = config['attenuation']
        self.attenuator = self.load_class(
            attconfig['classpath'], attconfig['init_kwargs'])

        pwrconfig = config['powermeter']
        self.powermeter = self.load_class(
            pwrconfig['classpath'], pwrconfig['init_kwargs'])

        if do_load_cal:
            self.load_calibration()

    def load_class(self, classpath, init_kwargs={}):
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
        indexnames = list(self.config['index'].keys()) + ['date', 'time']
        datim = [datetime.now().strftime('%Y-%m-%d'),
                 datetime.now().strftime('%H:%M')]
        indexvals = tuple(list(self.config['index'].values()) + datim)
        try:
            db = pd.read_excel(fname, index_col=list(range(len(indexvals))))
        except Exception as e:
            logger.debug('error loading database: ', str(e))
            # print('error loading database: ', str(e))
            midx = pd.MultiIndex.from_tuples(
                [indexvals], names=list(indexnames))
            db = pd.DataFrame(
                index=midx, columns=list(cali_pars.keys()))

        db.loc[indexvals, :] = list(cali_pars.values())
        db.to_excel(fname)

        if save_plot:
            fnplot = os.path.join(
                os.path.split(fname)[0],
                '_'.join([str(k)+'-'+str(v)
                    for k, v in zip(indexnames, indexvals)]) + '.png')
            fnplot = fnplot.replace(':', '-')
            fnplot = fnplot.replace('[', '(')
            fnplot = fnplot.replace(']', ')')
            self.analyzer.plot(
                fnplot,
                ylabel='Power [{:s}]'.format(self.powermeter.unit),
                title='power calibration curve')

    def load_calibration(self, idx='latest'):
        """Load a calibration from the database, and set the analyzer
        model accordingly

        Args:
            idx : None, 'latest', or list, len 2
                loads either the latest (if idx is None or a string)
                or a specific date and time
        """
        fname = self.config['database']
        indexnames = list(self.config['index'].keys()) + ['date', 'time']

        if idx==None or isinstance(idx, str):
            datim = [slice(None), slice(None)]
        elif (isinstance(idx, list) or isinstance(idx, tuple) and len(idx)==2):
            datim = list(idx)
        indexvals = tuple(list(self.config['index'].values()) + datim)

        try:
            db = pd.read_excel(fname, index_col=list(range(len(indexvals))))
        except:
            return

        try:
            db_select = db.loc[indexvals, :].sort_index().iloc[-1, :]
        except:
            # indexvals nto present
            return

        cali_pars = {col: val
                     for col, val
                     in zip(db_select.index, db_select.values)
                     if not np.isnan(val)}

        self.analyzer.load_model(cali_pars)
        self.is_calibrated = True
