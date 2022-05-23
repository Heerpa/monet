"""
    monet/tests/test_attenuation.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Test the attenuation module of monet.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import unittest
import monet.attenuation as mat


class TestAttenuation(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_basics_01_TestAttenuator(self):
        config = {
            'test': 'config',}
        att = mat.TestAttenuator(config)

        att._connect()
        att._wait()
        att._home()
        att._log_pos()
        att._move_absolute(5)
        att._move_relative(5)
        att.set(5)
