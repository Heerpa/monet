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
from monet import (LASER_TAG, POWER_TAG, DEVICE_TAG,
                   POWERMETER_BFP, normalize_powermeter_type)


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
            except Exception as e:
                logger.warning('Could not load calibration: %s. '
                               'Calibration-dependent features disabled.', e)

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
    def __init__(self, config, do_load_cal=True, auto_enable_lasers=True):
        """
        Args:
            config : dict
                keys: 'analysis', 'attenuation'
                with sub-keys each: 'classpath', and 'init_kwargs'
            do_load_cal : bool
                whether or not to load the latest calibration
            ignore_powermeter : bool
                if True, the powermeter is not loaded
            auto_enable_lasers : bool
                whether to switch on lasers at connection.
        """
        super().__init__(config, do_load_cal=do_load_cal)

        # here, all lasers (wavelengths) and powers are loaded
        config['index'][LASER_TAG] = slice(None)
        config['index'][POWER_TAG] = slice(None)

        self.auto_enable_lasers = auto_enable_lasers
        self.lasers = {}
        lasers_missing = []
        for laser_key, lconf in config['lasers'].items():
            try:
                laser = int(laser_key)
            except:
                laser = laser_key
            try:
                settgs = lconf.get('settings', None)
                self.lasers[laser] = load_class(
                    lconf['classpath'], lconf['init_kwargs'], settgs)
                # self.lasers[laser].enabled = False
            except Exception as e:
                logger.warning('Could not load laser %s: %s', laser, e)
                print('Could not load laser {:s}: {:s}. '
                      'Check that the device is on and the COM port / '
                      'connection settings are correct.'.format(str(laser), str(e)))
                # Track the original config key — it may differ from `laser`
                # (e.g. the numeric-string key '488' vs the int 488).
                lasers_missing.append(laser_key)

        for laser_key in lasers_missing:
            self.config['lasers'].pop(laser_key)

        if not self.lasers:
            raise RuntimeError(
                'No lasers could be loaded. Configured lasers: {}. '
                'Check that devices are powered on and COM ports have not '
                'changed.'.format(list(config['lasers'].keys())))
        self.curr_laser = list(self.lasers.keys())[0]

        self._factors = {}         # {laser: transmission_objective}; = P_sample/P_bfp
        self._powermeter_type = {} # {laser: 'sample'/'bfp'}

        if 'beampath' in config.keys():
            self.beampath = BeamPath(config['beampath'])
            self.use_beampath = True
        else:
            self.use_beampath = False

        if do_load_cal:
            try:
                self.load_calibration_database()
            except Exception as e:
                logger.warning('Could not load calibration database: %s. '
                               'Calibration-dependent features disabled.', e)

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
            pars = {}
            for col in cali_pars.columns:
                val = cali_pars[col].to_numpy()[0]
                try:
                    if not np.isnan(val):
                        pars[col] = val
                except (TypeError, ValueError):
                    pass  # skip non-numeric columns (e.g. powermeter_type)
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
    def laser(self, laser):
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
            if self.auto_enable_lasers:
                self.lasers[self.curr_laser].enabled = True
            if self.is_calibrated:
                ic(self.cali_db)
                self._analyzers, self._power_ranges = (
                    self._populate_analyzers(self.cali_db, self.curr_laser))
                self.laserpower = self._power_ranges.index.min()
            else:
                logger.debug('Calibration not available, not setting analyzers.')
        else:
            # raise KeyError('Laser {:s} is not available'.format(str(laser)))
            print('Laser {:s} is not available'.format(str(laser)) + '. Choose one of ' + str(list(self.lasers.keys())))

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

    def _bfp_factor(self, laser=None):
        """Return transmission_objective = P_sample / P_bfp if the latest
        calibration used the back focal plane (BFP) powermeter, else 1.0."""
        if laser is None:
            laser = self.curr_laser
        pm_type = normalize_powermeter_type(
            self._powermeter_type.get(laser, 'sample'))
        if pm_type == POWERMETER_BFP:
            return self._factors.get(laser, 1.0)
        return 1.0

    def to_sample_plane(self, raw, laser=None):
        """Project a raw power-meter reading to the sample plane.

        The power meter measures in the back focal plane (BFP) when the active
        calibration was taken with the BFP meter; everything we report to the
        user, write into the MicroManager comment, and compare against
        calibration predictions is in the sample plane. This applies the
        objective transmission factor (P_sample / P_bfp) so a raw reading
        becomes a sample-plane power.

        Returns `raw` unchanged unless a BFP transmission factor is available
        for `laser` (i.e. the calibration used the BFP meter).

        Args:
            raw : float
                power-meter reading in its native (BFP) units
            laser : int or str, optional
                laser wavelength; defaults to curr_laser
        Returns:
            float : power projected to the sample plane
        """
        return raw * self._bfp_factor(laser)

    @property
    def power(self):
        attpos = self.attenuator.curr_pos()
        raw = self.analyzer.estimate_power(attpos)
        return raw * self._bfp_factor()

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
                    self._power_ranges.loc[self._power_ranges['max']*.95 > pwr].index)
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

        # Apply BFP correction: convert sample-plane target → BFP-equivalent
        # P_bfp = P_sample / transmission_objective
        newpwr = newpwr / self._bfp_factor()
        super(self.__class__, self.__class__).power.__set__(self, newpwr)
        # IlluminationControl.power.fset(self, pwr)

    def set_power_fixed_attenuator(self, pwr, laser=None):
        """Set output power by adjusting laser power only, keeping attenuator fixed.

        Uses calibrations at multiple laser power levels. At the current
        attenuator position, each calibration gives an expected output power.
        A linear fit across those (laser_power, output_power) pairs is inverted
        to find the laser power needed to reach *pwr*.

        Args:
            pwr : float
                desired output power in mW
            laser : int or str, optional
                laser wavelength to target; defaults to curr_laser
        Raises:
            ValueError if not calibrated or fewer than 2 laser power levels exist.
        """
        if not self.is_calibrated:
            raise ValueError('Not calibrated. Cannot set power.')

        if laser is None:
            laser = self.curr_laser

        analyzers, _ = self._populate_analyzers(self.cali_db, laser)
        if len(analyzers) < 2:
            raise ValueError(
                'At least 2 calibrated laser power levels are required for '
                'fixed-attenuator mode.')

        att_pos = self.attenuator.curr_pos()

        laser_pwrs = []
        output_pwrs = []
        for lpwr, analyzer in analyzers.items():
            try:
                out = analyzer.estimate_power(att_pos)
                laser_pwrs.append(float(lpwr))
                output_pwrs.append(float(out))
            except Exception:
                pass

        if len(laser_pwrs) < 2:
            raise ValueError(
                'Could not evaluate enough analyzer curves at the current '
                'attenuator position.')

        laser_pwrs = np.array(laser_pwrs)
        output_pwrs = np.array(output_pwrs)

        # Linear fit: output ≈ a·laser_pwr + b  →  laser_pwr = (output − b) / a
        a, b = np.polyfit(laser_pwrs, output_pwrs, 1)
        if abs(a) < 1e-12:
            raise ValueError(
                'Linear fit slope is near zero — cannot determine laser power.')

        # output_pwrs are in BFP units if BFP-calibrated; convert target
        # P_bfp_target = P_sample / transmission_objective
        factor = self._bfp_factor(laser)
        laser_pwr_needed = float(
            np.clip((pwr / factor - b) / a, laser_pwrs.min(), laser_pwrs.max()))
        logger.debug(
            'Fixed-attenuator mode: target %.3f mW → laser power %.3f mW '
            '(fit: a=%.4f, b=%.4f)', pwr, laser_pwr_needed, a, b)

        self.lasers[laser].power = laser_pwr_needed

    def set_power_fixed_laser(self, pwr, laser=None):
        """Set output power by adjusting the attenuator only, keeping laser
        power fixed at its current hardware value.

        Uses the calibration curve for the nearest calibrated laser power
        level to estimate the required attenuator position.

        Args:
            pwr : float
                desired output power in mW
            laser : int or str, optional
                laser wavelength; defaults to curr_laser
        Raises:
            ValueError if not calibrated.
        """
        if not self.is_calibrated:
            raise ValueError('Not calibrated. Cannot set power.')
        if laser is None:
            laser = self.curr_laser

        analyzers, _ = self._populate_analyzers(self.cali_db, laser)

        # Find the calibrated level nearest to the current hardware laser power
        curr_lp = float(self.lasers[laser].power)
        closest_level = min(analyzers.keys(),
                            key=lambda x: abs(float(x) - curr_lp))
        logger.debug('Fixed-laser mode: using calibrated level %s mW '
                     '(hardware laser power %.3f mW)', closest_level, curr_lp)

        factor = self._bfp_factor(laser)
        ctrlval = analyzers[closest_level].estimate(pwr / factor)
        self.set_attenuator(ctrlval)

    def predict_power_fixed_attenuator(self, laser_pwr, laser=None):
        """Predict output power at the current attenuator position for a given
        laser power, using the same linear interpolation as
        set_power_fixed_attenuator.  Used to compute calibration deviation
        after a fixed-attenuator feedback cycle.

        Args:
            laser_pwr : float
                the laser power currently set (mW)
            laser : int or str, optional
                laser wavelength; defaults to curr_laser
        Returns:
            float : predicted output power in mW
        """
        if not self.is_calibrated:
            raise ValueError('Not calibrated.')
        if laser is None:
            laser = self.curr_laser

        analyzers, _ = self._populate_analyzers(self.cali_db, laser)
        att_pos = self.attenuator.curr_pos()

        laser_pwrs = []
        output_pwrs = []
        for lpwr, analyzer in analyzers.items():
            try:
                out = analyzer.estimate_power(att_pos)
                laser_pwrs.append(float(lpwr))
                output_pwrs.append(float(out))
            except Exception:
                pass

        if len(laser_pwrs) < 2:
            raise ValueError('Need at least 2 calibrated power levels.')

        a, b = np.polyfit(np.array(laser_pwrs), np.array(output_pwrs), 1)
        raw = float(a * float(laser_pwr) + b)
        return raw * self._bfp_factor(laser)

    def accessible_power_range(self, mode, laser=None):
        """Return the (lo, hi) output power range reachable in the given mode.

        Args:
            mode : str
                'combined'         — laser power and attenuator both adjusted
                'fixed_laser'      — laser power fixed, attenuator adjusted
                'fixed_attenuator' — attenuator fixed, laser power adjusted
            laser : int or str, optional
                laser wavelength; defaults to curr_laser
        Returns:
            (lo, hi) : tuple of float, output power range in mW
        Raises:
            ValueError if not calibrated or no calibration ranges are available.
        """
        if not self.is_calibrated:
            raise ValueError('Not calibrated. Cannot determine power range.')
        if laser is None:
            laser = self.curr_laser

        pr = getattr(self, '_power_ranges', None)
        if pr is None or pr.empty:
            raise ValueError('No calibration power ranges available.')

        if mode == 'combined':
            lo = float(pr['min'].min())
            hi = float(pr['max'].max())
        elif mode == 'fixed_laser':
            curr_lp = 0.0
            try:
                curr_lp = float(self.lasers[laser].power)
            except Exception:
                pass
            closest = min(pr.index, key=lambda x: abs(float(x) - curr_lp))
            lo = float(pr.loc[closest, 'min'])
            hi = float(pr.loc[closest, 'max'])
        elif mode == 'fixed_attenuator':
            min_lp = float(pr.index.min())
            max_lp = float(pr.index.max())
            lo = self.predict_power_fixed_attenuator(min_lp, laser)
            hi = self.predict_power_fixed_attenuator(max_lp, laser)
            if lo > hi:
                lo, hi = hi, lo
        else:
            raise ValueError("Unknown power mode '{}'.".format(mode))
        return float(lo), float(hi)

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

        # Load powermeter types and correction factors
        self._powermeter_type = {}
        if 'powermeter_type' in self.cali_db.columns:
            for laser in self.lasers.keys():
                try:
                    laser_int = int(laser)
                    sub = self.cali_db.loc[
                        self.cali_db.index.get_level_values(LASER_TAG) == laser_int]
                    if not sub.empty:
                        self._powermeter_type[laser] = normalize_powermeter_type(
                            sub.iloc[-1]['powermeter_type'])
                except Exception:
                    pass

        self._factors = {}
        try:
            device = self.config['index'][DEVICE_TAG]
            factors_df = io.load_factors(self.config['database'], device=device)
            if not factors_df.empty:
                for laser in self.lasers.keys():
                    try:
                        laser_int = int(laser)
                        sub = factors_df.loc[
                            factors_df.index.get_level_values(LASER_TAG) == laser_int]
                        if not sub.empty:
                            self._factors[laser] = float(
                                sub.iloc[-1]['transmission_objective_mean'])
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug('Could not load powermeter factors: %s', exc)

        self.laser = self.curr_laser  # to populate the analyzers
        self.laserpower = self.curr_laserpower


def run_power_feedback(instrument, powermeter, target_pwr, laser, mode,
                       kp=0.85, ki=0.15, max_dev_pct=1.0, max_iter=20,
                       settle_time=2.0, progress_callback=None,
                       cancel_check=None):
    """Closed-loop power setting using a power meter.

    Performs an initial open-loop power set, then iteratively measures the
    output power and corrects it until it is within `max_dev_pct` of
    `target_pwr` or `max_iter` correction iterations have run.

    The power meter and the instrument are passed separately because the
    instrument does not own the power meter (it is held by the calibration
    protocol / CLI shell).

    Args:
        instrument : IlluminationLaserControl
            the calibrated illumination control
        powermeter : AbstractPowerMeter
            the power meter to read the output power
        target_pwr : float
            desired output power in mW
        laser : int or str
            laser wavelength to target; must equal instrument.curr_laser so
            that instrument.analyzer is consistent
        mode : str
            'fixed_laser'      — laser power fixed, attenuator adjusted (PI loop)
            'fixed_attenuator' — attenuator fixed, laser power adjusted
            ('combined' is not supported for feedback and raises ValueError)
        kp, ki : float
            proportional / integral gains, used in 'fixed_laser' mode
        max_dev_pct : float
            convergence tolerance, in percent of target_pwr
        max_iter : int
            maximum number of correction iterations
        settle_time : float
            seconds to wait after the initial set before the first reading
        progress_callback : callable, optional
            called as progress_callback(iteration, setpoint, measured);
            iteration 0 is the reading taken right after the initial set
        cancel_check : callable, optional
            returns True to abort the loop early
    Returns:
        dict with keys:
            measured : float — last measured power, projected to the sample
                plane (raw beampath reading × objective transmission factor)
            converged : bool — whether the tolerance was reached
            cali_pred : float or None — power the calibration predicts
            out_of_range : bool — whether the attenuator range limit was hit
            att_pos : float or None — final attenuator position
            laser_pwr : float or None — final laser power set-point
            iterations : int — number of correction iterations performed
    """
    import time

    if mode not in ('fixed_laser', 'fixed_attenuator'):
        raise ValueError(
            "Feedback is only supported for 'fixed_laser' and "
            "'fixed_attenuator' modes, not '{}'.".format(mode))

    # The power meter and the analyzer work in back focal plane (BFP) units;
    # `target_pwr`, the progress callbacks and the returned `measured` are in
    # the sample plane. Run the loop internally in BFP units (so analyzer calls
    # and meter readings stay native) and project to the sample plane only at
    # the reporting boundary. `factor` is 1.0 unless the active calibration was
    # taken with the BFP meter.
    try:
        factor = instrument._bfp_factor(laser)
    except Exception:
        factor = 1.0
    if not factor:
        factor = 1.0
    target_bp = target_pwr / factor   # sample-plane target in BFP units

    # Initial open-loop power setting (these take a sample-plane target)
    if mode == 'fixed_attenuator':
        instrument.set_power_fixed_attenuator(target_pwr, laser)
    else:  # fixed_laser
        instrument.set_power_fixed_laser(target_pwr, laser)

    time.sleep(settle_time)

    integral_e = 0.0   # accumulated normalized error (PI integral term)
    converged = False
    out_of_range = False
    last_setpoint = target_pwr   # reported in the sample plane

    measured_bp = powermeter.read()
    if progress_callback is not None:
        progress_callback(0, last_setpoint, measured_bp * factor)

    iterations = 0
    for iter_num in range(max_iter):
        if cancel_check is not None and cancel_check():
            break
        dev_pct = (abs(measured_bp - target_bp) / target_bp * 100.0
                   if target_bp > 0 else 0.0)
        if dev_pct <= max_dev_pct:
            converged = True
            break
        if measured_bp <= 0:
            break  # cannot correct without light
        if mode == 'fixed_attenuator':
            # Proportional correction: scale current laser power
            curr_lp = instrument.lasers[laser].power
            instrument.lasers[laser].power = curr_lp * target_bp / measured_bp
            last_setpoint = target_pwr  # output target always target_pwr
        else:
            # PI controller in fixed-laser (attenuator-adjusting) mode.
            # e is the normalised error (dimensionless).
            e = (target_bp - measured_bp) / target_bp
            integral_e += e
            # Anti-windup: bound the integral contribution
            integral_e = float(np.clip(integral_e, -5.0, 5.0))
            corrected_target = target_bp * (
                1.0 + kp * e + ki * integral_e)
            try:
                out_rng = instrument.analyzer.output_range()
                lo = float(out_rng[0])
                hi = float(out_rng[1])
                clamped = float(np.clip(corrected_target, lo, hi))
                if abs(clamped - corrected_target) > 1e-9:
                    out_of_range = True
                    integral_e = 0.0  # reset on clamp (anti-windup)
                corrected_target = clamped
            except Exception:
                corrected_target = max(0.0, corrected_target)
            att_pos = instrument.analyzer.estimate(corrected_target)
            instrument.attenuator.set(att_pos)
            last_setpoint = corrected_target * factor  # report sample plane
            time.sleep(3)
        time.sleep(0.5)
        measured_bp = powermeter.read(5)
        time.sleep(0.5)
        measured_bp = powermeter.read(50)
        iterations = iter_num + 1
        if progress_callback is not None:
            progress_callback(iter_num + 1, last_setpoint, measured_bp * factor)

    # Project the final reading to the sample plane for all reporting.
    measured = measured_bp * factor

    # Calibration deviation: what the calibration predicts vs. what was measured
    cali_pred = None
    try:
        if mode == 'fixed_attenuator':
            curr_lp = instrument.lasers[laser].power
            cali_pred = instrument.predict_power_fixed_attenuator(
                curr_lp, laser)
        else:
            cali_pred = instrument.power
    except Exception:
        cali_pred = None
    try:
        att_pos = instrument.attenuator.curr_pos()
    except Exception:
        att_pos = None
    try:
        laser_pwr = instrument.lasers[laser].power
    except Exception:
        laser_pwr = None

    return {
        'measured': measured,
        'converged': converged,
        'cali_pred': cali_pred,
        'out_of_range': out_of_range,
        'att_pos': att_pos,
        'laser_pwr': laser_pwr,
        'iterations': iterations,
    }
