#!/usr/bin/env python
"""
    monet/control.py
    ~~~~~~~~~~~~~~~~

    Here, the calibrated system is controlled, correct laser power
    and attenuations are set for a set output power

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import pandas as pd
import logging
from icecream import ic

import monet.calibrate as mca
import monet.io as io
from monet import LASER_TAG, POWER_TAG


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


class IlluminationController():
    def __init__(self, config):
        """
        """
        self.config = config

        # here, all lasers (wavelengths) and powers are loaded
        config['index'][LASER_TAG] = slice(None)
        config['index'][POWER_TAG] = slice(None)

        self.calibrator = mca.PowerCalibrator(config)
        self.lasers = {}
        for laser, lconf in config['lasers'].items():
            self.lasers[laser] = mca.load_class(
                    lconf['classpath'], lconf['init_kwargs'])
            self.lasers[laser].enabled = False

        self.curr_laser = list(self.lasers.keys())[0]

        self.cali_db = io.load_database(
            self.config['database'], self.config['index'], 'last combinations')
        self.curr_laserpower = min(self.cali_db.index.get_level_values(POWER_TAG))

    def _populate_analyzers(self, db, laser):
        """from the database, create analyzers for various power settings
        Args:
            db : pandas Dataframe
                the database subset to choose from
            laser : str
                the laser to choose
        Returns:
            analyzers : dict
                the analyzers to evaluate the calibrated model for each power setting
            power_ranges : pandas DataFrame
                index: laser power settings
                columns: 'min', 'max'
        """
        subdb = db.loc[db.index.get_level_values(LASER_TAG)==laser]
        anaconfig = self.config['analysis']
        analyzers = {}
        power_ranges = pd.DataFrame(columns=['min', 'max'])
        for pwr, cali_pars in subdb.groupby(POWER_TAG):
            analyzers[pwr] = load_class(
                anaconfig['classpath'], anaconfig['init_kwargs'])
            analyzers[pwr].load_model(cali_pars)

            power_ranges.loc[pwr, :] = sorted(analyzers[pwr].output_range())
        return analyzers, power_ranges

    @property
    def laser(self):
        """Returns the list of laser names present
        Returns:
            lasers : list of str
        """
        return list(self.lasers.keys())

    @laser.setter
    def laser(self, laser):
        """Set the current laser by name
        Args:
            laser : str
                must be one of the keys in self.lasers
        """
        if laser in self.lasers.keys():
            self.lasers[self.curr_laser].enabled = False
            self.curr_laser = laser
            self.lasers[self.curr_laser].enabled = True
            self._analyzers, self._power_ranges = (
                self._populate_analyzers(self.cali_db, self.curr_laser))
        else:
            raise KeyError('Laser {:s} is not available'.format(str(laser)))

    def set_laserpower(self, laserpower):
        """Change the laser power output
        """
        self.curr_laserpower = laserpwr_best
        self.lasers[self.curr_laser].power = laserpwr_best
        self.calibrator.analyzer = self._analyzers[self.curr_laserpower]

    @property
    def power(self):
        return self.lasers[self.curr_laser].power

    @power.setter
    def power(self, pwr):
        """Set the power in the sample. If possible with current laser output
        power setting, use this, otherwise change laser output power, and
        in any case, adjust attenuator to get correct sample power.

        Args:
            pwr : float
                laser power in the sample
        """
        if ((pwr < self._power_ranges.loc[self.curr_laserpower, 'min'] or
             pwr > self._power_ranges.loc[self.curr_laserpower, 'max'])):
            # necessary to change laser output power setting - find best
            powerrange_centerdistance = {}
            for laserpwr, row in self.curr_laserpower.iterrows():
                range = row['max'] - row['min']
                quantile = (pwr-row['min'])/range
                if quantile > 0 and quantile < 1:
                    powerrange_centerdistance[laserpwr] = (quantile - .5)**2
            if len(powerrange_centerdistance.keys()) == 0:
                raise KeyError('Power setting {:.2f} is out of range.'.format(pwr))
            # find quantile closest to the center of the range (0.5)
            mindist = np.min(np.array(powerrange_centerdistance.values()))
            laserpwr_best = [
                k for k, v in powerrange_centerdistance.items()
                if v==mindist][0]
            self.set_laserpower(laserpwr_best)

        self.calibrator.set_power(pwr)
