#!/usr/bin/env python
"""
monet/laser.py
~~~~~~~~~~~~~~

This module provides functionality for laser communication.

:authors: Heinrich Grabmayr, 2022
:copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""

import abc
import logging
import re
import time

import serial
from icecream import ic

# `microscope.lights.toptica` (TopticaiBeam) and `pycobolt` are imported
# lazily inside the classes that need them (Toptica_Old / Cobolt /
# Cobolt_OEM), so the rest of the package is usable on machines without
# those hardware SDKs.


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


class AbstractLaser(abc.ABC):
    """An abstract class for laser communication.

    Keeps the last set power level
    """

    def __init__(self, warmup_delay):
        """Initialize the laser.

        Parameters
        ----------
        warmup_delay : scalar
            Time delay in seconds to wait for stabilization after changing
            power.
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
        logger.debug(
            'Simulating Test laser with connection parameters '
            + str(connection_parameters)
        )
        self._enabled = False
        self._power = 0

    @property
    def enabled(self):
        logger.debug(
            'Querying enabled state. It is {:s}'.format(str(self._enabled))
        )
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
    def __init__(self, connection_parameters, warmup_delay=0.1):
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

    Parameters
    ----------
    port : str
        The serial port used for the communication. Defaults to
        '/dev/ttyDAQ' (docker renamed); on a bare system use something
        like /dev/ttyACM0.
    baudrate : int
        The baud rate for serial communication. Defaults to 115200.
    bytesize : int
        The byte size for serial communication. Defaults to 8.
    parity : one of ['N', 'E', 'O', 'M', 'S']
        Parity for serial communication. N: None, E: Even, O: Odd,
        M: Mark, S: Space. Defaults to N.
    stopbits : int
        The number of stop bits for serial communication. Defaults to 1.
    timeout : float
        The timeout for serial communication (in seconds). Defaults to 0.2.
    """

    def __init__(
        self,
        port='COM10',
        baudrate=9600,
        bytesize=8,
        parity='N',
        stopbits=1,
        timeout=1,
    ):
        paritydict = {
            'N': serial.PARITY_NONE,
            'E': serial.PARITY_EVEN,
            'O': serial.PARITY_ODD,
            'M': serial.PARITY_MARK,
            'S': serial.PARITY_SPACE,
        }
        bytesizedict = {
            5: serial.FIVEBITS,
            6: serial.SIXBITS,
            7: serial.SEVENBITS,
            8: serial.EIGHTBITS,
        }
        stopbitsdict = {
            1: serial.STOPBITS_ONE,
            2: serial.STOPBITS_TWO,
            1.5: serial.STOPBITS_ONE_POINT_FIVE,
        }
        super().__init__(
            port=port,
            baudrate=baudrate,
            bytesize=bytesizedict[bytesize],
            parity=paritydict[parity],
            stopbits=stopbitsdict[stopbits],
            timeout=timeout,
        )

    @property  # @Feat(read_once=True)
    def idn(self):
        """Identification of the device"""
        return self.query('GETMODEL')

    @property  # @Feat()
    def status(self):
        """Current device status"""
        ans = self.query('shlaser')
        return ans.split('\r')

    # ENABLE LASER
    @property  # Feat(values={True: '1', False: '0'})
    def enabled(self):
        """Method for turning on the laser"""
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
            'ON': '1',
        }
        value = translation[value]
        self.query('SETLDENABLE ' + value, expectanswer=False)

    # LASER'S CONTROL MODE AND SET POINT

    @property  # @Feat(values={'APC': '1', 'ACC': '0'})
    def ctl_mode(self):
        """To handle laser diode current (mA) in Active Current Control Mode"""
        return self.query('GETPOWERENABLE', values={'APC': '1', 'ACC': '0'})

    @ctl_mode.setter
    def ctl_mode(self, value):
        self.query('POWERENABLE {}'.format(value), expectanswer=False)

    @property  # @Feat(units='mA')
    def current_sp(self):
        """To handle laser diode current (mA) in Active Current Control Mode"""
        return float(self.query('GETLDCUR 1'))

    @current_sp.setter
    def current_sp(self, value):
        self.query('SETLDCUR 1 {:.1f}'.format(value), expectanswer=False)

    @property  # @Feat(units='mW')
    def power_sp(self):
        """To handle output power set point (mW) in APC Mode"""
        return float(self.query('GETPOWER 0'))

    @power_sp.setter
    def power_sp(self, value):
        self.query('SETPOWER 0 {:.0f}'.format(value), expectanswer=False)

    @property  # @Feat(units='mW')
    def power_sp_lim(self):
        """The power set point limits"""
        setptlims = self.query('GETPOWERSETPTLIM 1').split(' ')
        # setptlims = self.query('GETPOWERSETPTLIM 2').split(' ')
        return [float(setptlims[0]), float(setptlims[1])]

    # LASER'S CURRENT STATUS

    @property  # @Feat(units='mW')
    def power(self):
        """To get the laser emission power (mW)"""
        return float(self.query('POWER 0'))

    @property  # @Feat(units='mA')
    def ld_current(self):
        """To get the laser diode current (mA)"""
        return float(self.query('LDCURRENT 1'))

    @property  # @Feat(units='degC')
    def ld_temp(self):
        """To get the laser diode temperature (ºC)"""
        return float(self.query('LDTEMP 1'))

    @property  # @Feat(units='mA')
    def tec_current(self):
        """To get the thermoelectric cooler (TEC) current (mA)"""
        return float(self.query('TECCURRENT 1'))

    @property  # @Feat(units='degC')
    def tec_temp(self):
        """To get the thermoelectric cooler (TEC) temperature (ºC)"""
        return float(self.query('TECTEMP 1'))

    # SECOND HARMONIC GENERATOR METHODS

    @property  # @Feat(units='degC')
    def shg_temp_sp(self):
        """To handle the SHG temperature set point"""
        return float(self.query('GETSHGTEMP'))

    @shg_temp_sp.setter
    def shg_temp_sp(self, value):
        self.query('GETSHGTEMP {:.2f}'.format(value), expectanswer=False)

    @property  # @Feat(units='degC')
    def shg_temp(self):
        """To get the SHG temperature"""
        return float(self.query('SHGTEMP'))

    @property  # @Feat()
    def shg_tune_info(self):
        """Getting information about laser ready for SHG tuning"""
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
        """Initiating SHG tuning"""
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

    # @Action()
    def tune_shg(self):
        self.query('SETSHGCMD 1')

    # @Action()
    def tune_shg_stop(self):
        self.query('SETSHGCMD 2')

    def query(self, cmd, values=None, expectanswer=True):
        '''Send a command and receive the answer.

        Parameters
        ----------
        cmd : byte string
            The command to send. Necessary end-of-command syntax will be
            appended.
        values : dict
            Conversion of possible return values. Keys are the required
            outputs of this query function and values the expected serial
            answers.
        expectanswer : bool
            Whether to wait for an answer.
        '''
        if self.in_waiting:
            self.reset_input_buffer()
        self.write(cmd.encode() + b'\r')

        answer = self.read_until()
        answer = answer.decode().split('\rD')[0]

        if values is not None:
            valrev = {v: k for k, v in values.items()}
            answer = valrev[answer]
        return answer


class Toptica_Old(AbstractLaser):
    """ """

    def __init__(self, connection_parameters, warmup_delay=1):
        """Initialize the laser.

        Parameters
        ----------
        connection_parameters : dict
            Connection settings passed to the low-level driver.
        """
        super().__init__(warmup_delay)
        from microscope.lights.toptica import TopticaiBeam

        self.las = TopticaiBeam(**connection_parameters)
        # enable the channels, switch off laser, just to be safe
        self.las._conn.command(b'en 1')
        self.las._conn.command(b'en 2')
        # self.enabled = False

    @property
    def enabled(self):
        return self.las.get_is_on()

    @enabled.setter
    def enabled(self, value):
        if value is True:
            self.las.enable()
            time.sleep(self.warmup_delay)
        elif value is False:
            if hasattr(self.las, '_conn'):
                self.las.disable()
        else:
            raise ValueError(
                'value must be bool, but is {:s}'.format(str(value))
            )

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
        '''Get the power in mW'''
        return self.las._get_power_mw()

    @property
    def min_power(self):
        return 0

    @property
    def max_power(self):
        return self.las._max_power

    def close(self):
        try:
            self.las._conn._serial._serial.close()
        except Exception:
            pass


class Toptica(AbstractLaser):
    """ """

    def __init__(self, connection_parameters, warmup_delay=0.1):
        """Initialize the laser.

        Parameters
        ----------
        connection_parameters : dict
            Connection settings passed to the low-level driver.
        """
        super().__init__(warmup_delay)
        self.las = Toptica_lowlevel(**connection_parameters)
        self.las.set_enabled(True)
        # self.enabled = False

    @property
    def enabled(self):
        return self.las.get_enabled()

    @enabled.setter
    def enabled(self, value):
        if value is True:
            self.las.set_enabled(True)
            time.sleep(self.warmup_delay)
        elif value is False:
            self.las.set_enabled(False)
        else:
            raise ValueError(
                'value must be bool, but is {:s}'.format(str(value))
            )

    @property
    def power(self):
        return self.las.get_power()

    @power.setter
    def power(self, power):
        if power != self.curr_power_set:
            self.las.set_power(power)
            time.sleep(self.warmup_delay)

    @property
    def min_power(self):
        return 0

    @property
    def max_power(self):
        return None

    def __del__(self):
        if hasattr(self, 'las'):
            try:
                self.las.close()
            except Exception:
                pass


class Toptica_lowlevel(serial.Serial):
    """Low-level implementation of Toptica iBeam laser
    communication via serial communication

    Parameters
    ----------
    port : str
        The serial port used for the communication. Defaults to
        '/dev/ttyDAQ' (docker renamed); on a bare system use something
        like /dev/ttyACM0.
    baudrate : int
        The baud rate for serial communication. Defaults to 115200.
    bytesize : int
        The byte size for serial communication. Defaults to 8.
    parity : one of ['N', 'E', 'O', 'M', 'S']
        Parity for serial communication. N: None, E: Even, O: Odd,
        M: Mark, S: Space. Defaults to N.
    stopbits : int
        The number of stop bits for serial communication. Defaults to 1.
    timeout : float
        The timeout for serial communication (in seconds). Defaults to 0.2.
    """

    def __init__(
        self,
        port='COM10',
        baudrate=115200,
        bytesize=8,
        parity='N',
        stopbits=1,
        timeout=1,
    ):
        paritydict = {
            'N': serial.PARITY_NONE,
            'E': serial.PARITY_EVEN,
            'O': serial.PARITY_ODD,
            'M': serial.PARITY_MARK,
            'S': serial.PARITY_SPACE,
        }
        bytesizedict = {
            5: serial.FIVEBITS,
            6: serial.SIXBITS,
            7: serial.SEVENBITS,
            8: serial.EIGHTBITS,
        }
        stopbitsdict = {
            1: serial.STOPBITS_ONE,
            2: serial.STOPBITS_TWO,
            1.5: serial.STOPBITS_ONE_POINT_FIVE,
        }
        super().__init__(
            port=port,
            baudrate=baudrate,
            bytesize=bytesizedict[bytesize],
            parity=paritydict[parity],
            stopbits=stopbitsdict[stopbits],
            timeout=timeout,
        )
        self.query('channel 1 power {:d} micro'.format(0))

    # ENABLE LASER
    def set_enabled(self, value):
        translation = {
            0: False,
            1: True,
            False: False,
            True: True,
            '0': False,
            '1': True,
            'off': False,
            'on': True,
            'OFF': False,
            'ON': True,
        }
        value = translation[value]

        if value:
            self.query('laser on', expectanswer=True)
            self.query('enable 1')
            self.query('enable 2')
        else:
            self.query('laser off', expectanswer=True)
            self.query('disable 1')
            self.query('disable 2')

    def get_enabled(self):
        answer = self.query('show ch 1')
        answer = ';'.join(answer)
        if 'status: on' in answer:
            return True
        else:
            return False
        return self.query('show ch 1')

    def set_power(self, value):
        """Set the power in milliwatt.

        Parameters
        ----------
        value : int
            The power in mW (precision down to µW).
        """
        if not isinstance(value, int) and not isinstance(value, float):
            raise ValueError(
                'Power needs to be specified as an integer mW value. '
                'Not {:s}'.format(str(value))
            )
        # chan1pwr = min([int(1e3*value), 100000])
        # self.query('channel 1 power {:d} micro'.format(chan1pwr))
        self.query('channel 2 power {:d} micro'.format(int(1e3 * value)))

    def get_power(self):
        """Query the current power.

        Returns
        -------
        value : int
            The current power in mW.
        """
        value = self.query('show level power')
        # print('got values', value)
        powers = {}
        for ln in value:
            m = re.search(
                r"CH(\d+),\s*PWR:\s*([\d.]+)\s*(mW|uW)",
                ln,
                flags=re.IGNORECASE,
            )
            if m:
                p = float(m[2]) * (1 if m[3].lower() == "mw" else 1e-3)
                powers[int(m[1])] = p
        return max(powers.values())

    def query(self, cmd, values=None, expectanswer=True):
        '''Send a command and receive the answer.

        Parameters
        ----------
        cmd : byte string
            The command to send. Necessary end-of-command syntax will be
            appended.
        values : dict
            Conversion of possible return values. Keys are the required
            outputs of this query function and values the expected serial
            answers.
        expectanswer : bool
            Whether to wait for an answer.
        '''
        if self.in_waiting:
            self.reset_input_buffer()
        time.sleep(0.03)
        self.write(cmd.encode() + b'\r')
        time.sleep(0.03)

        answer = self.read_until('CMD>')
        time.sleep(0.03)
        all_answers = answer.decode().split('\r')

        # all_answers = []
        # for i in range(10):
        #     answer = self.read_until()
        #     if answer == b'':
        #         break
        #     #print('got answer', answer)
        #     answer = answer.decode().split('\rD')[0]
        #     #print('decoded to ', answer)
        #     all_answers = all_answers + [answer]
        #     #if isinstance(answer, list):
        #     #    all_answers = all_answers + answer
        #     #elif isinstance(answer, str):
        #     #    all_answers = all_answers + [answer]
        #     #print('all answers', all_answers)
        # #answer = self.read_until()
        # #print('got answer', answer)
        # #answer = answer.decode().split('\rD')[0]
        # #print('got answer', answer)

        # if values is not None:
        #     valrev = {v: k for k, v in values.items()}
        #     answer = valrev[answer]

        # return all_answers
        if len(all_answers) == 1:
            return all_answers[0]
        else:
            return all_answers


class LaserQuantum(AbstractLaser):
    def __init__(self, connection_parameters, warmup_delay=5):
        super().__init__(warmup_delay)
        self.laser = LaserQuantum_lowlevel(**connection_parameters)
        self.laser.control_mode('power')

    @property
    def enabled(self):
        status = self.laser.get_status()
        if status:
            return True
        else:
            return False

    @enabled.setter
    def enabled(self, value):
        self.laser.set_enabled(value)

    @property
    def power(self):
        return self.laser.get_power()

    @power.setter
    def power(self, power):
        self.laser.set_power(power)
        time.sleep(self.warmup_delay)

    @property
    def min_power(self):
        return None

    @property
    def max_power(self):
        return None


class LaserQuantum_lowlevel(serial.Serial):
    """Low-level implementation of "Laser Quantum" laser
    communication via serial communication
    https://novantaphotonics.com/wp-content/uploads/2022/05/gem_with_smd12_Novanta_Product_Manual.pdf

    Parameters
    ----------
    port : str
        The serial port used for the communication. Defaults to
        '/dev/ttyDAQ' (docker renamed); on a bare system use something
        like /dev/ttyACM0.
    baudrate : int
        The baud rate for serial communication. Defaults to 115200.
    bytesize : int
        The byte size for serial communication. Defaults to 8.
    parity : one of ['N', 'E', 'O', 'M', 'S']
        Parity for serial communication. N: None, E: Even, O: Odd,
        M: Mark, S: Space. Defaults to N.
    stopbits : int
        The number of stop bits for serial communication. Defaults to 1.
    timeout : float
        The timeout for serial communication (in seconds). Defaults to 0.2.
    """

    def __init__(
        self,
        port='COM10',
        baudrate=9600,
        bytesize=8,
        parity='N',
        stopbits=1,
        timeout=1,
    ):
        paritydict = {
            'N': serial.PARITY_NONE,
            'E': serial.PARITY_EVEN,
            'O': serial.PARITY_ODD,
            'M': serial.PARITY_MARK,
            'S': serial.PARITY_SPACE,
        }
        bytesizedict = {
            5: serial.FIVEBITS,
            6: serial.SIXBITS,
            7: serial.SEVENBITS,
            8: serial.EIGHTBITS,
        }
        stopbitsdict = {
            1: serial.STOPBITS_ONE,
            2: serial.STOPBITS_TWO,
            1.5: serial.STOPBITS_ONE_POINT_FIVE,
        }
        super().__init__(
            port=port,
            baudrate=baudrate,
            bytesize=bytesizedict[bytesize],
            parity=paritydict[parity],
            stopbits=stopbitsdict[stopbits],
            timeout=timeout,
        )

    # ENABLE LASER
    def set_enabled(self, value):
        translation = {
            0: False,
            1: True,
            False: False,
            True: True,
            '0': False,
            '1': True,
            'off': False,
            'on': True,
            'OFF': False,
            'ON': True,
        }
        value = translation[value]

        if value:
            self.query('ON', expectanswer=True)
        else:
            self.query('OFF', expectanswer=True)

    def control_mode(self, value='power'):
        """Set the control mode to one of 'power' or 'current'"""
        value = value.upper()
        options = ['POWER', 'CURRENT']
        if value not in options:
            raise ValueError(
                'Control Mode {:s} is not implemented.'.format(value)
                + ' use one of {:s}.'.format(str(options))
            )
        self.query('CONTROL={:s}'.format(value))

    def set_current(self, value):
        """Set the current to a specified percentage.

        Parameters
        ----------
        value : int
            Percentage of current used.
        """
        if (not isinstance(value, int)) or value < 0 or value > 100:
            raise ValueError(
                'Current percentage must be an integer between 0 and 100. '
                'Not {:s}'.format(str(value))
            )
        self.query('CURRENT={:d}'.format(value))

    def set_power(self, value):
        """Set the power in milliwatt.

        Parameters
        ----------
        value : int
            The power in mW.
        """
        if not isinstance(value, int):
            raise ValueError(
                'Power needs to be specified as an integer mW value. '
                'Not {:s}'.format(str(value))
            )
        self.query('POWER={:d}'.format(value))

    def get_power(self):
        """Query the current power.

        Returns
        -------
        value : int
            The current power in mW.
        """
        return self.query('POWER?')

    def sten(self, value):
        """Set enable on startup.

        Parameters
        ----------
        value : bool
            Whether to enable laser emission at startup.
        """
        if not isinstance(value, bool):
            raise ValueError(
                'value must be a bool, not {:s}'.format(str(value))
            )
        if value:
            value = 'YES'
        else:
            value = 'NO'
        self.query('STEN={:s}'.format(value))
        self.query('WRITE')

    def stpow(self, value):
        """Set power on startup.

        Parameters
        ----------
        value : int
            The startup power value.
        """
        if not isinstance(value, int):
            raise ValueError(
                'value must be an int, not {:s}'.format(str(value))
            )
        self.query('STPOW={:d}'.format(value))
        self.query('WRITE')

    def get_laser_temp(self):
        """Get the temperature at the laser head.

        Returns
        -------
        temp : int
            Temperature in centigrade.
        """
        return self.query('LASTEMP?')

    def get_psu_temp(self):
        """Get the temperature at the PSU.

        Returns
        -------
        temp : int
            Temperature in centigrade.
        """
        return self.query('PSUTEMP?')

    def get_status(self):
        """Get the status of the interlock"""
        return self.query('STATUS?')

    def get_timers(self):
        """Get the timers of laser and PSU:
        Time=#######.# Total time the system has been powered
        Laser Time=#######.# Total time the diodes have been powered
        Laser > 1A Time=#######.# Total time the diodes have been powered >1 A
        """
        return self.query('TIMERS?')

    def get_version(self):
        """Get the firmware version"""
        return self.query('VERSION?')

    def query(self, cmd, values=None, expectanswer=True):
        '''Send a command and receive the answer.

        Parameters
        ----------
        cmd : byte string
            The command to send. Necessary end-of-command syntax will be
            appended.
        values : dict
            Conversion of possible return values. Keys are the required
            outputs of this query function and values the expected serial
            answers.
        expectanswer : bool
            Whether to wait for an answer.
        '''
        if self.in_waiting:
            self.reset_input_buffer()
        self.write(cmd.encode() + b'\r')

        answer = self.read_until()
        answer = answer.decode().split('\rD')[0]

        if values is not None:
            valrev = {v: k for k, v in values.items()}
            answer = valrev[answer]
        return answer


class Cobolt(AbstractLaser):
    """Implementation of the Cobolt lasers.

    Cobolt lasers come in two flavours that need different ways of switching
    the emission off, selected via the ``has_key`` argument:

    * Keyed lasers (``has_key=True``, the default): with autostart enabled, a
      software ``turn_off`` (``l0``) requires physically cycling the key switch
      before the laser can be turned on again. To avoid that, the laser is kept
      logically 'on' and the beam is extinguished by entering constant-current
      mode at 0 mA (below the lasing threshold).
    * OEM / keyless lasers (``has_key=False``): emission can be toggled
      fully in software, so disabling calls ``turn_off`` and enabling
      calls ``turn_on``.

    In both cases the laser is left dark after construction and is reliably
    switched off again when the object is destroyed (see ``__del__``).
    """

    def __init__(self, connection_parameters, warmup_delay=0.1, has_key=True):
        """Initialize the Cobolt laser.

        Parameters
        ----------
        connection_parameters : dict
            Connection settings with keys ``port`` (str, the COM port to
            use), ``serialnumber`` (str, optional, can be used instead of
            port) and ``baudrate`` (int, the baud rate; default 115200).
        warmup_delay : scalar
            Time delay in seconds to wait for stabilization after changing
            power.
        has_key : bool
            Whether the laser has a physical key switch (autostart). If
            True, the beam is extinguished via ``constant_current(0)`` so
            that re-enabling does not require cycling the key. If False
            (OEM lasers), emission is toggled with ``turn_off()`` /
            ``turn_on()``.
        """
        super().__init__(warmup_delay)
        import pycobolt

        self.has_key = has_key
        self.laser = pycobolt.CoboltLaser(**connection_parameters)
        self.laser.constant_power()
        self.laser.turn_on()
        if has_key:
            print(
                'please enable the Cobolt laser by switching the key. '
                + str(connection_parameters)
            )
        self._power = 0
        # Start in a defined, dark state so the laser is never emitting until
        # it is explicitly enabled.
        self._enabled = True
        self.enabled = False

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        self._enabled = value
        if value:
            if not self.has_key:
                # keyless laser: emission was fully turned off, re-arm it
                self.laser.turn_on()
            # leave the current-off / off state and restore the set power
            self.laser.constant_power()
            self.power = self._power
        else:
            self.laser.set_power(0)
            if self.has_key:
                # A software turn_off would require cycling the key before the
                # laser can be turned on again. Instead drop the current below
                # the lasing threshold so the beam is dark while the laser
                # stays logically on.
                self.laser.constant_current(0)
            else:
                # OEM / keyless laser: switch emission off entirely.
                self.laser.turn_off()

    @property
    def power(self):
        return self.laser.get_power()

    @power.setter
    def power(self, power):
        self.laser.set_power(power)
        self._power = power

    @property
    def min_power(self):
        return None

    @property
    def max_power(self):
        return None

    def __del__(self):
        # Make sure the beam is switched off before the connection is dropped,
        # so quitting the program never leaves the laser emitting.
        try:
            self.enabled = False
        except Exception:
            pass
        try:
            self.laser.disconnect()
        except Exception:
            pass


class Cobolt_OEM(Cobolt):
    """OEM Cobolt laser without a key switch.

    Emission is toggled entirely in software (``turn_on`` / ``turn_off``), so
    no key needs to be cycled. Kept as a thin subclass for backward-compatible
    configs that reference ``monet.laser.Cobolt_OEM``; equivalent to
    ``Cobolt(..., has_key=False)``.
    """

    def __init__(self, connection_parameters, warmup_delay=0.1):
        super().__init__(connection_parameters, warmup_delay, has_key=False)
