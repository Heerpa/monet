"""
    monet/tests/test_laser.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~

    Test the lasermodule of monet.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import unittest
import monet.calibrate as mca
import numpy as np
import os
import shutil

import monet.laser as mlas


class TestLaser(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_01_TestLaser(self):

        laserpars = {'port': 'COM4'}
        warmup_delay = 5

        ls = mlas.TestLaser(laserpars, warmup_delay)

        ls.enabled
        ls.enabled = True
        ls.enabled

        ls.power = 8
        ls.power
