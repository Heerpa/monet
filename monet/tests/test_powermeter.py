"""
    monet/tests/test_powermeter.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Test the powermeter module of monet.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import unittest
import monet.powermeter as mpm


class TestPowerMeter(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_basics_01_TestPowerMeter(self):
        config = {
            'bkg': 0,
            'amp': 50,
            'phi': 30,
            'start': 10,
            'step': 5,
            'noise': 3}
        att = mpm.TestPowerMeter(config)

        for i in range(20):
            print(att.read())

        assert True
