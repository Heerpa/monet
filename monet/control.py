#!/usr/bin/env python
"""
    monet/control.py
    ~~~~~~~~~~~~~~~~

    Here, the calibrated system is controlled, correct laser power
    and attenuations are set for a set output power

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import numpy as np
import pandas as pd
import logging
from icecream import ic

from monet.util import load_class
import monet.io as io
from monet.beampath import BeamPath
import monet.laser as mlas
from monet import LASER_TAG, POWER_TAG, DEVICE_TAG


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


class IlluminationControl():
    """A class to control an illumination via an attenuator, with no
    control over laser power.

    Example for configuration:
    default_config = {
        'database': '../power_database.xlsx',
        'index': {
            'name': 'DefaultMicroscope',
            LASER_TAG: 488,
            POWER_TAG: 100},
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
    """
    def __init__(self, config, do_load_cal=True):
        """Initialize the analyzer, and attenuator classes
        defined in the configuration file.

        Args:
            config : dict
                keys: 'analysis', 'attenuation'
                with sub-keys each: 'classpath', and 'init_kwargs'
            do_load_cal : bool
                whether or not to load the latest calibration
        """
        self.config = config
        self.is_calibrated = False

        self.config = config
        anaconfig = config['analysis']
        self.analyzer = load_class(
            anaconfig['classpath'], anaconfig['init_kwargs'])

        attconfig = config['attenuation']
        settgs = attconfig.get('settings', None)
        self.attenuator = load_class(
            attconfig['classpath'], attconfig['init_kwargs'], settgs)

        if do_load_cal:
            try:
                self.load_calibration()
            except:
                pass

    @property
    def power(self):
        return self.attenuator.estimate_power()

    @power.setter
    def power(self, power):
        """Set a power level once the power has been calibrated.

        Args:
            power : float
                the power to set in mW
        """
        if not self.is_calibrated:
            raise ValueError('No calibration present. Please calibrate first.')
        ctrlval = self.analyzer.estimate(power)
        self.set_attenuator(ctrlval)

    def set_attenuator(self, value):
        """Set the attenuator value
        """
        self.attenuator.set(value)

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


class IlluminationLaserControl(IlluminationControl):
    """A class to control the illumination for multiple lasers, setting
    multiple power levels for them.

    Additional config entry example compared to IlluminationControl:
    'lasers' : {
        '488': {
            'classpath': 'monet.laser.Toptica',
            'init_kwargs': {'port': 'COM4'},
            },
        '561': {
            'classpath': 'monet.laser.MPBVFL',
            'init_kwargs': {'port': 'COM7'},
            },
        '640': {
            'classpath': 'monet.laser.MPBVFL',
            'init_kwargs': {'port': 'COM8'},
            },
        },
    """
    def __init__(self, config, do_load_cal=True):
        """
        Args:
            config : dict
                keys: 'analysis', 'attenuation'
                with sub-keys each: 'classpath', and 'init_kwargs'
            do_load_cal : bool
                whether or not to load the latest calibration
            ignore_powermeter : bool
                if True, the powermeter is not loaded
        """
        super().__init__(config, do_load_cal=do_load_cal)

        # here, all lasers (wavelengths) and powers are loaded
        config['index'][LASER_TAG] = slice(None)
        config['index'][POWER_TAG] = slice(None)

        self.lasers = {}
        lasers_missing = []
        for laser, lconf in config['lasers'].items():
            try:
                laser = int(laser)
            except:
                pass
            try:
                settgs = lconf.get('settings', None)
#                self.lasers[laser] = load_class(
#                    lconf['classpath'], lconf['init_kwargs'], **settgs)
                self.lasers[laser] = load_class(
                    lconf['classpath'], lconf['init_kwargs'])
                # self.lasers[laser].enabled = False
            except Exception as e:
                logger.debug('could not load laser {:s}.'.format(str(laser)))
                logger.debug(e)
                print('Could not load laser {:s}.'.format(
                    str(laser)))
                # self.lasers[laser] = mlas.TestLaser({})
                lasers_missing.append(laser)

        for laser in lasers_missing:
            self.config['lasers'].pop(laser)

        self.curr_laser = list(self.lasers.keys())[0]


        if 'beampath' in config.keys():
            self.beampath = BeamPath(config['beampath'])
            self.use_beampath = True
        else:
            self.use_beampath = False

        if do_load_cal:
            try:
                self.load_calibration_database()
            except:
                pass

    def _populate_analyzers(self, db, laser):
        """from the database, create analyzers for various power settings
        Args:
            db : pandas Dataframe
                the database subset to choose from
            laser : str or int, numeric
                the laser to choose
        Returns:
            analyzers : dict
                the analyzers to evaluate the calibrated model for each power setting
            power_ranges : pandas DataFrame
                index: laser power settings
                columns: 'min', 'max'
        """
        if not self.is_calibrated:
            raise KeyError('Cannot populate analyzers: no calibration present.')
        laser = int(laser)
        ic(db)
        subdb = db.loc[db.index.get_level_values(LASER_TAG)==laser]
        ic(subdb)
        anaconfig = self.config['analysis']
        analyzers = {}
        power_ranges = pd.DataFrame(columns=['min', 'max'])
        for pwr, cali_pars in subdb.groupby(POWER_TAG):
            pars = {col: cali_pars[col].to_numpy()[0] for col in cali_pars.columns}
            analyzers[pwr] = load_class(
                anaconfig['classpath'], anaconfig['init_kwargs'])
            analyzers[pwr].load_model(pars)

            power_ranges.loc[pwr, :] = sorted(analyzers[pwr].output_range())
        ic(power_ranges)
        return analyzers, power_ranges

    @property
    def laser(self):
        """Returns the list of laser names present
        Returns:
            lasers : list of str
        """
        return list(self.lasers.keys())

    @laser.setter
    def laser(self, laser, enable=True):
        """Set the current laser by name
        Args:
            laser : str
                must be one of the keys in self.lasers
        """
        try:
            laser = int(laser)
        except:
            pass
        if laser in self.lasers.keys():
            # self.lasers[self.curr_laser].enabled = False
            self.curr_laser = laser
            self.config['index'][LASER_TAG] = laser
            self.lasers[self.curr_laser].enabled = enable
            if self.is_calibrated:
                ic(self.cali_db)
                self._analyzers, self._power_ranges = (
                    self._populate_analyzers(self.cali_db, self.curr_laser))
                self.laserpower = self._power_ranges.index.min()
            else:
                logger.debug('Calibration not available, not setting analyzers.')
        else:
            raise KeyError('Laser {:s} is not available'.format(str(laser)))

    @property
    def laserpower(self):
        return self.lasers[self.curr_laser].power

    @laserpower.setter
    def laserpower(self, laserpower):
        """Change the laser power output
        """
        try:
            laserpower = int(laserpower)
        except:
            pass
        self.curr_laserpower = laserpower
        self.config['index'][POWER_TAG] = laserpower
        self.lasers[self.curr_laser].power = laserpower
        if self.is_calibrated:
            self.analyzer = self._analyzers[self.curr_laserpower]

    @property
    def laser_enabled(self):
        return self.lasers[self.curr_laser].enabled

    @laser_enabled.setter
    def laser_enabled(self, value):
        self.lasers[self.curr_laser].enabled = value

    @property
    def power(self):
        return self.analyzer.estimate_power()

    @power.setter
    def power(self, pwr):
        """Set the power in the sample. If possible with current laser output
        power setting, use this, otherwise change laser output power, and
        in any case, adjust attenuator to get correct sample power.

        Args:
            pwr : float
                laser power in the sample
        """
        if not self.is_calibrated:
            raise ValueError('Not calibrated. Cannot set power.')

        newpwr = pwr

        if ((pwr < self._power_ranges.loc[self.curr_laserpower, 'min'] or
             pwr > self._power_ranges.loc[self.curr_laserpower, 'max'])):
            # necessary to change laser output power setting

            # find best laserpwoer: minimal laserpower of which 95% of max 
            # is larger than pwr to set 
            laserpwr_best = list(
                    self._power_ranges.loc[self.power_ranges['max']*.95 > pwr].index)
            if len(laserpwr_best) > 0:
                laserpwr_best = min(laserpwr_best)
            else:
                laserpwr_best = max(list(self._power_ranges.index))

            if self._power_ranges.loc[laserpwr_best, 'min'] > pwr:
                newpwr = self._power_ranges.loc[laserpwr_best, 'min']
                logger.debug(
                    'Power setting {:.2f} is out of range. '.format(pwr) +
                    'Setting closest power = {:.2f}.'.format(newpwr))
                print(
                    'Power setting {:.2f} is out of range. '.format(pwr) +
                    'Setting closest power = {:.2f}.'.format(newpwr))
                pwr = newpwr
            elif self._power_ranges.loc[laserpwr_best, 'max'] < pwr:
                newpwr = self._power_ranges.loc[laserpwr_best, 'max']
                logger.debug(
                    'Power setting {:.2f} is out of range. '.format(pwr) +
                    'Setting closest power = {:.2f}.'.format(newpwr))
                print(
                    'Power setting {:.2f} is out of range. '.format(pwr) +
                    'Setting closest power = {:.2f}.'.format(newpwr))
                pwr = newpwr

            # # ALTERNATIVE SOLUTION
            # # find best laserpower: that which's center of power range is 
            # # closest to the power to set
            # powerrange_centerdistance = {}
            # for laserpwr, row in self._power_ranges.iterrows():
            #     range = row['max'] - row['min']
            #     if range > 0:
            #         quantile = (pwr-row['min'])/range
            #     else:
            #         quantile = (pwr-row['min'])/1
            #     powerrange_centerdistance[laserpwr] = np.sqrt((quantile - .5)**2)

            # # find quantile closest to the center of the range (0.5)
            # ic(powerrange_centerdistance)
            # mindist = min(list(powerrange_centerdistance.values()))
            # ic(mindist)
            # laserpwr_best = [
            #     k for k, v in powerrange_centerdistance.items()
            #     if v==mindist][0]

            # if min(list(powerrange_centerdistance.values())) >.5:
            #     range = self._power_ranges.loc[laserpwr_best, :]
            #     if pwr <= range['min']:
            #         newpwr = range['min']
            #     else:
            #         newpwr = range['max']
            #     logger.debug(
            #         'Power setting {:.2f} is out of range. '.format(pwr) +
            #         'Setting closest power = {:.2f}.'.format(newpwr))
            #     print(
            #         'Power setting {:.2f} is out of range. '.format(pwr) +
            #         'Setting closest power = {:.2f}.'.format(newpwr))

            logger.debug('setting laser power to {:s}'.format(str(laserpwr_best)))
            print('setting laser power to {:s}'.format(str(laserpwr_best)))
            self.laserpower = laserpwr_best

        # super().power = pwr
        super(self.__class__, self.__class__).power.__set__(self, newpwr)
        # IlluminationControl.power.fset(self, pwr)

    def load_calibration_database(self):
        load_index = {DEVICE_TAG: self.config['index'][DEVICE_TAG]}
        self.cali_db = io.load_database(
            self.config['database'], load_index, 'last combinations')
        logger.debug('loaded latest calibrations for every combination')
        ic(self.cali_db)
        index_combi = self.cali_db.index.to_frame(index=False)
        ic(index_combi)
        ic(self.curr_laser)
        logger.debug(index_combi.loc[index_combi[LASER_TAG]==self.curr_laser,
                        :])
        self.curr_laserpower = min(
            index_combi.loc[index_combi[LASER_TAG]==self.curr_laser,
                            POWER_TAG])
        self.is_calibrated = True

        self.laser = self.curr_laser  # to populate the analyzers
        self.laserpower = self.curr_laserpower
