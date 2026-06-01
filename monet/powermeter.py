#!/usr/bin/env python
"""
    monet/powermeter.py
    ~~~~~~~~~~~~~~~~~~~

    Device communication for power measurement.
    Specifically, this module provides functionality to access the
    Thorlabs PM100D via USB.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import pyvisa
# `ThorlabsPM100` is imported lazily inside ThorlabsPowerMeter._open_powermeter
# and `TLPM` lazily inside ThorlabsTLPMPowerMeter._open_powermeter, so the rest
# of the package is usable without those optional SDKs installed.
import abc
import logging
import numpy as np


logger = logging.getLogger(__name__)


class AbstractPowerMeter(abc.ABC):
    pass

    @abc.abstractmethod
    def read(self):
        return

    @property
    @abc.abstractmethod
    def read(self):
        return

    @property
    @abc.abstractmethod
    def wavelength(self):
        return

    @wavelength.setter
    @abc.abstractmethod
    def wavelength(self, value):
        pass


class TestPowerMeter(AbstractPowerMeter):
    """A powermeter for testing purposes
    For testing to be useful, the powermeter must generate an output
    in a pattern. In this case, it is compatible with the sinusoidal
    analyzer
    """
    def __init__(self, config):
        """For sinusoidal output, the config must specify the parameters here
        Args:
            config: keys: bkg, amp, phi, start, step, noise
        """
        self.config = config
        self.pos = config['start']

    def read(self, averaging=10):
        # `averaging` is accepted for interface compatibility with
        # ThorlabsPowerMeter.read (used by feedback control and `measure`);
        # the simulated meter ignores it.
        outval = self._model_function(
            self.pos, self.config['bkg'], self.config['amp'],
            self.config['phi'])
        outval = outval + np.random.normal(loc=0, scale=self.config['noise'])
        self.pos += self.config['step']
        return outval

    def _model_function(self, x, bkg, amp, phi):
        """Squared sinus function with twice the angle, background and offset

        sin**2(alpha) = (1+sin(2*alpha))/2

        P(alpha) = bkg + amp * sin(2*(alpha+phi))**2
                 = bkg + amp * (1 + sin(4*pi/180(alpha+phi)))/2
        Args:
            alpha : float or array
                input angle, in deg
            bkg : float
                background
            amp : float
                amplitude
            phi : float
                angular offset in deg
        Returns:
            result : float or array
                the squared sinus etc.
        """
        return bkg + amp * (1+np.sin(4*np.pi/180*(x+phi)))

    @property
    def wavelength(self):
        return 488

    @wavelength.setter
    def wavelength(self, value):
        pass

    @property
    def unit(self):
        return 'mW'


class ThorlabsPowerMeter(AbstractPowerMeter):
    def __init__(self, config):
        self.pm = self._open_powermeter(config['address'])
        if self.pm is None:
            raise ValueError(
                'Could not connect to Thorlabs power meter '
                '(address: {!r}). '
                'Check that the device is plugged in, drivers are installed, '
                'and no other application has it open.'.format(
                    config['address']))
        self.config = config

    def _open_powermeter(self, address=''):
        """Open the communication with the power meter.

        Args:
            address : str
                the address string of the powermeter. If none is given,
                check resources
            manufacturer : str
                the manufacturer of the 
        Returns:
            power_meter : ThorlabsPM100 instance
                the interface to reading power values
        """
        from ThorlabsPM100 import ThorlabsPM100
        manufacturer='thorlabs'
        power_meter = None
        rm = pyvisa.ResourceManager()

        if address == '' or address == 'find connection':
            resources = rm.list_resources()
            for res in resources:
                try:
                    inst = rm.open_resource(res, timeout=1)
                    assert inst.get_visa_attribute(pyvisa.constants.VI_ATTR_MANF_NAME).lower() == manufacturer
                    power_meter = ThorlabsPM100(inst=inst)
                    break
                except Exception:
                    pass
            if power_meter is None and resources:
                logger.warning(
                    'Thorlabs power meter not found among VISA resources: %s',
                    list(resources))
                # errstr = ('No address given, multiple instruments present. ' +
                #           'This is an unsolved situation.')
                # raise NotImplementedError(errstr)
        else:
            inst = rm.open_resource(address, timeout=1)
            power_meter = ThorlabsPM100(inst=inst)

        return power_meter

    def read(self, averaging=10):
        power = np.mean(np.array([self.pm.read for i in range(averaging)]))
        return power * 1000

    @property
    def wavelength(self):
        return self.pm.sense.correction.wavelength

    @wavelength.setter
    def wavelength(self, value):
        self.pm.sense.correction.wavelength = value

    @property
    def unit(self):
        return 'mW'


class ThorlabsTLPMPowerMeter(AbstractPowerMeter):
    """Power meter accessed through the Thorlabs TLPM driver.

    This is the counterpart to :class:`ThorlabsPowerMeter` for meters that are
    bound to the Thorlabs TLPM/USBTMC driver (the one installed by the "Optical
    Power Monitor" software) rather than the legacy VISA/USBTMC driver. Such
    meters (e.g. the PM100D2, or any PMxxx switched to the TLPM driver) do not
    appear as standard VISA resources, so the pyvisa-based ``ThorlabsPowerMeter``
    cannot open them.

    The interface mirrors ``ThorlabsPowerMeter`` exactly (``read``, ``wavelength``
    getter/setter, ``unit``), so it is a drop-in replacement selected purely via
    the ``classpath`` config value.
    """
    def __init__(self, config):
        self.pm = self._open_powermeter(config.get('address', 'find connection'))
        if self.pm is None:
            raise ValueError(
                'Could not connect to Thorlabs TLPM power meter '
                '(address: {!r}). '
                'Check that the device is plugged in, that it is bound to the '
                'Thorlabs TLPM driver (Optical Power Monitor), and that no other '
                'application has it open.'.format(config.get('address')))
        self.config = config

    def _open_powermeter(self, address=''):
        """Open communication with the meter through the TLPM driver.

        Args:
            address : str
                the TLPM resource name of the meter. If empty or
                'find connection', the first meter found is used.
        Returns:
            power_meter : TLPM instance or None
                the open device handle, or None if no device could be opened.
        """
        import ctypes
        from TLPM import TLPM

        power_meter = TLPM()
        try:
            if address and address not in ('', 'find connection'):
                resource = ctypes.create_string_buffer(address.encode())
            else:
                device_count = ctypes.c_uint32()
                power_meter.findRsrc(ctypes.byref(device_count))
                if device_count.value == 0:
                    logger.warning('No Thorlabs TLPM power meter found.')
                    power_meter.close()
                    return None
                resource = ctypes.create_string_buffer(1024)
                power_meter.getRsrcName(ctypes.c_int(0), resource)
            power_meter.open(resource, ctypes.c_bool(True), ctypes.c_bool(True))
        except Exception:
            logger.exception('Failed to open Thorlabs TLPM power meter.')
            try:
                power_meter.close()
            except Exception:
                pass
            return None

        return power_meter

    def read(self, averaging=10):
        import ctypes
        power = ctypes.c_double()
        vals = []
        for _ in range(averaging):
            self.pm.measPower(ctypes.byref(power))
            vals.append(power.value)
        # TLPM measPower returns watts; convert to mW to match the other meters.
        return float(np.mean(np.array(vals))) * 1000

    @property
    def wavelength(self):
        import ctypes
        wl = ctypes.c_double()
        # attribute 0 (TLPM_ATTR_SET_VAL) reads the configured set value.
        self.pm.getWavelength(ctypes.c_int16(0), ctypes.byref(wl))
        return wl.value

    @wavelength.setter
    def wavelength(self, value):
        import ctypes
        self.pm.setWavelength(ctypes.c_double(float(value)))

    @property
    def unit(self):
        return 'mW'
