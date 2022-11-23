#!/usr/bin/env python
"""
    monet/__main__.py
    ~~~~~~~~~~~~~~~~~

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import logging
import time
import pprint
from logging import handlers
import yaml as _yaml
import cmd
import copy
import os
import traceback
from io import StringIO
from contextlib import redirect_stdout

from monet import CONFIGS, CONFIGS_PATH, PROTOCOLS, PROTOCOLS_PATH


# configure logger and log that this shouldn't be done here
def config_logger():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s | %(name)s | %(levelname)s -> %(message)s')
    file_handler = handlers.RotatingFileHandler(
        'monet.log', maxBytes=1e6, backupCount=5)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.WARNING)
    logger.addHandler(file_handler)
    # logger.addHandler(stream_handler)


def main():
    """Function called from the command line.
    """
    import argparse
    # os.chdir(os.path.split(CONFIGS_PATH)[0])

    # Main parser
    parser = argparse.ArgumentParser("monet")
    parser.add_argument(
        'mode', type=str,
        help='mode. One of "set", "adjust", or "calibrate".')
    parser.add_argument(
        'name', type=str,
        help='Microscope Name, as specified in config.')
    parser.add_argument(
        '-c', '--configs-file', type=str, required=False,
        default=None,
        help='''
            path to the configurations yaml file.
            - Only for calibration mode''')
    parser.add_argument(
        '-p', '--protocol-file', type=str, required=False,
        default=None,
        help='''
            path to the protocol yaml file (if not supplied, only attenuation
            will be controlled, no laser control).
            - Only for calibration mode''')

    # Parse
    args = parser.parse_args()
    if args.mode == 'calibrate':
        MonetCalibrateInteractive(
            args.name, args.configs_file, args.protocol_file).do_calibrate(args={})
    elif args.mode == 'adjust':
        MonetAdjustInteractive(
            args.name, args.configs_file, args.protocol_file).cmdloop()
    elif args.mode == 'set':
        MonetSetInteractive(
            args.name).cmdloop()
    else:
        raise KeyError('monet mode has to be one of "set" and "calibrate".')


# def monet_interactive(CONFIG):
#     import monet.calibrate as mca
#     config_items = [
#         config['index'],
#         config['analysis']['init_kwargs'],
#     ]
#     config_cmds = [
#         'database',
#         *[*list(item.keys()) for item in config_items]
#         # *list(config['index'].keys()),
#         # *list(config['analysis']['init_kwargs'].keys())
#     ]
#
#     config = CONFIG
#     pc = mca.PowerCalibrator(config)
#     # do a main loop
#     while True:
#         cmd = input('Enter command: ')
#         if 'calibrate' in cmd:
#             pc.calibrate()
#         elif 'set' in cmd:
#             pwr = int(cmd[3:])
#             pc.set_power(pwr)
#         elif 'config' in cmd:
#             subcommands = {
#                 combi.strip().split(' ')[0]: combi.strip().split(' ')[1]
#                 for i, combi in enumerate(cmd.split('--'))
#                 if i>0}
#
#             if len(subcommands,keys()) == 0:
#                 print_help_interactive_config(config_commands)
#                 continue
#             for c, v in subcommands.items():
#                 if c == 'database':
#                     config['database'] = v
#                 cmd = get_most_similar(c, config_commands)
#                 for item in config_items:
#                     if cmd in item.keys():
#                         try:
#                             item[cmd] = float(v)
#                         except:
#                             item[cmd] = v
#                         print('Setting {:s} to '.format(cmd), v)
#                         pp.pprint(config)
#                         break
#         elif cmd.startswith('load'):
#             fname = cmd.strip().split(' ')[1]
#             config = _yaml.full_load(fname)
#             pc = mca.PowerCalibrator(config)
#         elif cmd == 'q' or 'exit' in cmd:
#             return
#         else:
#             print_help_interactive(config_cmds)
#         time.sleep(.2)


class MonetCalibrateInteractive(cmd.Cmd):
    """Command-line interactive power calibration and setting.
    """
    intro = '''Welcome to interactive monet. Here, Microscope
        illumination can be calibrated and set. Two modes are
        available:
            * only modulate the attenuator (e.g. HWP/Polarizator)

            * modulate laser type, laser power and attenuator
        '''
    prompt = '(monet calibrate)'
    file = None

    def __init__(self, config_name, configs_file=None, protocol_file=None):
        super().__init__()
        import monet.calibrate as mca
        global CONFIGS, PROTOCOLS

        if configs_file is not None:
            with open(configs_file, 'r') as cf:
                CONFGIS = _yaml.full_load(cf)
        try:
            config = CONFIGS[config_name]
        except KeyError as e:
            print('Could not find ' +
                  config_name + ' in configurations. Aborting.')
            print('All configurations:')
            pp = pprint.PrettyPrinter(indent=2)
            pp.pprint(CONFIGS)
            raise e

        if protocol_file is not None:
            with open(protocol_file, 'r') as pf:
                PROTOCOLS = _yaml.full_load(pf)
        try:
            protocol = PROTOCOLS[config_name]
        except KeyError as e:
            print('Could not find ' +
                  config_name + ' in protocols. Not using laser control.')
            print('All protocols:')
            pp = pprint.PrettyPrinter(indent=2)
            pp.pprint(PROTOCOLS)
            protocol = None
            # raise e

        if protocol is None:
            self.pc = mca.CalibrationProtocol1D(config)
            self.run_2d = False
        else:
            self.pc = mca.CalibrationProtocol2D(config, protocol)
            self.run_2d = True
        self.config_name = config_name

    def do_calibrate(self, args):
        """Perform a power calibration with the settings as described
        in the configuration.
        """
        self.pc.instrument.attenuator.home()
        if not self.run_2d:
            self.pc.calibrate()
        else:
            self.pc.run_protocol()

    def do_set(self, power):
        """Set the power to a specified level.
        Args:
            power : float
                the power to set to [mW]
        """
        if not power:
            print('Please specify a power value.')
        else:
            try:
                print('Setting power for settings {:s}'.format('\n'.join([str(k)+': '+str(v) for k, v in self.config['index'].items()])))
                self.pc.instrument.power = int(power)
            except ValueError as e:
                print(str(e))

    def do_config(self, line):
        """Change the configuration.
        Args:
            Format --parameter: value
            --database : str
                the path to the database (ends in .xlsx)
            --other config parameters in 'index', or 'analysis/init_kwargs'
        """
        pp = pprint.PrettyPrinter(indent=2)
        try:
            commands = line.split('--')[1:]
            kwargs = {cmd.split(':')[0].strip(): cmd.split(':')[1].strip() for cmd in commands}
        except:
            print('please format your commands correctly')
            print('Current Configuration:')
            pp.pprint(self.config)

        if 'database' in kwargs.keys():
            self.pc.instrument.config['database'] = kwargs['database']

        config_items = [
            self.pc.instrument.config['index'],
            self.pc.instrument.config['analysis']['init_kwargs'],
        ]
        config_cmds = []
        for item in config_items:
            config_cmds = config_cmds + list(item.keys())

        for c, v in kwargs.items():
            cmd = get_most_similar(c, config_cmds)
            for item in config_items:
                if cmd in item.keys():
                    try:
                        item[cmd] = float(v)
                    except:
                        item[cmd] = v
                    print('Setting {:s} to '.format(cmd), v)
                    pp.pprint(self.pc.instrument.config)
                    break

    def help_config(self):
        helplines = ['--database : str', '   the path to the database (ends in .xlsx)' ]
        config_items = [
            self.pc.instrument.config['index'],
            self.pc.instrument.config['analysis']['init_kwargs'],
        ]
        for it in config_items:
            for k, v in it.items():
                helplines.append('--{:s}'.format(k))
                helplines.append(' '*4 + 'currently {:s}'.format(str(v)))
        print('\n'.join(helplines))
        print('Some Fuzziness is allowed. Matching of single words is ok.')

    def do_rename(self, name):
        """Rename the microscope name for the current settings.
        Args:
            name : str
                the microscope name
        """
        self.config_name = name.strip()
        self.pc.instrument.config['index']['name'] = self.config_name

    def do_load_config(self, fname):
        """Load configuration from file.
        Args:
            fname : str
                the file name
        """
        with open(fname, 'r') as f:
            self.pc.instrument.config = _yaml.full_load(f)
        if not self.run_2d:
            self.pc = mca.CalibrationProtocol1D(self.pc.instrument.config)
        else:
            self.pc = mca.CalibrationProtocol2D(
                self.pc.instrument.config, self.pc.protocol)

    def do_load_protocol(self, fname=None):
        """Load protocol from file. If no file name is given, load
        from default protocols.

        Args:
            fname : str
                the file name
        """
        if fname is not None:
            with open(fname, 'r') as f:
                self.pc.protocol = _yaml.full_load(f)
        else:
            self.pc.protocol = PROTOCOLS[self.config_name]

        if not self.run_2d:
            print('Protocol files are only used in with laser control. Switching mode.')
            self.run_2d = True
        self.pc = mca.CalibrationProtocol2D(
                self.pc.instrument.config, self.pc.protocol)

    def do_save_config(self, fname=''):
        """Save configuration to file.
        Args:
            fname : str
                The filename to save the yaml file. If empty, save to
                the location loaded during init in __init__.
        """
        if not fname:
            fname = CONFIGS_PATH

        cfgs = CONFIGS
        cfgs[self.config_name] = copy.deepcopy(self.pc.instrument.config)
        with open(fname, 'w') as f:
            _yaml.dump(cfgs, f)

    def do_save_protocol(self, fname=''):
        """Save configuration to file.
        Args:
            fname : str
                The filename to save the yaml file. If empty, save to
                the location loaded during init in __init__.
        """
        if not self.run_2d:
            print('Not in laser modulation mode. No protocol available for saving.')
            return

        if not fname:
            fname = PROTOCOLS_PATH

        prts = PROTOCOLS
        prts[self.config_name] = copy.deepcopy(self.pc.protocol)
        with open(fname, 'w') as f:
            _yaml.dump(prts, f)
    # def do_EOF(self, line):
    #     return True
    #
    def do_exit(self, line):
        """Exit the interaction
        """
        return True

    def precmd(self, line):
        return line

    def close(self):
        pass


class MonetAdjustInteractive(cmd.Cmd):
    """Command-line interactive power calibration and setting.
    """
    intro = '''Welcome to interactive monet adjust. Here, Microscope
        illumination can be aligned and adjusted. To that end, all configured
        lasers can be switched on and off, laser powers set, and power
        limits queried.
        '''
    prompt = '(monet adjust)'
    file = None

    def __init__(self, config_name, configs_file=None, protocol_file=None):
        super().__init__()
        import monet.calibrate as mca
        global CONFIGS, PROTOCOLS

        if configs_file is not None:
            with open(configs_file, 'r') as cf:
                CONFGIS = _yaml.full_load(cf)
        try:
            config = CONFIGS[config_name]
        except KeyError as e:
            print('Could not find ' +
                  config_name + ' in configurations. Aborting.')
            print('All configurations:')
            pp = pprint.PrettyPrinter(indent=2)
            pp.pprint(CONFIGS)
            raise e

        if protocol_file is not None:
            with open(protocol_file, 'r') as pf:
                PROTOCOLS = _yaml.full_load(pf)
        try:
            protocol = PROTOCOLS[config_name]
        except KeyError as e:
            print('Could not find ' +
                  config_name + ' in protocols. Not using laser control.')
            print('All protocols:')
            pp = pprint.PrettyPrinter(indent=2)
            pp.pprint(PROTOCOLS)
            protocol = None
            # raise e

        if protocol is None:
            self.pc = mca.CalibrationProtocol1D(config)
            self.run_2d = False
        else:
            self.pc = mca.CalibrationProtocol2D(config, protocol)
            self.run_2d = True
        self.config_name = config_name

    def do_laser(self, laser):
        """Set the laser to use, enable it and switch beam path accordingly.
        Args:
            laser : int
                laser to set to (specified by wavelength in nm)
        """
        if not laser:
            print('Please specify a laser.')
        else:
            laser = int(laser)
            try:
                # print('Setting laser')
                self.pc.instrument.laser = laser
            except Exception as e:
                print(str(e))
                print('Available lasers: ', str(self.pc.instrument.laser))
                return
            try:
                self.pc.instrument.laser_enabled = True
            except Exception as e:
                print(str(e))
                return
            try:
                self.pc.instrument.beampath.positions = self.pc.protocol[
                    'beampath'][laser]
            except Exception as e:
                print(str(e))
                return
            self.pc.powermeter.wavelength = int(laser)

    def do_laser_power(self, power):
        """Set the power to a specified level.
        Args:
            power : float
                the power to set to [mW]
        """
        if not power:
            print('Please specify a power value.')
        else:
            try:
                self.pc.instrument.laserpower = int(power)
            except ValueError as e:
                print(str(e))

    def do_min_power(self, line):
        """Query the minimum power of the current laser"""
        laser = self.pc.instrument.lasers[self.pc.instrument.curr_laser]
        print('Minimum power of laser: ', laser.min_power)

    def do_max_power(self, line):
        """Query the maximum power of the current laser"""
        laser = self.pc.instrument.lasers[self.pc.instrument.curr_laser]
        print('Maximum power of laser: ', laser.max_power)

    def do_attenuate(self, pos):
        """Set the attenuation device to a position (float)"""
        if pos.upper() == 'HOME':
            self.pc.instrument.attenuator.home()
        else:
            pos = float(pos)
            self.pc.instrument.attenuator.set(pos)

    def do_open(self, line):
        """open shutter and set the correct light path positions"""
        try:
            self.pc.instrument.beampath.positions = self.pc.protocol[
                'beampath'][self.pc.instrument.curr_laser]
        except Exception as e:
            print(str(e))
            return

    def do_close(self, line):
        """close shutter"""
        try:
            self.pc.instrument.beampath.positions = self.pc.protocol[
                'beampath']['end']
        except Exception as e:
            print(str(e))
            return

    def do_autoshutter(self, line):
        """set autoshutter"""
        try:
            line = int(line)
        except:
            if line.upper() == 'TRUE':
                line = 1
            else:
                line = 0
        if line == 1:
            line = True
        else:
            line = False
        try:
            self.pc.instrument.beampath.objects['shutter'].autoshutter = line
        except Exception as e:
            print(str(e))
            return

    def do_py(self, line):
        """Execute a line of code"""
        line = 'print(' + line + ')'
        f = StringIO()
        with redirect_stdout(f):
            try:
                exec(line)
            except Exception as e:
                print(str(e))
                print(traceback.format_exc())
        print(f.getvalue())

    def do_restartdb(self, line):
        """Restart the database with the last entries and save a backup"""
        fname = self.pc.config['database']
        io.restart_database(fname)

    def do_exit(self, line):
        """Exit the interaction
        """
        laser = self.pc.instrument.lasers[self.pc.instrument.curr_laser]
        laser.enabled=False
        return True

    def precmd(self, line):
        return line

    def close(self):
        pass


class MonetSetInteractive(cmd.Cmd):
    """Command-line interactive power setting.
    """
    intro = '''Welcome to interactive monet - set. Here, Microscope
        illumination power can be set if calibrations exist. A powermeter
        does not need to be plugged in.
        '''
    prompt = '(monet set)'
    file = None

    def __init__(self, config_name):
        super().__init__()
        import monet.control as mco
        from monet.util import load_class
        global CONFIGS, PROTOCOLS

        try:
            config = CONFIGS[config_name]
        except KeyError as e:
            print('Could not find ' +
                  config_name + ' in configurations. Aborting.')
            print('All configurations:')
            pp = pprint.PrettyPrinter(indent=2)
            pp.pprint(CONFIGS)
            raise e

        try:
            protocol = PROTOCOLS[config_name]
        except KeyError as e:
            print('Could not find ' +
                  config_name + ' in protocols. Not using laser control.')
            print('All protocols:')
            pp = pprint.PrettyPrinter(indent=2)
            pp.pprint(PROTOCOLS)
            protocol = None
        self.protocol = protocol

        self.instrument = mco.IlluminationLaserControl(config, auto_enable_lasers=False)
        try:
            self.instrument.load_calibration_database()
        except:
            raise KeyError(
                'Microscope probably not calibrated yet. ' +
                'Monet Set only works with an existing calibration.')

        # load powermeter if present
        try:
            pwrconfig = config['powermeter']
            self.powermeter = load_class(
                pwrconfig['classpath'], pwrconfig['init_kwargs'])
            self.use_powermeter = True
        except:
            self.use_powermeter = False
            print('Powermeter not connected. Do not use measure function')
            # print(traceback.format_exc())

        # switch on autoshutter (is switched on in initialization because
        # that is necessary for calibrate and adjust)
        try:
            self.instrument.beampath.objects['shutter'].autoshutter = True
        except Exception as e:
            pass

        self.config_name = config_name

        self.power_setvalues = {}
        for las in self.instrument.laser:
            self.do_laser(las)
            self.power_setvalues[las] = self.instrument.power

    def do_laser(self, laser):
        """Activate a laser. Deactivate current laser with 'OFF', deactivate all with 'ALLOFF'
        Args:
            laser : str
                the laser to activate (its wavelength in nm)
                if 'OFF': turn current laser off
                if 'ALLOFF': turn all lasers off
        """
        if not laser:
            print('Currently active laser: ', self.instrument.curr_laser)
        else:
            if isinstance(laser, str) and laser.upper() == 'OFF':
                self.instrument.lasers[self.instrument.curr_laser].enabled = False
            elif isinstance(laser, str) and laser.upper() == 'ALLOFF':
                for las in self.instrument.laser:
                    self.instrument.lasers[las].enabled = False
            else:
                try:
                    print('Setting laser {:s}.'.format(str(laser)))
                    self.instrument.laser = laser
                    # set laser power back to the value for that laser
                    try:
                        self.do_power(self.power_setvalues[self.instrument.curr_laser])
                    except:
                        pass
                    if self.use_powermeter:
                        self.powermeter.wavelength = int(laser)
                except ValueError as e:
                    print(str(e))

    def do_laser_power(self, power):
        """Set the laser power of the current laser"""
        if not power:
            print('Current laserpower out of laser: ', self.instrument.laserpower)
        else:
            try:
                self.instrument.laserpower = int(power)
            except ValueError as e:
                print(str(e))

    def do_power(self, power):
        """Set the power to a specified level.
        Args:
            power : float
                the power to set to [mW]
        """
        if not power:
            print('Current power at objective:', self.instrument.power)
        else:
            try:
                # print('Setting power for settings \n {:s}'.format('\n'.join(
                #     [str(k)+': '+str(v)
                #      for k, v in self.instrument.config['index'].items()])))
                print('Setting output power to ', int(power))
                self.instrument.power = int(power)
                self.power_setvalues[self.instrument.curr_laser] = int(power)
            except ValueError as e:
                print(str(e))

    def do_attenuate(self, pos):
        """Set the attenuation device to a position (float)"""
        if pos.upper() == 'HOME':
            self.instrument.attenuator.home()
        else:
            pos = float(pos)
            self.instrument.attenuator.set(pos)

    def do_open(self, line):
        """open shutter and set the correct light path positions"""
        try:
            self.instrument.beampath.positions = self.protocol[
                'beampath'][self.instrument.curr_laser]
        except Exception as e:
            print(str(e))
            return

    def do_close(self, line):
        """close shutter"""
        try:
            self.instrument.beampath.positions = self.protocol[
                'beampath']['end']
        except Exception as e:
            print(str(e))
            return

    def do_autoshutter(self, line):
        """set autoshutter"""
        try:
            line = int(line)
        except:
            if line.upper() == 'TRUE':
                line = 1
            else:
                line = 0
        if line == 1:
            line = True
        else:
            line = False
        try:
            self.instrument.beampath.objects['shutter'].autoshutter = line
        except Exception as e:
            print(str(e))
            return

    def do_measure(self, averaging):
        """Measure the power if a powermeter is connected.
        Args:
            averaging : int
                the number of measurements to average
        """
        try:
            averaging = int(averaging)
        except:
            averaging = 10

        from monet import POWER_TAG
        if self.use_powermeter:
            print(POWER_TAG + ': ' + str(self.powermeter.read(averaging)))
        else:
            print('No powermeter is connected. Cannot measure.')

    def do_py(self, line):
        """Execute a line of code"""
        line = 'print(' + line + ')'
        f = StringIO()
        with redirect_stdout(f):
            try:
                exec(line)
            except Exception as e:
                print(str(e))
        print(f.getvalue())

    def do_exit(self, line):
        """Exit the interaction
        """
        #self.do_laser('off')
        self.close()
        return True

    def precmd(self, line):
        return line

    def close(self):
        pass


def get_most_similar(input, options):
    equals = [input==opt for opt in options]
    partof = [input in opt for opt in options]

    if any(equals):
        return options[equals.index(True)]
    elif any(partof):
        return options[partof.index(True)]
    else:
        return None

def print_help_interactive(config_commands):
    pp = pprint.PrettyPrinter(indent=2)
    print('Interactive monet.')
    print('Commands:')
    cmd_desc = {
        'calibrate': 'Start calibration routine according to the configuration',
        'set': 'set power to a level. Args: Power in mW, float.',
        'config': 'alter the configuration',
        'q or exit': 'quit',
    }
    pp.pprint(cmd_desc)
    print_help_interactive_config(config_commands)


def print_help_interactive_config(config_commands):
    print('for config commands, use pairs of "--[CMD] [ARG]"')
    print('Commands config (CMD): ')
    print(config_commands)


if __name__ == "__main__":
    config_logger()
    logger = logging.getLogger(__name__)
    logger.debug('start logging')
    main()
