"""
    monet/tests/test_powermeter.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Test the powermeter module of monet.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import sys
import types
import unittest
import monet.powermeter as mpm


class _FakeTLPM:
    """Minimal stand-in for the Thorlabs ``TLPM`` device class.

    Simulates a single connected meter so ``ThorlabsTLPMPowerMeter`` can be
    exercised without the hardware or the TLPM SDK installed.
    """
    _power_w = 0.0123      # 12.3 mW
    _wavelength = 488.0

    def findRsrc(self, count_ref):
        count_ref._obj.value = 1

    def getRsrcName(self, index, resource_buffer):
        resource_buffer.value = b'FAKE::TLPM::INSTR'

    def open(self, resource, id_query, reset):
        pass

    def measPower(self, power_ref):
        power_ref._obj.value = self._power_w

    def getWavelength(self, attribute, wl_ref):
        wl_ref._obj.value = self._wavelength

    def setWavelength(self, value):
        self._wavelength = value.value

    def close(self):
        pass


class _FakeTLPMNoDevice(_FakeTLPM):
    def findRsrc(self, count_ref):
        count_ref._obj.value = 0


def _install_fake_tlpm(device_cls):
    module = types.ModuleType('TLPM')
    module.TLPM = device_cls
    sys.modules['TLPM'] = module


class TestPowerMeter(unittest.TestCase):

    def setUp(self):
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

    def tearDown(self):
        sys.modules.pop('TLPM', None)

    def test_basics_02_ThorlabsTLPMPowerMeter(self):
        _install_fake_tlpm(_FakeTLPM)
        pm = mpm.ThorlabsTLPMPowerMeter({'address': 'find connection'})

        # measPower returns watts; the class reports mW.
        self.assertAlmostEqual(pm.read(averaging=5), 12.3, places=6)
        self.assertEqual(pm.unit, 'mW')

        self.assertAlmostEqual(pm.wavelength, 488.0)
        pm.wavelength = 561
        self.assertAlmostEqual(pm.wavelength, 561.0)

    def test_basics_03_ThorlabsTLPM_no_device_raises(self):
        _install_fake_tlpm(_FakeTLPMNoDevice)
        with self.assertRaises(ValueError):
            mpm.ThorlabsTLPMPowerMeter({'address': 'find connection'})
