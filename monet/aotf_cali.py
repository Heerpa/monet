#!/usr/bin/env python
"""
    monet/aotf_cali.py
    ~~~~~~~~~~~~~~~~~~
    
    calibrate the AA AOTF

    :authors: Heinrich Grabmayr, 2023
    :copyright: Copyright (c) 2023 Jungmann Lab, MPI of Biochemistry
"""
import os
import shutil
from datetime import datetime
import numpy as np
import pandas as pd
import time
import argparse
import yaml
import matplotlib.pyplot as plt

from monet.attenuation import AAAOTF_lowlevel
from monet.powermeter import ThorlabsPowerMeter


def sweep_freq(aotf, powermeter, channel, freqs, t_wait=.05):
    """Sweep over the frequencies and measure the power after each step
    Args:
        aotf : AAAOTF_lowlevel instance
            aotf control
        powermeter : ThorlabsPowerMeter instance
            powermeter control
        channel : int
            the channel to use
        freqs : 1D array
            the frequencies to query
    """
    aotf.frequency(channel, freqs[0])
    time.sleep(.5)
    start_progress('Frequency sweep', len(freqs))
    powers = np.nan * np.ones_like(freqs)
    for i, freq in enumerate(freqs):
        progress(i/len(freqs))
        aotf.frequency(channel, freq)
        time.sleep(t_wait)
        powers[i] = powermeter.read()
    end_progress()
    return powers


def sweep_pdb(aotf, powermeter, channel, pdbs, t_wait=.05):
    """Sweep over the aotf db powers and measure the power after each step
    Args:
        aotf : AAAOTF_lowlevel instance
            aotf control
        powermeter : ThorlabsPowerMeter instance
            powermeter control
        channel : int
            the channel to use
        pdbs : 1D array
            the aotf db powers to query
    """
    aotf.powerdb(channel, pdbs[0])
    time.sleep(.5)
    start_progress('Power sweep', len(pdbs))
    powers = np.nan * np.ones_like(pdbs)
    for i, pdb in enumerate(pdbs):
        progress(i/len(pdbs))
        aotf.powerdb(channel, pdb)
        time.sleep(t_wait)
        powers[i] = powermeter.read()
    end_progress()
    return powers


def start_progress(ltitle, n_frames):
    global progress_x, title
    global nimgs_acquired, nimgs_total
    nimgs_acquired = 0
    title = ltitle
    nimgs_total = n_frames
    #sys.stdout.write(title + ": [" + "-"*40 + "]" + chr(8)*41)
    #sys.stdout.flush()
    charwidth = 40
    print(title + ": [" + "-"*charwidth + "]", end='\r')
    progress_x = 0

def progress(x):
    """Updates the progress bar
    Args:
        x : float
            progress in fraction (0-1)
    """
    global title, nimgs_total
    charwidth = 40
    charprog = x * charwidth
    charfull = int(charprog)
    chardeci = int((charprog-charfull) * 10)
    if chardeci > 9:
        chardeci = 0
    charrest = charwidth - charfull - 1
    print(title + ": [" + '#'*charfull + str(chardeci) +"-"*charrest + "]" + "  {:d}/{:d}".format(1+int(x*nimgs_total), nimgs_total), end='\r')
    #print(x, y, deci, x+y+1)


def end_progress():
    #sys.stdout.write("#" * (40 - progress_x) + "]\n")
    #sys.stdout.flush()
    charwidth = 40
    print(title + ": [" + "#"*charwidth + "]", end='\n')


def calibrate_all(instrument, protocol, powermeter):
    """Calibrate all channels defined
    Args:
        instrument : monet.control.IlluminationLaserControl Instance
            the access to all hardware
    """
    aotf = instrument.attenuator
    aotf.lowlvl.blanking(True, 'internal')
    freqstep = aotf.config['freqstep']
    freqwindow = aotf.config['freqwindow']

    channeldef = aotf.channeldef
    wavelengths = channeldef['wavelength'].unique()
    channels = {
        int(wvl): int(channeldef.loc[channeldef['wavelength']==wvl, 'channel'].values[0])
        for wvl in channeldef['wavelength'].unique()
        if wvl > 0}
    indexes = {
        int(wvl): list(channeldef[channeldef['wavelength']==wvl].index)[0]
        for wvl in channeldef['wavelength'].unique()
        if wvl > 0}
    filedir, _ = os.path.split(aotf.config['channeldef_loc'])

    lasers = instrument.laser

    missing_defs = []
    calibrate_lasers = []
    for l in lasers:
        if l in wavelengths:
            calibrate_lasers.append(l)
        else:
            missing_defs.append(l)

    if len(missing_defs) > 0:
        print('channel for laser {:s} not defined.'.format(str(missing_defs)))
    print('Calibrating lasers {:s}.'.format(str(calibrate_lasers)))

    # go through lasers
    for laser in calibrate_lasers:
        print('Calibrating laser ', laser)
        instrument.laser = laser
        instrument.laserpower = max(protocol['laser_powers'][laser])
        time.sleep(10)
        try:
            instrument.beampath.positions = protocol['beampath'][laser]
        except Exception as e:
            print(str(e))
            return

        # previously set approximate frequency and aotf power
        prev_freq = channeldef.loc[indexes[laser], 'frequency']
        prev_pwr = channeldef.loc[indexes[laser], 'power']
        aotf.lowlvl.enable(channels[laser], True)
        aotf.lowlvl.frequency(channels[laser], prev_freq)
        aotf.lowlvl.powerdb(channels[laser], prev_pwr)

        freqs = np.arange(prev_freq-freqwindow/2, prev_freq+freqwindow/2, step=freqstep)
        pdbs = np.arange(0, 22.6, step=.1)

        powers_f = sweep_freq(aotf.lowlvl, powermeter, channels[laser], freqs, t_wait=.01)

        best_freq = freqs[np.argmax(powers_f)]
        aotf.lowlvl.frequency(channels[laser], best_freq)

        powers_p = sweep_pdb(aotf.lowlvl, powermeter, channels[laser], pdbs, t_wait=.01)

        aotf.lowlvl.enable(channels[laser], False)

        best_pdb = pdbs[np.argmax(powers_p)]

        channeldef.loc[indexes[laser], 'frequency'] = best_freq
        channeldef.loc[indexes[laser], 'power'] = best_pdb

        instrument.laser_enabled = False

        plot_results(filedir, laser, freqs, powers_f, best_freq, pdbs, powers_p, best_pdb, prev_pwr)
    aotf.lowlvl.store()
    filename = aotf.config['channeldef_loc']
    channeldef.to_csv(filename, float_format='%.3f')
    srvdir, _ = os.path.split(instrument.config['database'])
    datestr = datetime.now().strftime('%Y-%m-%d_%H-%M_')
    srvdir = os.path.join(srvdir, 'AOTFcali', datestr+instrumnet.config['index']['name'])
    try:
        os.mkdirs(srvdir)
    shutil.copytree(filedir, srvdir)



def plot_results(filedir, wavelength, freqs, powers_f, best_freq, pdbs, powers_p, best_pdb, prev_pwr):
    fig, ax = plt.subplots(ncols=2)
    ax[0].plot(freqs, powers_f)
    ax[0].set_xlabel('Frequency [MHz]')
    ax[0].set_ylabel('beam power at {:.0f}nm [mW]'.format(wavelength))
    ax[0].set_title('optimum frequency: {:.3f} MHz (at {:.1f} db)'.format(best_freq, prev_pwr))

    ax[1].plot(pdbs, powers_p)
    ax[1].set_xlabel('AOTF power [db]')
    ax[1].set_ylabel('beam power at {:.0f}nm [mW]'.format(wavelength))
    ax[1].set_title('optimum AOTF power: {:.1f} db (at {:.3f} MHz)'.format(best_pdb, best_freq))

    fig.set_size_inches((8, 6))
    fig.tight_layout()
    filename = os.path.join(filedir, 'aotfpower{:d}nm.png'.format(wavelength))
    fig.savefig(filename)


if __name__ == '__main__':
    # # Main parser
    # parser = argparse.ArgumentParser("aotfcali")
    # parser.add_argument(
    #     'channel', type=int,
    #     help='The channel to calibrate. 1-8.')
    # parser.add_argument(
    #     'ctrfreq', type=float,
    #     help='Center frequency to test [MHz].')
    # parser.add_argument(
    #     'freqwindow', type=float,
    #     default=1,
    #     help='Frequency window width of testing [MHz].')
    # parser.add_argument(
    #     'freqstep', type=float,
    #     default=.001,
    #     help='Frequency step for testing [MHz].')

    # # Parse
    # args = parser.parse_args()
    # channel = args.channel
    # ctrfreq = args.ctrfreq
    # freqwindow = args.freqwindow
    # freqstep = args.freqstep

    arguments = {
        'channel': 6,
        'ctrfreq': 94,
        'freqwindow': 2,
        'freqstep': .001,
        'wavelength': 561,
        'AOTF_port': 'COM5',
        'output': 'C:\\Users\\admin\\Desktop\\AOTFcalibration',
        't_sweepstep': .01,
    }
    channel = arguments['channel']
    ctrfreq = arguments['ctrfreq']
    freqwindow = arguments['freqwindow']
    freqstep = arguments['freqstep']
    t_sweepstep = arguments['t_sweepstep']

    freqs = np.arange(ctrfreq-freqwindow/2, ctrfreq+freqwindow/2, step=freqstep)
    pdbs = np.arange(0, 22.6, step=.1)

    aotf = AAAOTF_lowlevel(
        port=arguments['AOTF_port'], baudrate=57600, bytesize=8, parity='N',
        stopbits=1, timeout=1)
    powermeter = ThorlabsPowerMeter(config={'address': ''})

    powermeter.wavelength = arguments['wavelength']

    aotf.enable(channel, True)
    aotf.powerdb(channel, 22.5)
    powers_f = sweep_freq(aotf, powermeter, channel, freqs, t_sweepstep)

    best_freq = freqs[np.argmax(powers_f)]
    aotf.frequency(channel, best_freq)

    powers_p = sweep_pdb(aotf, powermeter, channel, pdbs, t_sweepstep)

    aotf.enable(channel, False)

    best_pdb = pdbs[np.argmax(powers_p)]

    filename = os.path.join(arguments['output'], 'aotf_settings.csv')
    if os.path.exists(filename):
        settgs = pd.read_csv(filename, index_col=0)
    else:
        settgs = pd.DataFrame(index=np.arange(1, 9), columns=['wavelength', 'frequency', 'power'])
        settgs.index.name = 'channel'
    settgs.loc[channel, 'wavelength'] = arguments['wavelength']
    settgs.loc[channel, 'Frequency'] = best_freq
    settgs.loc[channel, 'Power'] = best_pdb
    settgs.to_csv(filename, float_format='%.3f')

    fig, ax = plt.subplots(ncols=2)
    ax[0].plot(freqs, powers_f)
    ax[0].set_xlabel('Frequency [MHz]')
    ax[0].set_ylabel('beam power at {:.0f}nm [mW]'.format(arguments['wavelength']))
    ax[0].set_title('optimum frequency: {:.3f} MHz'.format(best_freq))

    ax[1].plot(pdbs, powers_p)
    ax[1].set_xlabel('AOTF power [db]')
    ax[1].set_ylabel('beam power at {:.0f}nm [mW]'.format(arguments['wavelength']))
    ax[1].set_title('optimum AOTF power: {:.1f} db'.format(best_pdb))

    fig.set_size_inches((8, 6))
    fig.tight_layout()
    filename = os.path.join(arguments['output'], 'aotfpower{:d}nm.png'.format(arguments['wavelength']))
    fig.savefig(filename)
    plt.show()