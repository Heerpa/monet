#!/usr/bin/env python
"""
    monet/laser.py
    ~~~~~~~~~~~~~~

    This module provides functionality for laser communication.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import logging
from icecream import ic
import os
import abc

import serial
from microscope.lights.toptica import TopticaiBeam

import time
import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


class AbstractLaser(abc.ABC):
    """An abstract class for laser communication.

    Keeps the last set power level
    """
    def __init__(self, warmup_delay):
        """
        Args:
            warmup_delay : scalar
                time delay in seconds to wait for stabilization after
                changing power
        """
        # time to wait after changing power
        self.warmup_delay = warmup_delay
        self.curr_power_set = 0


    @property
    @abc.abstractmethod
    def enabled(self):
        return

    @enabled.setter
    @abc.abstractmethod
    def enabled(self, value):
        pass

    @property
    @abc.abstractmethod
    def power(self):
        return

    @power.setter
    @abc.abstractmethod
    def power(self, power):
        pass

    @property
    def min_power(self):
        pass

    @property
    def max_power(self):
        pass


class TestLaser(AbstractLaser):
    def __init__(self, connection_parameters, warmup_delay=0):
        super().__init__(warmup_delay)
        logger.debug('Simulating Test laser with connection parameters ' + str(connection_parameters))
        self._enabled = False
        self._power = 0

    @property
    def enabled(self):
        logger.debug('Querying enabled state. It is {:s}'.format(
            str(self._enabled)))
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        logger.debug('Setting enabled state to {:s}.'.format(str(value)))
        self._enabled = value

    @property
    def power(self):
        logger.debug('Querying power. It is {:s}'.format(str(self._power)))
        return self._power

    @power.setter
    def power(self, power):
        logger.debug('Setting laser power to {:s}.'.format(str(power)))
        self._power = power


class MPBVFL(AbstractLaser):
    def __init__(self, connection_parameters, warmup_delay=10):
        super().__init__(warmup_delay)
        self.laser = MPBVFL_lowlevel(**connection_parameters)

    @property
    def enabled(self):
        return self.laser.enabled

    @enabled.setter
    def enabled(self, value):
        self.laser.enabled = value

    @property
    def power(self):
        return self.laser.power

    @power.setter
    def power(self, power):
        if self.curr_power_set == power:
            return
        self.curr_power_set = power
        self.laser.power_sp = power
        time.sleep(self.warmup_delay)

    @property
    def min_power(self):
        return self.laser.power_sp_lim[0]

    @property
    def max_power(self):
        return self.laser.power_sp_lim[1]


class MPBVFL_lowlevel(serial.Serial):
    """Low-level implementation of VFL laser communication via serial
    communication

    Args:
        port : str
            the serial port used for the communication.
            Defaults to '/dev/ttyDAQ' (docker renamed)
            on a bare system, use sth like /dev/ttyACM0
        baudrate : int
            the baud rate for serial communication
            Defaults to 115200
        bytesize : int
            the byte size for serial communication
            Defaults to 8
        parity : one of ['N', 'E', 'O', 'M', 'S']
            parity for serial communication.
            N: None, E: Even, O: Odd, M: Mark, S: Space.
            Defaults to N
        stopbits : int
            the # stop bits for serial communication.
            Defaults to 1
        timeout : float
            the timeout for serial communication (in seconds).
            Defaults to 0.2.
    """
    def __init__(self, port='COM10',
                 baudrate=9600, bytesize=8, parity='N',
                 stopbits=1, timeout=1):
        paritydict = {
            'N': serial.PARITY_NONE,
            'E': serial.PARITY_EVEN,
            'O': serial.PARITY_ODD,
            'M': serial.PARITY_MARK,
            'S': serial.PARITY_SPACE
        }
        bytesizedict = {
            5: serial.FIVEBITS,
            6: serial.SIXBITS,
            7: serial.SEVENBITS,
            8: serial.EIGHTBITS
        }
        stopbitsdict = {
            1: serial.STOPBITS_ONE,
            2: serial.STOPBITS_TWO,
            1.5: serial.STOPBITS_ONE_POINT_FIVE
        }
        super().__init__(port=port, baudrate=baudrate,
                         bytesize=bytesizedict[bytesize],
                         parity=paritydict[parity],
                         stopbits=stopbitsdict[stopbits], timeout=timeout)

    @property  # @Feat(read_once=True)
    def idn(self):
        """Identification of the device
        """
        return self.query('GETMODEL')

    @property  # @Feat()
    def status(self):
        """Current device status
        """
        ans = self.query('shlaser')
        return ans.split('\r')

    # ENABLE LASER
    @property  #  Feat(values={True: '1', False: '0'})
    def enabled(self):
        """Method for turning on the laser
        """
        return self.query('GETLDENABLE', values={True: '1', False: '0'})

    @enabled.setter
    def enabled(self, value):
        translation = {
            0: '0',
            1: '1',
            False: '0',
            True: '1',
            '0': '0',
            '1': '1',
            'off': '0',
            'on': '1',
            'OFF': '0',
            'ON': '1'
        }
        value = translation[value]
        self.query('SETLDENABLE ' + value, expectanswer=False)

    # LASER'S CONTROL MODE AND SET POINT

    @property  # @Feat(values={'APC': '1', 'ACC': '0'})
    def ctl_mode(self):
        """To handle laser diode current (mA) in Active Current Control Mode
        """
        return self.query('GETPOWERENABLE', values={'APC': '1', 'ACC': '0'})

    @ctl_mode.setter
    def ctl_mode(self, value):
        self.query('POWERENABLE {}'.format(value), expectanswer=False)

    @property  # @Feat(units='mA')
    def current_sp(self):
        """To handle laser diode current (mA) in Active Current Control Mode
        """
        return float(self.query('GETLDCUR 1'))

    @current_sp.setter
    def current_sp(self, value):
        self.query('SETLDCUR 1 {:.1f}'.format(value), expectanswer=False)

    @property  # @Feat(units='mW')
    def power_sp(self):
        """To handle output power set point (mW) in APC Mode
        """
        return float(self.query('GETPOWER 0'))

    @power_sp.setter
    def power_sp(self, value):
        self.query('SETPOWER 0 {:.0f}'.format(value), expectanswer=False)

    @property  # @Feat(units='mW')
    def power_sp_lim(self):
        """The power set point limits
        """
        return [
            float(self.query('GETPOWERSETPTLIM 1')),
            float(self.query('GETPOWERSETPTLIM 2'))]

    # LASER'S CURRENT STATUS

    @property  # @Feat(units='mW')
    def power(self):
        """To get the laser emission power (mW)
        """
        return float(self.query('POWER 0'))

    @property  # @Feat(units='mA')
    def ld_current(self):
        """To get the laser diode current (mA)
        """
        return float(self.query('LDCURRENT 1'))

    @property  # @Feat(units='degC')
    def ld_temp(self):
        """To get the laser diode temperature (ºC)
        """
        return float(self.query('LDTEMP 1'))

    @property  # @Feat(units='mA')
    def tec_current(self):
        """To get the thermoelectric cooler (TEC) current (mA)
        """
        return float(self.query('TECCURRENT 1'))

    @property  # @Feat(units='degC')
    def tec_temp(self):
        """To get the thermoelectric cooler (TEC) temperature (ºC)
        """
        return float(self.query('TECTEMP 1'))

    # SECOND HARMONIC GENERATOR METHODS

    @property  # @Feat(units='degC')
    def shg_temp_sp(self):
        """To handle the SHG temperature set point
        """
        return float(self.query('GETSHGTEMP'))

    @shg_temp_sp.setter
    def shg_temp_sp(self, value):
        self.query('GETSHGTEMP {:.2f}'.format(value), expectanswer=False)

    @property  # @Feat(units='degC')
    def shg_temp(self):
        """To get the SHG temperature
        """
        return float(self.query('SHGTEMP'))

    @property  # @Feat()
    def shg_tune_info(self):
        """Getting information about laser ready for SHG tuning
        """
        info = self.query('GETSHGTUNERDY').split()
        if info[0] == '0':
            ready = 'Laser not ready for SHG tuning. '
        else:
            ready = 'Laser ready for SHG tuning. '

        schedule = 'Next SHG tuning scheduled in {} '.format(info[1])
        schedule += 'hours of operation. '
        warm = 'Warm-up period expires in {} seconds.'.format(info[2])

        ans = ready + schedule + warm
        return ans

    @property  # @Feat()
    def shg_tuning(self):
        """Initiating SHG tuning
        """
        state = self.query('GETSHGTUNESTATE').split()
        if state[0] == '0':
            tuning = 'No SHG tuning performed since last reset. '
        elif state[0] == '3':
            tuning = 'SHG tuning in progress. '
        elif state[0] == '1':
            tuning = 'SHG tuning completed successfully. '
        elif state[0] == '2':
            tuning = 'SHG tuning aborted. '

        if state[1] == '0':
            error = 'No error detected.'
        elif state[1] == '1':
            error = 'Error: Laser not running in APC.'
        elif state[1] == '8':
            error = 'Error: Output Power not stabilized.'

        return tuning + error

    #@Action()
    def tune_shg(self):
        self.query('SETSHGCMD 1')

    #@Action()
    def tune_shg_stop(self):
        self.query('SETSHGCMD 2')

    def query(self, cmd, values=None, expectanswer=True):
        '''send and receive the answer

        Args:
            cmd : byte string
                the command to send. necessary end-of-command syntax will
                be appended
            values : dict
                conversion of possible return values.
                    keys: required outputs of this query function
                    values: expected serial answers
            expectanswer : bool
                whether or not to wait for an answer
        '''
        if self.in_waiting:
            self.reset_input_buffer()
        self.write(cmd.encode()+b'\r')

        answer = self.read_until()
        answer = answer.decode().split('\rD')[0]

        if values is not None:
            valrev = {v: k for k, v in values.items()}
            answer = valrev[answer]
        return answer


class Toptica(AbstractLaser):
    """
    """
    def __init__(self, connection_parameters, warmup_delay=10):
        """
        Args:
            connection_parameters
        """
        super().__init__(warmup_delay)
        self.las = TopticaiBeam(**connection_parameters)
        # enable the channels, switch off laser, just to be safe
        self.las._conn.command(b'en 1')
        self.las._conn.command(b'en 2')
        self.enabled = False

    @property
    def enabled(self):
        return self.las.get_is_on()

    @enabled.setter
    def enabled(self, value):
        if value==True:
            self.las.enable()
            time.sleep(self.warmup_delay)
        elif value==False:
            self.las.disable()
        else:
            raise ValueError('value must be bool, but is {:s}'.format(str(value)))

    @property
    def power(self):
        return self._get_power()

    @power.setter
    def power(self, power):
        if power != self.curr_power_set:
            self._set_power(power)
            time.sleep(self.warmup_delay)

    def _set_power(self, power):
        self.curr_power_set = power
        return self.las._set_power_mw(power)

    def _get_power(self):
        '''Get the power in mW
        '''
        self.las._get_power_mw(power)

    @property
    def min_power(self):
        return 0

    @property
    def max_power(self):
        return self.las._max_power

    def close(self):
        self.las._conn._serial._serial.close()
