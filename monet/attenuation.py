#!/usr/bin/env python
"""
    monet/attenuation.py
    ~~~~~~~~~~~~~~~~~~~~

    Device communication for power attenuation.
    Specifically, this module provides functionality to rotate the half-wave
    plate in front of the polarizing beam splitter.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import logging
from icecream import ic
import abc
import time
import os
import serial

from msl.equipment import EquipmentRecord, ConnectionRecord, Backend
from msl.equipment.resources.thorlabs import MotionControl


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


class AbstractAttenuator(abc.ABC):
    """An abstract class for laser power attenuation.
    Both device communication and power curve fitting analysis are
    taken care of in this class
    """
    def __init__(self, attenuation_config):
        """Initialize the class, and connect to the attenuation device.

        Args:
            attenuation_config : dict
                parameters for device communication and measurement settings
        """
        self.config = attenuation_config
        self.device = self._connect()

    @abc.abstractmethod
    def _connect(self):
        return None

    @abc.abstractmethod
    def set(self, val):
        pass

    @abc.abstractmethod
    def curr_pos(self):
        pass

    @abc.abstractmethod
    def home(self):
        pass


class TestAttenuator(AbstractAttenuator):
    """Implementation of a Attenuator for testing purposes
    """
    def __init__(self, attenuation_config):
        super().__init__(attenuation_config)

    def _connect(self):
        """
        """
        logger.debug('Simulate connecting.')
        return None

    def _wait(self):
        """Wait for the Kinesis to have moved.
        """
        logger.debug('simulate waiting.')

    def home(self):
        """Home the device
        """
        logger.debug('Simulate homing...')

    def _log_pos(self):
        """Logs the current position
        """
        pass

    def curr_pos(self):
        return 0        

    def _move_absolute(self, pos):
        """Move to an absolute position

        Args:
            pos : int
                the position to move to in internal steps
        """
        logger.debug('Simulate moving to {:d}...'.format(pos))
        self._log_pos()

    def _move_relative(self, step):
        """Move by a relative step

        Args:
            step : int
                the step in internal units
        """
        logger.debug('Simulate moving by {:d}...'.format(step))

    def set(self, val):
        """Set a value. Called from outside, calls a specific function.

        Args:
            val : float
                the value to set to.
        """
        logger.debug('simulate setting value {:f}'.format(val))

    def __del__(self):
        pass


class KinesisAttenuator(AbstractAttenuator):
    """Implementation of the AbstractAttenuator using a Thorlabs Kinesis
    rotation mount, rotating a half-wave plate followed in the beam path
    by a polarizing beam splitter cube to modulate the laser power output.
    """
    def __init__(self, attenuation_config, wait_after_move=.5):
        super().__init__(attenuation_config)
        self.wait_after_move = wait_after_move

    def _connect(self):
        """Connect to a Thorlabs Kinesis rotary stage.
        For now, KDC101 with servo motor is supported. For different versions,
        add more ConnectionRecord possibilities.

        Attributes used:
            config: dict, as saved in AbstractAttenuator.__init__
                parameters for attenuation device communication, as well as
                analysis
        returns:
            motor : msl Equipment
                interface to move the Kinesis rotator.
        """
        # ensure that the Kinesis folder is available on PATH
        kinesis_path = 'C:/Program Files/Thorlabs/Kinesis'
        if kinesis_path not in os.environ['PATH']:
            os.environ['PATH'] += os.pathsep + kinesis_path

        record = EquipmentRecord(
            manufacturer='Thorlabs', model='KDC101',
            serial=self.config['serial'],
            connection=ConnectionRecord(
                backend=Backend.MSL,
                address='SDK::Thorlabs.MotionControl.KCube.DCServo.dll'))

        # avoid the FT_DeviceNotFound error
        MotionControl.build_device_list()

        # connect to the KCube Stepper Motor
        motor = record.connect()
        logger.debug('Connected to {}'.format(motor))

        # load the configuration settings (so that we can use the get_real_value_from_device_unit() method)
        motor.load_settings()
        # start polling at 200 ms
        motor.start_polling(200)

        # ic(motor.settings)

        return motor

    def _wait(self):
        """Wait for the Kinesis to have moved.
        """
        self.device.clear_message_queue()
        while True:
            status = self.device.convert_message(
                *self.device.wait_for_message())['id']
            if status == 'Homed' or status == 'Moved':
                break
            position = self.device.get_position()
            real = self.device.get_real_value_from_device_unit(
                position, 'DISTANCE')
            # logger.debug('  at position {} [device units] {:.3f} [real-world units]'.format(position, real))
            # time.sleep(.2)

    def home(self):
        """Home the device
        """
        logger.debug('Homing...')
        self.device.home()
        self._wait()
        logger.debug('Homing done. At position {} [device units]'.format(
            self.device.get_position()))

    def _log_pos(self):
        """Logs the current position
        """
        pos = self.device.get_position()
        pdevu = 'At position {} [device units]'.format(pos)
        pnatu = 'At position {} [natural units]'.format(
            self.device.get_real_value_from_device_unit(
                pos, 'DISTANCE'))
        logger.debug(pdevu + pnatu)

    def curr_pos(self):
        """return current position"""
        pos = self.device.get_position()
        return self.device.get_real_value_from_device_unit(
            pos, 'DISTANCE')

    def _move_absolute(self, pos):
        """Move to an absolute position

        Args:
            pos : int
                the position to move to in internal steps
        """
        # logger.debug('Moving to {:d}...'.format(pos))
        pos_devu = self.device.get_device_unit_from_real_value(
            pos, 'DISTANCE')
        self.device.move_to_position(pos_devu)
        self._wait()
        time.sleep(self.wait_after_move)
        # logger.debug('Moving done.')
        # self._log_pos()

    def _move_relative(self, step):
        """Move by a relative step

        Args:
            step : int
                the step in internal units
        """
        # logger.debug('Moving by {:d}...'.format(step))
        step_devu = self.device.get_device_unit_from_real_value(
            step, 'DISTANCE')
        self.device.move_relative(step_devu)
        self._wait()
        time.sleep(self.wait_after_move)
        # logger.debug('Moving done.')
        # self._log_pos()

    def set(self, val):
        """Set a value. Called from outside, calls a specific function.

        Args:
            val : float
                the value to set to.
        """
        pos = int(val)
        self._move_absolute(pos)

    def __del__(self):
        self.device.stop_polling()
        self.device.disconnect()


class AAAOTF_lowlevel(serial.Serial):
    """Low-level implementation of AA AOTF communication via serial
    communication
    https://gitlab.com/nanooptics-code/hyperion/-/blob/master/hyperion/controller/aa/aa_modd18012.py

    Args:
        port : str
            the serial port used for the communication.
            Defaults to '/dev/ttyDAQ' (docker renamed)
            on a bare system, use sth like /dev/ttyACM0
            on Windows: COM
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
                 baudrate=57600, bytesize=8, parity='N',
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

    def main_enabled(self, value):
        """Enable the
        """
        self.query("I{}".format(value), expectanswer=False)

    def enable(self, channel, value):
        """Enable single channels.
        Args:
            channel : int
                channel to use (can be from 1 to 8 inclusive)
            value : bool
                True for on and False for off
        """
        if value:
            value = 1
        else:
            value = 0

        self.query("L{}O{}".format(channel, value), expectanswer=False)

    def store(self):
        """store current parameters into EEPROM
        """
        self.query("E", expectanswer=False)

    def blanking(self, state, mode):
        """Define the blanking state. If True (False), all channels are on (off).
        It can be set to 'internal' or 'external', where external means that the modulation voltage
        of the channel will be used to define the channel output.

        Args:
            state : bool
                blanking state: True->channels on
            mode : str
                'external' or 'internal'. 
                'external' is used to follow TTL external modulation
        """
        if mode == 'internal':
            if state:
                self.query("L0I1O1", expectanswer=False)
            else:
                self.query("L0I1O0", expectanswer=False)
        elif mode == 'external':
            if state:
                self.query("L0I0O1", expectanswer=False)
            else:
                self.query("L0I0O0", expectanswer=False)
        else:
            raise Warning('Blanking type not known.')

    def get_states(self):
        """ Gets the status of all the channels

        Returns:
            states : str
                message from the driver describing all channel states
        """
        return self.query('S')

    def frequency(self, channel, value):
        """RF frequency for a given channel.
        Args:
            channel : int
                channel to set the frequency.
            value : float
                Frequency to set in MHz (it has accepted ranges that depends on the channel)
        """
        self.query("L{}F{}".format(channel, value), expectanswer=False)

    def powerdb(self, channel, value):
        """Power for a given channel (in db).
        Range: (0,22) dBm

        Args:
            channel : int
                channel to use
            value : float
                power value in dBm
        """
        self.query("L{}D{}".format(channel, value), expectanswer=False)

    # def power(self, channel, value):
    #     """Power for a given channel (in digital units).
    #     """
    #     self.query("L{}P{:04d}".format(channel, value), expectanswer=False)

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

        if expectanswer:
            answer = self.read_until()
            answer = answer.decode().split('\rD')[0]

            if values is not None:
                valrev = {v: k for k, v in values.items()}
                answer = valrev[answer]
            return answer


class AAAOTFAttenuator(AbstractAttenuator):
    """Implementation of the AbstractAttenuator using an AOTF
    from AA.

    """
    CHANNELS = list(range(8))

    
