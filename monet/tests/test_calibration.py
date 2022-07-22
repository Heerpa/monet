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

    def test_01_Calibrator(self):
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
