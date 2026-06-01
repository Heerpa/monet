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
    driver, and a hand-rolled ctypes binding to ``TLPM_64.dll`` proved fragile —
    depending on the installed driver variant the expected symbols (e.g.
    ``TLPM_findRsrc``) may not resolve. We therefore delegate to Thorlabs' own
    ``TLPM.py`` wrapper, which ships with the Optical Power Monitor SDK and
    encapsulates the correct DLL loading. Drop that file into the ``monet``
    package directory (``monet/TLPM.py``) or anywhere on the Python path. It is
    imported lazily, so the package still imports without it.

    The interface mirrors ``ThorlabsPowerMeter`` exactly (``read``, ``wavelength``
    getter/setter, ``unit``), so it is a drop-in replacement selected purely via
    the ``classpath`` config value. Optional config keys: ``address`` (TLPM
    resource name, or 'find connection' to auto-pick the first meter) and
    ``dll_path`` (path to the TLPM_64.dll, or the directory containing it, when
    that DLL is not on the system PATH — Thorlabs' TLPM.py loads it by bare name,
    so its folder must be findable for the DLL and its dependencies to resolve).
    """
    def __init__(self, config):
        self.config = config
        self.pm = None
        # Holds the os.add_dll_directory() handle; the directory is removed from
        # the DLL search path when this handle is GC'd, so it must outlive the
        # DLL load (keep it for the object's lifetime).
        self._dll_dir_handle = None
        self._open_powermeter(
            config.get('address', 'find connection'),
            dll_path=config.get('dll_path'))

    def _import_tlpm_wrapper(self):
        """Import Thorlabs' TLPM wrapper class, preferring the copy vendored
        into the monet package. Separated out so tests can patch it."""
        try:
            from monet.TLPM import TLPM
            return TLPM
        except ImportError:
            pass
        try:
            from TLPM import TLPM
            return TLPM
        except ImportError as exc:
            raise ImportError(
                "Thorlabs' TLPM wrapper could not be imported. Copy TLPM.py "
                "(installed with the Optical Power Monitor SDK, e.g. under "
                "C:\\Program Files\\IVI Foundation\\VISA\\Win64\\TLPM\\Examples"
                "\\Python\\) into the monet package directory (monet/TLPM.py) "
                "or onto the Python path.") from exc

    def _prepare_dll_search(self, dll_path):
        """Make TLPM_64.dll and its dependencies findable when not on PATH.

        Thorlabs' TLPM.py loads the DLL by bare name, so its containing folder
        must be on the DLL search path. ``dll_path`` may point at the DLL file
        or its directory.
        """
        if not dll_path:
            return
        import os
        dll_dir = dll_path if os.path.isdir(dll_path) \
            else os.path.dirname(dll_path)
        if not dll_dir or not os.path.isdir(dll_dir):
            logger.warning('TLPM dll_path directory does not exist: %r',
                           dll_path)
            return
        # Python 3.8+ on Windows: documented way to extend the DLL search path
        # for the DLL and dependencies sitting in the same folder.
        if hasattr(os, 'add_dll_directory'):
            try:
                # Keep the handle: the directory is removed from the search
                # path when the handle is garbage-collected.
                self._dll_dir_handle = os.add_dll_directory(dll_dir)
            except OSError:
                logger.exception('Could not add TLPM dll directory: %s',
                                 dll_dir)
        # Also prepend to PATH so dependent DLLs resolve under older loaders.
        os.environ['PATH'] = dll_dir + os.pathsep + os.environ.get('PATH', '')

    def _open_powermeter(self, address='', dll_path=None):
        """Open communication with the meter through the TLPM wrapper.

        Args:
            address : str
                the TLPM resource name of the meter. If empty or
                'find connection', the first meter found is used.
            dll_path : str or None
                path to TLPM_64.dll or its directory, if not on the PATH.
        """
        import ctypes
        self._prepare_dll_search(dll_path)
        TLPM = self._import_tlpm_wrapper()
        power_meter = TLPM()

        if address and address not in ('', 'find connection'):
            resource = ctypes.create_string_buffer(address.encode())
        else:
            device_count = ctypes.c_uint32(0)
            power_meter.findRsrc(ctypes.byref(device_count))
            if device_count.value == 0:
                raise ValueError(
                    'No Thorlabs TLPM power meter found. Check that the device '
                    'is plugged in, bound to the TLPM driver, and not held open '
                    'by another application (e.g. Optical Power Monitor).')
            resource = ctypes.create_string_buffer(1024)
            power_meter.getRsrcName(ctypes.c_int(0), resource)

        power_meter.open(resource, ctypes.c_bool(True), ctypes.c_bool(True))
        self.pm = power_meter

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

    def close(self):
        """Close the device session, if open."""
        if self.pm is not None:
            try:
                self.pm.close()
            except Exception:
                logger.exception('Error closing Thorlabs TLPM power meter.')
            self.pm = None
