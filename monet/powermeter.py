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
    bound to the Thorlabs TLPM driver (the one installed by the "Optical Power
    Monitor" software) rather than the legacy VISA/USBTMC driver. Such meters
    (e.g. the PM100D2, or any PMxxx switched to the TLPM driver) do not appear as
    standard VISA resources, so the pyvisa-based ``ThorlabsPowerMeter`` cannot
    open them.

    Implementation note: there is no maintained pip package wrapping the TLPM
    driver, so rather than depending on Thorlabs' loose ``TLPM.py`` example file
    this class binds the handful of needed functions of ``TLPM_64.dll`` directly
    via ``ctypes``. That DLL is installed by the Optical Power Monitor software;
    nothing extra needs to be pip-installed or copied. The DLL is loaded lazily
    in ``_open_powermeter`` so the package still imports without it.

    The interface mirrors ``ThorlabsPowerMeter`` exactly (``read``, ``wavelength``
    getter/setter, ``unit``), so it is a drop-in replacement selected purely via
    the ``classpath`` config value. Optional config keys: ``address`` (TLPM
    resource name, or 'find connection' to auto-pick the first meter) and
    ``dll_path`` (explicit path to the TLPM DLL if it is not on the system PATH).
    """
    def __init__(self, config):
        self.config = config
        self._dll = None
        # ViSession is a 32-bit handle; 0 is the "no device" handle used for
        # resource enumeration before a meter is opened.
        self._session = None
        self._open_powermeter(
            address=config.get('address', 'find connection'),
            dll_path=config.get('dll_path'))

    def _load_dll(self, dll_path=None):
        """Load TLPM_64.dll (or a caller-supplied DLL). Separated out so tests
        can patch it with a fake DLL."""
        import ctypes
        if dll_path:
            return ctypes.CDLL(dll_path)
        name = 'TLPM_64.dll' if ctypes.sizeof(ctypes.c_void_p) == 8 \
            else 'TLPM_32.dll'
        return ctypes.CDLL(name)

    def _check(self, status, context):
        """Raise on a negative TLPM ViStatus (0 = ok, >0 = warning)."""
        if status >= 0:
            return
        import ctypes
        detail = ''
        try:
            buf = ctypes.create_string_buffer(512)
            self._dll.TLPM_errorMessage(
                self._session, ctypes.c_int(status), buf)
            detail = buf.value.decode(errors='replace').strip()
        except Exception:
            pass
        raise OSError('Thorlabs TLPM {} failed (status {}){}'.format(
            context, status, ': ' + detail if detail else ''))

    def _open_powermeter(self, address='', dll_path=None):
        """Load the DLL and open communication with the meter.

        Args:
            address : str
                the TLPM resource name of the meter. If empty or
                'find connection', the first meter found is used.
            dll_path : str or None
                explicit path to the TLPM DLL, if it is not on the PATH.
        """
        import ctypes
        try:
            self._dll = self._load_dll(dll_path)
        except OSError as exc:
            raise OSError(
                'Could not load the Thorlabs TLPM driver DLL (TLPM_64.dll). '
                'It is installed with the Optical Power Monitor software; '
                'ensure that software is installed and the DLL is on the system '
                "PATH, or set 'dll_path' in the powermeter config.") from exc

        self._session = ctypes.c_ulong(0)

        if address and address not in ('', 'find connection'):
            resource = ctypes.create_string_buffer(address.encode())
        else:
            device_count = ctypes.c_uint32(0)
            self._check(
                self._dll.TLPM_findRsrc(self._session,
                                        ctypes.byref(device_count)),
                'findRsrc')
            if device_count.value == 0:
                raise ValueError(
                    'No Thorlabs TLPM power meter found. Check that the device '
                    'is plugged in, bound to the TLPM driver, and not held open '
                    'by another application (e.g. Optical Power Monitor).')
            resource = ctypes.create_string_buffer(1024)
            self._check(
                self._dll.TLPM_getRsrcName(self._session, ctypes.c_uint32(0),
                                           resource),
                'getRsrcName')

        # The DLL exposes the device-open call as TLPM_open (newer) or
        # TLPM_init (IVI-C standard); both share the same signature.
        open_func = getattr(self._dll, 'TLPM_open', None) \
            or getattr(self._dll, 'TLPM_init', None)
        if open_func is None:
            raise OSError('TLPM DLL exposes neither TLPM_open nor TLPM_init.')
        self._check(
            open_func(resource, ctypes.c_bool(True), ctypes.c_bool(True),
                      ctypes.byref(self._session)),
            'open')

    def read(self, averaging=10):
        import ctypes
        power = ctypes.c_double()
        vals = []
        for _ in range(averaging):
            self._check(
                self._dll.TLPM_measPower(self._session, ctypes.byref(power)),
                'measPower')
            vals.append(power.value)
        # TLPM measPower returns watts; convert to mW to match the other meters.
        return float(np.mean(np.array(vals))) * 1000

    @property
    def wavelength(self):
        import ctypes
        wl = ctypes.c_double()
        # attribute 0 (TLPM_ATTR_SET_VAL) reads the configured set value.
        self._check(
            self._dll.TLPM_getWavelength(self._session, ctypes.c_int16(0),
                                         ctypes.byref(wl)),
            'getWavelength')
        return wl.value

    @wavelength.setter
    def wavelength(self, value):
        import ctypes
        self._check(
            self._dll.TLPM_setWavelength(self._session,
                                         ctypes.c_double(float(value))),
            'setWavelength')

    @property
    def unit(self):
        return 'mW'

    def close(self):
        """Close the device session, if open."""
        if self._dll is not None and self._session is not None:
            try:
                self._dll.TLPM_close(self._session)
            except Exception:
                logger.exception('Error closing Thorlabs TLPM power meter.')
            self._session = None
