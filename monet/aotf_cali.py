#!/usr/bin/env python
"""
    monet/aotf_cali.py
    ~~~~~~~~~~~~~~~~~~
    
    calibrate the AA AOTF

    :authors: Heinrich Grabmayr, 2023
    :copyright: Copyright (c) 2023 Jungmann Lab, MPI of Biochemistry
"""
import numpy as np
import time
import argparse
import matplotlib.pyplot as plt

from monet.attenuation import AAAOTF_lowlevel
from monet.powermeter import ThorlabsPowerMeter


def sweep_freq(aotf, powermeter, channel, freqs):
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
    start_progress('Frequency sweep', len(freqs))
    powers = np.nan * np.ones_like(freqs)
    for i, freq in enumerate(freqs):
        progress(i/len(freqs)*100)
        aotf.frequency(channel, freq)
        time.sleep(.1)
        powers[i] = powermeter.read()
    end_progress()
    return powers


def sweep_pdb(aotf, powermeter, channel, pdbs):
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
    start_progress('Power sweep', len(pdbs))
    powers = np.nan * np.ones_like(pdbs)
    for i, pdb in enumerate(pdbs):
        progress(i/len(pdbs)*100)
        aotf.powerdb(channel, pdb)
        time.sleep(.1)
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
    print(title + ": [" + "-"*40 + "]", end='\r')
    progress_x = 0

def progress(x):
    global title
    deci = int((x - int(x))*10)
    x = int(x * 40 // 100)
    y = max([0, 40-x-1])
    print(title + ": [" + '#'*x + str(deci) +"-"*y + "]", end='\r')
    #print(x, y, deci, x+y+1)


def end_progress():
    #sys.stdout.write("#" * (40 - progress_x) + "]\n")
    #sys.stdout.flush()
    print(title + ": [" + "#"*40 + "]", end='\n')


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
        'AOTF_port': 'COM5'
    }
    channel = arguments['channel']
    ctrfreq = arguments['ctrfreq']
    freqwindow = arguments['freqwindow']
    freqstep = arguments['freqstep']

    freqs = np.arange(ctrfreq-freqwindow/2, ctrfreq+freqwindow/2, step=freqstep)
    pdbs = np.arange(0, 22.6, step=.1)

    aotf = AAAOTF_lowlevel(
        port=arguments['AOTF_port'], baudrate=57600, bytesize=8, parity='N',
        stopbits=1, timeout=1)
    powermeter = ThorlabsPowerMeter(config={'address': ''})

    wavelength = 561
    powermeter.wavelength = wavelength

    aotf.powerdb(channel, 22.5)
    powers_f = sweep_freq(aotf, powermeter, channel, freqs)

    best_freq = freqs[np.argmax(powers_f)]
    aotf.frequency(channel, best_freq)

    powers_p = sweep_pdb(aotf, powermeter, channel, pdbs)

    best_pdb = freqs[np.argmax(powers_p)]

    fig, ax = plt.subplots(ncols=2)
    ax[0].plot(freqs, powers_f)
    ax[0].set_xlabel('Frequency [MHz]')
    ax[0].set_ylabel('beam power at {:.0f}nm [mW]'.format(wavelength))
    ax[0].set_title('optimum frequency: {:.3f} MHz'.format(best_freq))

    ax[1].plot(pdbs, powers_p)
    ax[1].set_xlabel('AOTF power [db]')
    ax[1].set_ylabel('beam power at {:.0f}nm [mW]'.format(wavelength))
    ax[1].set_title('optimum AOTF power: {:.1f} db'.format(best_pdb))

    fig.savefig('aotfpower.png')
    plt.show()