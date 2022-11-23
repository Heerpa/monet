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
        return self.device.get_position()

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
