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
from ThorlabsPM100 import ThorlabsPM100
import abc
import numpy as np


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

    def read(self):
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
        self.config = config

    def _open_powermeter(self, address=''):
        """Open the communication with the power meter.

        Args:
            address : str
                the address string of the powermeter. If none is given,
                check resources
        Returns:
            power_meter : ThorlabsPM100 instance
                the interface to reading power values
        """
        rm = pyvisa.ResourceManager()

        if address == '' or address == 'find connection':
            resources = rm.list_resources()
            for res in resources:
                try:
                    inst = rm.open_resource(res, timeout=1)
                    power_meter = ThorlabsPM100(inst=inst)
                    break
                except: # if it didn't work, it raises an error
                    pass
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
