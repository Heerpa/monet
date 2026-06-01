"""
    monet/tests/test_powermeter.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Test the powermeter module of monet.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import unittest
from unittest import mock
import monet.powermeter as mpm


class _FakeTLPMDLL:
    """Minimal stand-in for ``TLPM_64.dll``.

    Simulates a single connected meter so ``ThorlabsTLPMPowerMeter`` (which
    binds the DLL functions directly via ctypes) can be exercised without the
    hardware or the Thorlabs driver installed. Each ``TLPM_*`` function writes
    through its byref arguments and returns 0 (ViStatus success), mirroring the
    real DLL. The byref target is reachable via ``._obj`` in CPython.
    """
    _power_w = 0.0123      # 12.3 mW
    _device_count = 1

    def __init__(self):
        self._wavelength = 488.0

    def TLPM_findRsrc(self, session, count_ref):
        count_ref._obj.value = self._device_count
        return 0

    def TLPM_getRsrcName(self, session, index, resource_buffer):
        resource_buffer.value = b'FAKE::TLPM::INSTR'
        return 0

    def TLPM_open(self, resource, id_query, reset, session_ref):
        session_ref._obj.value = 1
        return 0

    def TLPM_measPower(self, session, power_ref):
        power_ref._obj.value = self._power_w
        return 0

    def TLPM_getWavelength(self, session, attribute, wl_ref):
        wl_ref._obj.value = self._wavelength
        return 0

    def TLPM_setWavelength(self, session, value):
        self._wavelength = value.value
        return 0

    def TLPM_close(self, session):
        return 0


class _FakeTLPMDLLNoDevice(_FakeTLPMDLL):
    _device_count = 0


def _patch_dll(dll):
    """Patch ThorlabsTLPMPowerMeter._load_dll to return the given fake DLL."""
    return mock.patch.object(
        mpm.ThorlabsTLPMPowerMeter, '_load_dll', return_value=dll)


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

    def test_basics_02_ThorlabsTLPMPowerMeter(self):
        with _patch_dll(_FakeTLPMDLL()):
            pm = mpm.ThorlabsTLPMPowerMeter({'address': 'find connection'})

            # measPower returns watts; the class reports mW.
            self.assertAlmostEqual(pm.read(averaging=5), 12.3, places=6)
            self.assertEqual(pm.unit, 'mW')

            self.assertAlmostEqual(pm.wavelength, 488.0)
            pm.wavelength = 561
            self.assertAlmostEqual(pm.wavelength, 561.0)

    def test_basics_03_ThorlabsTLPM_no_device_raises(self):
        with _patch_dll(_FakeTLPMDLLNoDevice()):
            with self.assertRaises(ValueError):
                mpm.ThorlabsTLPMPowerMeter({'address': 'find connection'})
