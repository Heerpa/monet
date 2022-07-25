"""
    monet/tests/test_beampath.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Test the beam path module of monet.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import unittest
import monet.calibrate as mca
import numpy as np
import os
import shutil

import monet.beampath as mbp


class TestBeampath(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_01_TestShutter(self):

        shutterpars = {'port': 'COM4'}

        sh = mbp.TestShutter(shutterpars)

        print(sh.position)
        sh.position = True
        print(sh.position)

    def test_02_TestBeamPath(self):
        bp_config = {
            'shutter01': {
                'classpath': 'monet.beampath.TestShutter',
                'init_kwargs': {'SN': 1234},},
        }
        bp_settings = {
            'A': {'shutter01': True},
            'B': {'shutter01': False}
        }

        bp = mbp.BeamPath(bp_config)

        bp.positions = bp_settings['A']
        bp.positions = bp_settings['B']
