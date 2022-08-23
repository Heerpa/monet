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
        'mode', type=str, required=True,
        help='mode. One of "set" and "calibrate".')
    parser.add_argument(
        '-n', '--name', type=str, required=True,
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
    prompt = '(monet)'
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


class MonetSetInteractive(cmd.Cmd):
    """Command-line interactive power setting.
    """
    intro = '''Welcome to interactive monet - set. Here, Microscope
        illumination power can be set if calibrations exist.
        '''
    prompt = '(monet)'
    file = None

    def __init__(self, config_name):
        super().__init__()
        import monet.calibrate as mca

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
            # raise e

        if protocol is None:
            self.pc = mca.CalibrationProtocol1D(config)
            self.run_2d = False
        else:
            self.pc = mca.CalibrationProtocol1D(config, protocol)
            self.run_2d = True
        self.config_name = config_name

    def do_set_laser(self, laser):
        """Activate a laser.
        Args:
            laser : str
                the laser to activate
        """
        if not self.run_2d:
            print('Cannot switch lasers automatically in non-laser control mode.')
            return

        if not laser:
            print('Please specify a laser.')
        else:
            try:
                print('Setting laser {:s}.'.format(str(laser)))
                self.pc.instrument.laser = laser
            except ValueError as e:
                print(str(e))

    def do_set_power(self, power):
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

    def do_exit(self, line):
        """Exit the interaction
        """
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
