"""
    monet/tests/test_calibration.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Test the calibration module of monet.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import unittest
import monet.calibrate as mca
import numpy as np
import os
import shutil


class TestCalibration(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_01_Calibrator1D(self):
        try:
            shutil.rmtree('monet/tests/TestData/calibrate')
        except:
            pass
        try:
            os.mkdir('monet/tests/TestData/calibrate')
        except:
            pass

        config = {
            'database': 'monet/tests/TestData/calibrate/power_database.xlsx',
            'index': {
                'name': 'DefaultMicroscope',
                'wavelength [nm]': 488,
                'laser_power [mW]': 100},
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
                }
        }
        pc = mca.CalibrationProtocol1D(config)

        # if not calibrated yet, setting power should yield a Value error
        with self.assertRaises(ValueError) as context:
            pc.instrument.power = 5
        self.assertTrue('No calibration present' in str(context.exception))

        # remove the database to test creating a new one
        try:
            os.remove(config['database'])
        except:
            pass
        pc.calibrate(wait_time=0)
        # test saving into an existing database
        pc.save_calibration()

        pc.instrument.load_calibration()

        # assert False

    def test_01_Calibrator2D(self):
        try:
            shutil.rmtree('monet/tests/TestData/calibrate')
        except:
            pass
        try:
            os.mkdir('monet/tests/TestData/calibrate')
        except:
            pass

        config = {
            'database': 'monet/tests/TestData/calibrate/power_database.xlsx',
            'index': {
                'name': 'DefaultMicroscope',},
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
                488: {
                    'classpath': 'monet.laser.TestLaser',
                    'init_kwargs': {'port': 'COM4'},
                    },
                561: {
                    'classpath': 'monet.laser.TestLaser',
                    'init_kwargs': {'port': 'COM7'},
                    },
                640: {
                    'classpath': 'monet.laser.TestLaser',
                    'init_kwargs': {'port': 'COM8'},
                    },
                },
            'beampath': {
                'shutter01': {
                    'classpath': 'monet.beampath.TestShutter',
                    'init_kwargs': {'SN': 234}},
                'shutter02': {
                    'classpath': 'monet.beampath.TestShutter',
                    'init_kwargs': {'SN': 456}},
            }
        }
        calibration_protocol = {
            'laser_sequence': [488, 561],
            'laser_powers': {
                488: [100, 200, 500],
                561: [200, 500, 1000],},
            'beampath': {
                488: {'shutter01': True, 'shutter02': True},
                561: {'shutter01': True, 'shutter02': False},
            }
        }
        pc = mca.CalibrationProtocol2D(config, calibration_protocol)

        # if not calibrated yet, setting power should yield a Value error
        with self.assertRaises(ValueError) as context:
            pc.instrument.power = 5
        self.assertTrue('Not calibrated' in str(context.exception))

        # remove the database to test creating a new one
        try:
            os.remove(config['database'])
        except:
            pass
        pc.run_protocol(wait_time=0)

        pc.instrument.load_calibration_database()

        pc.instrument.power = 5

        pc.instrument.power = 89

        pc.instrument.power = 300

        pc.instrument.power = 2000

        assert True
