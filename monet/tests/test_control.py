"""
    monet/tests/test_control.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Test the control module of monet.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import unittest
from unittest import mock
import monet.calibrate as mca
import numpy as np
import pandas as pd
import os
import shutil
from datetime import datetime

from monet import DATABASE_INDEXLEVELS
import monet.control as mco


class _TrackingAttenuator:
    """Minimal attenuator that records the last set position. The stock
    TestAttenuator is a no-op whose curr_pos() is always 0, which is not
    enough to exercise the attenuator-adjusting feedback loop."""

    def __init__(self, start=0.0):
        self._pos = start

    def set(self, val):
        self._pos = val

    def curr_pos(self):
        return self._pos

    def home(self):
        self._pos = 0.0


class _LaserPowerMeter:
    """Fake powermeter whose reading tracks the laser power set-point.
    Models 'fixed_attenuator' mode, where only the laser power changes."""

    unit = 'mW'

    def __init__(self, instrument, laser, gain=1.0):
        self._inst = instrument
        self._laser = laser
        self._gain = gain

    def read(self, averaging=10):
        return self._inst.lasers[self._laser].power * self._gain


class _AttenuatorCurvePowerMeter:
    """Fake powermeter whose reading follows the attenuator calibration
    curve. Models 'fixed_laser' mode, where only the attenuator moves.
    `miscal` is a steady multiplicative offset so the PI loop has something
    to correct."""

    unit = 'mW'

    def __init__(self, instrument, miscal=1.0):
        self._inst = instrument
        self._miscal = miscal

    def read(self, averaging=10):
        att = self._inst.attenuator.curr_pos()
        return self._inst.analyzer.estimate_power(att) * self._miscal


class TestControl(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_02_IlluminationLaserControl(self):
        # TestData/ is git-ignored, so create the dirs we write into (a fresh
        # clone won't have them).
        os.makedirs('monet/tests/TestData/calibrate', exist_ok=True)
        os.makedirs('monet/tests/TestData/control', exist_ok=True)

        datim = [datetime.now().strftime('%Y-%m-%d'),
                 datetime.now().strftime('%H:%M')]
        db = pd.DataFrame(
            index=pd.MultiIndex.from_product(
                [['DefaultMicroscope'], ['488', '561'], [50, 100], [datim[0]], [datim[1]]],
                names=tuple(DATABASE_INDEXLEVELS)),
            data={'bkg': [0]*4, 'amp': [50, 100, 40, 80], 'phi': [30, 30, 25, 25]}
        )
        db_path = 'monet/tests/TestData/control/power_database.xlsx'
        db.to_excel(db_path)

        config = {
            'database': db_path,
            'dest_calibration_plot': 'monet/tests/TestData/control/',
            'index': {
                'name': 'DefaultMicroscope',
                },
            'powermeter': {
                'classpath': 'monet.powermeter.TestPowerMeter',
                'init_kwargs': {
                    'bkg': 0,
                    'amp': 50,
                    'phi': 30,
                    'start': 10,
                    'step': 5,
                    'noise': 3}
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
                },
        }
        ctrl = mco.IlluminationControl(config)

        print(ctr.power)
        ctrl.power = 50
        ctrl.power = 200

        assert True


    def test_02_IlluminationLaserControl(self):
        # TestData/ is git-ignored, so create the dirs we write into (a fresh
        # clone won't have them).
        os.makedirs('monet/tests/TestData/calibrate', exist_ok=True)
        os.makedirs('monet/tests/TestData/control', exist_ok=True)

        datim = [datetime.now().strftime('%Y-%m-%d'),
                 datetime.now().strftime('%H:%M')]
        db = pd.DataFrame(
            index=pd.MultiIndex.from_product(
                [['DefaultMicroscope'], ['488', '561'], [50, 100], [datim[0]], [datim[1]]],
                names=tuple(DATABASE_INDEXLEVELS)),
            data={'bkg': [0]*4, 'amp': [50, 100, 40, 80], 'phi': [30, 30, 25, 25]}
        )
        db_path = 'monet/tests/TestData/control/power_database.xlsx'
        db.to_excel(db_path)

        config = {
            'database': db_path,
            'dest_calibration_plot': 'monet/tests/TestData/control/',
            'index': {
                'name': 'DefaultMicroscope',
                },
            'powermeter': {
                'classpath': 'monet.powermeter.TestPowerMeter',
                'init_kwargs': {
                    'bkg': 0,
                    'amp': 50,
                    'phi': 30,
                    'start': 10,
                    'step': 5,
                    'noise': 3}
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
                },
            'lasers' : {
                '488': {
                    'classpath': 'monet.laser.TestLaser',
                    'init_kwargs': {'port': 'COM4'},
                    },
                '561': {
                    'classpath': 'monet.laser.TestLaser',
                    'init_kwargs': {'port': 'COM7'},
                    },
                '640': {
                    'classpath': 'monet.laser.TestLaser',
                    'init_kwargs': {'port': 'COM8'},
                    },
                },
        }
        ctrl = mco.IlluminationLaserControl(config)

        print(ctrl.laser)

        ctrl.laser = '561'

        ctrl.power = 50
        ctrl.power = 200
        print(ctrl.laserpower)

        assert True

    def _build_laser_control(self):
        """Build an IlluminationLaserControl with a linear calibration for
        lasers 488 and 561 at laser powers 50 and 100 mW. The attenuator is
        swapped for one that tracks its position so the feedback loop can be
        exercised."""
        os.makedirs('monet/tests/TestData/control', exist_ok=True)
        datim = [datetime.now().strftime('%Y-%m-%d'),
                 datetime.now().strftime('%H:%M')]
        db = pd.DataFrame(
            index=pd.MultiIndex.from_product(
                [['DefaultMicroscope'], ['488', '561'], [50, 100],
                 [datim[0]], [datim[1]]],
                names=tuple(DATABASE_INDEXLEVELS)),
            data={'bkg': [0, 0, 0, 0], 'amp': [1.0, 2.0, 0.8, 1.6]},
        )
        db_path = 'monet/tests/TestData/control/feedback_test_db.xlsx'
        db.to_excel(db_path)

        config = {
            'database': db_path,
            'dest_calibration_plot': 'monet/tests/TestData/control/',
            'index': {'name': 'DefaultMicroscope'},
            'attenuation': {
                'classpath': 'monet.attenuation.TestAttenuator',
                'init_kwargs': {
                    'bkg': 0, 'amp': 50, 'phi': 30,
                    'start': 30, 'step': 5},
            },
            'analysis': {
                'classpath': 'monet.analysis.LinearCurveAnalyzer',
                'init_kwargs': {'min': 0, 'max': 100, 'step': 5},
            },
            'lasers': {
                '488': {'classpath': 'monet.laser.TestLaser',
                        'init_kwargs': {'port': 'COM4'}},
                '561': {'classpath': 'monet.laser.TestLaser',
                        'init_kwargs': {'port': 'COM7'}},
            },
        }
        ctrl = mco.IlluminationLaserControl(config)
        # The stock TestAttenuator is a no-op; swap in one that tracks state.
        ctrl.attenuator = _TrackingAttenuator(start=30)
        ctrl.laser = 488
        ctrl.laserpower = 50
        return ctrl

    @mock.patch('time.sleep')
    def test_run_power_feedback_fixed_attenuator(self, _sleep):
        """Feedback in fixed_attenuator mode scales the laser power until the
        measured output power reaches the target."""
        ctrl = self._build_laser_control()
        pm = _LaserPowerMeter(ctrl, laser=488, gain=1.0)
        progress = []
        result = mco.run_power_feedback(
            ctrl, pm, target_pwr=30, laser=488, mode='fixed_attenuator',
            max_dev_pct=1.0, max_iter=20,
            progress_callback=lambda i, s, m: progress.append((i, s, m)))

        self.assertTrue(result['converged'])
        self.assertLessEqual(abs(result['measured'] - 30) / 30 * 100.0, 1.0)
        self.assertGreaterEqual(len(progress), 2)
        for key in ('measured', 'converged', 'cali_pred', 'out_of_range',
                    'att_pos', 'laser_pwr', 'iterations'):
            self.assertIn(key, result)

    @mock.patch('time.sleep')
    def test_run_power_feedback_fixed_laser(self, _sleep):
        """Feedback in fixed_laser mode runs the PI controller on the
        attenuator until the measured output power reaches the target."""
        ctrl = self._build_laser_control()
        pm = _AttenuatorCurvePowerMeter(ctrl, miscal=1.1)
        progress = []
        result = mco.run_power_feedback(
            ctrl, pm, target_pwr=30, laser=488, mode='fixed_laser',
            max_dev_pct=2.0, max_iter=20,
            progress_callback=lambda i, s, m: progress.append((i, s, m)))

        self.assertTrue(result['converged'])
        self.assertLessEqual(abs(result['measured'] - 30) / 30 * 100.0, 2.0)
        self.assertGreaterEqual(len(progress), 2)

    @mock.patch('time.sleep')
    def test_run_power_feedback_rejects_combined(self, _sleep):
        """Feedback is not defined for the combined mode."""
        ctrl = self._build_laser_control()
        pm = _LaserPowerMeter(ctrl, laser=488)
        with self.assertRaises(ValueError):
            mco.run_power_feedback(
                ctrl, pm, target_pwr=30, laser=488, mode='combined')

    def test_accessible_power_range(self):
        """accessible_power_range returns a sane (lo, hi) for every mode."""
        ctrl = self._build_laser_control()
        for mode in ('combined', 'fixed_laser', 'fixed_attenuator'):
            lo, hi = ctrl.accessible_power_range(mode, 488)
            self.assertLessEqual(lo, hi)
            self.assertTrue(np.isfinite(lo) and np.isfinite(hi))

    def test_to_sample_plane(self):
        """to_sample_plane is a no-op for sample-plane calibrations and applies
        the objective transmission factor for back focal plane calibrations."""
        ctrl = self._build_laser_control()
        # No factor loaded → sample plane → unchanged.
        self.assertEqual(ctrl.to_sample_plane(10.0, 488), 10.0)
        # BFP calibration with a transmission factor → projected.
        ctrl._powermeter_type[488] = 'bfp'
        ctrl._factors[488] = 2.5
        self.assertAlmostEqual(ctrl.to_sample_plane(10.0, 488), 25.0)
        # A sample-plane laser is unaffected even when another has a factor.
        ctrl._powermeter_type[561] = 'sample'
        self.assertEqual(ctrl.to_sample_plane(10.0, 561), 10.0)

    def test_to_sample_plane_legacy_values(self):
        """Legacy stored values 'beampath'/'manual' are still honoured."""
        ctrl = self._build_laser_control()
        ctrl._powermeter_type[488] = 'beampath'   # legacy → bfp
        ctrl._factors[488] = 2.0
        self.assertAlmostEqual(ctrl.to_sample_plane(10.0, 488), 20.0)
        ctrl._powermeter_type[561] = 'manual'     # legacy → sample
        self.assertEqual(ctrl.to_sample_plane(10.0, 561), 10.0)

    @mock.patch('time.sleep')
    def test_run_power_feedback_projects_to_sample_plane(self, _sleep):
        """With a BFP transmission factor, the feedback loop drives the
        sample-plane power (raw reading × factor) to the target and reports
        the projected value."""
        ctrl = self._build_laser_control()
        ctrl._powermeter_type[488] = 'bfp'
        ctrl._factors[488] = 2.0
        # The meter reads in the back focal plane (raw analyzer curve).
        pm = _AttenuatorCurvePowerMeter(ctrl, miscal=1.0)
        progress = []
        result = mco.run_power_feedback(
            ctrl, pm, target_pwr=30, laser=488, mode='fixed_laser',
            max_dev_pct=2.0, max_iter=20,
            progress_callback=lambda i, s, m: progress.append((i, s, m)))

        self.assertTrue(result['converged'])
        # Returned measured is sample-plane: raw beampath ≈ 15, ×2 ≈ 30.
        self.assertLessEqual(abs(result['measured'] - 30) / 30 * 100.0, 2.0)
        # The raw meter reading itself is ~target/factor = 15.
        self.assertLessEqual(abs(pm.read() - 15) / 15 * 100.0, 3.0)
        # Progress is reported in the sample plane too.
        self.assertLessEqual(abs(progress[-1][2] - 30) / 30 * 100.0, 2.0)

    # ── laser / laserpower / enabled properties ──────────────────────────

    def test_laser_property_lists_and_selects(self):
        ctrl = self._build_laser_control()
        self.assertEqual(sorted(ctrl.laser), [488, 561])
        ctrl.laser = 561
        self.assertEqual(ctrl.curr_laser, 561)
        # Unknown laser is ignored (prints), current laser unchanged.
        ctrl.laser = 999
        self.assertEqual(ctrl.curr_laser, 561)

    def test_laserpower_property(self):
        ctrl = self._build_laser_control()
        ctrl.laserpower = 100
        self.assertEqual(ctrl.curr_laserpower, 100)
        self.assertEqual(ctrl.laserpower, 100)

    def test_laser_enabled_property(self):
        ctrl = self._build_laser_control()
        ctrl.laser_enabled = True
        self.assertTrue(ctrl.laser_enabled)
        ctrl.laser_enabled = False
        self.assertFalse(ctrl.laser_enabled)

    # ── power getter / setter (combined mode) ────────────────────────────

    def test_power_getter(self):
        ctrl = self._build_laser_control()
        # att_pos=30, level-50 linear amp=1.0 → 30 mW.
        self.assertAlmostEqual(ctrl.power, 30.0, places=6)

    def test_power_setter_in_range(self):
        ctrl = self._build_laser_control()
        ctrl.power = 50.0  # within level-50 range [0, 100]
        self.assertEqual(ctrl.curr_laserpower, 50)
        self.assertAlmostEqual(ctrl.attenuator.curr_pos(), 50.0, places=6)
        self.assertAlmostEqual(ctrl.power, 50.0, places=6)

    def test_power_setter_out_of_range_switches_level(self):
        ctrl = self._build_laser_control()
        # 150 mW exceeds level-50 max (100) → switch to level 100 (max 200).
        ctrl.power = 150.0
        self.assertEqual(ctrl.curr_laserpower, 100)
        self.assertAlmostEqual(ctrl.power, 150.0, places=6)

    def test_power_setter_not_calibrated_raises(self):
        ctrl = self._build_laser_control()
        ctrl.is_calibrated = False
        with self.assertRaises(ValueError):
            ctrl.power = 50.0

    # ── set_power_fixed_attenuator / fixed_laser / predict ───────────────

    def test_set_power_fixed_attenuator(self):
        ctrl = self._build_laser_control()
        # att=30: (50→30, 100→60) ⇒ output = 0.6·laser_pwr. Target 45 ⇒ 75 mW.
        ctrl.set_power_fixed_attenuator(45.0, laser=488)
        self.assertAlmostEqual(ctrl.lasers[488].power, 75.0, places=4)

    def test_set_power_fixed_laser(self):
        ctrl = self._build_laser_control()
        ctrl.laserpower = 50
        ctrl.set_power_fixed_laser(40.0, laser=488)
        # level-50 amp=1.0 ⇒ attenuator position = target.
        self.assertAlmostEqual(ctrl.attenuator.curr_pos(), 40.0, places=4)

    def test_predict_power_fixed_attenuator(self):
        ctrl = self._build_laser_control()
        self.assertAlmostEqual(
            ctrl.predict_power_fixed_attenuator(75.0, laser=488), 45.0,
            places=4)

    def test_fixed_modes_not_calibrated_raise(self):
        ctrl = self._build_laser_control()
        ctrl.is_calibrated = False
        with self.assertRaises(ValueError):
            ctrl.set_power_fixed_attenuator(45.0, laser=488)
        with self.assertRaises(ValueError):
            ctrl.set_power_fixed_laser(40.0, laser=488)
        with self.assertRaises(ValueError):
            ctrl.predict_power_fixed_attenuator(75.0, laser=488)

    # ── accessible_power_range error paths ───────────────────────────────

    def test_accessible_power_range_unknown_mode_raises(self):
        ctrl = self._build_laser_control()
        with self.assertRaises(ValueError):
            ctrl.accessible_power_range('nonsense', 488)

    def test_accessible_power_range_not_calibrated_raises(self):
        ctrl = self._build_laser_control()
        ctrl.is_calibrated = False
        with self.assertRaises(ValueError):
            ctrl.accessible_power_range('combined', 488)

    # ── construction edge case ───────────────────────────────────────────

    def test_no_lasers_loadable_raises_runtimeerror(self):
        config = {
            'database': 'unused.xlsx',
            'index': {'name': 'DefaultMicroscope'},
            'attenuation': {
                'classpath': 'monet.attenuation.TestAttenuator',
                'init_kwargs': {},
            },
            'analysis': {
                'classpath': 'monet.analysis.LinearCurveAnalyzer',
                'init_kwargs': {'min': 0, 'max': 100},
            },
            # A numeric-string key ('488') that fails to load used to trip a
            # KeyError in the cleanup loop; the missing-laser cleanup must now
            # pop the original key and reach the graceful RuntimeError.
            'lasers': {
                '488': {'classpath': 'monet.laser.NoSuchLaser',
                        'init_kwargs': {}},
            },
        }
        with self.assertRaises(RuntimeError):
            mco.IlluminationLaserControl(config, do_load_cal=False)
