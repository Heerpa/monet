"""
    monet/tests/test_control.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Test the control module of monet.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import unittest
import monet.calibrate as mca
import numpy as np
import os
import shutil

import monet.control as mco


class TestControl(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_01_IlluminationController(self):
        try:
            os.mkdir('monet/tests/TestData/calibrate')
        except:
            pass

        config = {
            'database': 'monet/tests/TestData/control/power_database.xlsx',
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
        ctrl = mco.IlluminationController(config)

        print(ctrl.laser)

        ctrl.laser = '561'

        ctrl.power = 50
        ctrl.power = 200

        assert False
