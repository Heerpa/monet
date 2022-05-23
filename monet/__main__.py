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

from monet import CONFIGS, CONFIGS_PATH


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
    os.chdir(os.path.split(CONFIGS_PATH)[0])

    # Main parser
    parser = argparse.ArgumentParser("monet")
    parser.add_argument(
        '-n', '--name', type=str, required=True,
        help='Microscope Name, as specified in config.')
    parser.add_argument(
        '-c', '--configs-file', type=str, required=False,
        default=None,
        help='path to the configurations yaml file.')

    # Parse
    args = parser.parse_args()
    MonetInteractive(args.name, args.configs_file).cmdloop()


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


class MonetInteractive(cmd.Cmd):
    """Command-line interactive power calibration and setting.
    """
    intro = 'Welcome to interactive monet.'
    prompt = '(monet)'
    file = None

    def __init__(self, config_name, configs_file=None):
        super().__init__()
        import monet.calibrate as mca
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

        self.pc = mca.PowerCalibrator(config)
        self.config_name = config_name

    def do_calibrate(self, args):
        """Perform a power calibration with the settings as described
        in the configuration.
        """
        self.pc.calibrate()

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
                self.pc.set_power(int(power))
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
            self.pc.config['database'] = kwargs['database']

        config_items = [
            self.pc.config['index'],
            self.pc.config['analysis']['init_kwargs'],
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
                    pp.pprint(self.pc.config)
                    break

    def help_config(self):
        helplines = ['--database : str', '   the path to the database (ends in .xlsx)' ]
        config_items = [
            self.pc.config['index'],
            self.pc.config['analysis']['init_kwargs'],
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
        self.pc.config['index']['name'] = self.config_name

    def do_load(self, fname):
        """Load configuration from file.
        Args:
            fname : str
                the filie name
        """
        with open(fname, 'r') as f:
            self.pc.config = _yaml.full_load(f)
        self.pc = mca.PowerCalibrator(self.pc.config)

    def do_save(self, fname=''):
        """Save configuration to file.
        Args:
            fname : str
                The filename to save the yaml file. If empty, save to
                the location loaded during init in __init__.
        """
        if not fname:
            fname = CONFIGS_PATH
        if fname:
            cfgs = CONFIGS
            cfgs[self.config_name] = copy.deepcopy(self.pc.config)
            with open(CONFIGS_PATH, 'w') as f:
                _yaml.dump(cfgs, f)

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
