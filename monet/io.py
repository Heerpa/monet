#!/usr/bin/env python
"""
    monet/io.py
    ~~~~~~~~~~~

    File in/output operations

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import pandas as pd
import numpy as np
import os
from datetime import datetime
import logging
from icecream import ic
import matplotlib.pyplot as plt

from monet import LASER_TAG, POWER_TAG, DEVICE_TAG
from monet import DATABASE_INDEXLEVELS

logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)


def save_calibration(fname, index, cali_pars):
    """Save the calibration to the database
    Args:
        fname : str
            file name to the database (excel)
        index : dict
            index values for the database entry
                e.g. microscope name, wavelength, laser power
        cali_pars : dict
            keys: parameter names, vals: calibration parameters

    Returns:
        indexnames : list of str
            the names of indices in the database
        indexvals : list of str
            the values of indices in the database
    """
    indexnames = list(index.keys()) + ['date', 'time']
    indexnames = DATABASE_INDEXLEVELS + list(set(indexnames) -
                                             set(DATABASE_INDEXLEVELS))
    index['date'] = datetime.now().strftime('%Y-%m-%d')
    index['time'] = datetime.now().strftime('%H:%M')
    indexvals = tuple([index[k] for k in indexnames])
    try:
        db = pd.read_excel(fname, index_col=list(range(len(indexvals))))
    except Exception as e:
        logger.debug('Problem loading database: ' + str(e) + ' Creating file.')
        # print('error loading database: ', str(e))
        ic(indexnames)
        ic(indexvals)
        midx = pd.MultiIndex.from_tuples(
            [indexvals], names=list(indexnames))
        db = pd.DataFrame(
            index=midx, columns=list(cali_pars.keys()))

    db.loc[indexvals, :] = list(cali_pars.values())
    db.to_excel(fname)

    return indexnames, indexvals


def load_calibration(fname, index, time_idx='latest'):
    """Load a calibration from the database

    Args:
        fname : str
            file name of the database
        index : dict
            index values for the database entry
                e.g. microscope name, wavelength, laser power
                keys: category names
                values: single values, or slice(None)
        time_idx : None, 'latest', or list, len 2
            loads either the latest (if time_idx is None or a string)
            or a specific date and time

    Returns:
        cali_pars : dict
            keys: parameter names, vals: calibration parameters
    """
    db_select = load_database(fname, index, time_idx=time_idx)

    # logger.debug('db_select')
    # logger.debug(db_select)
    # logger.debug('db_select.index')
    # logger.debug(db_select.index)
    # logger.debug('db_select.values')
    # logger.debug(db_select.values)

    cali_pars = {col: val
                 for col, val
                 in zip(db_select.index, db_select.values)
                 if not np.isnan(val)}
    return cali_pars


def load_database(fname, index, time_idx='last date'):
    """Load the database

    Args:
        fname : str
            file name of the database
        index : dict
            index values for the database entry
                e.g. microscope name, wavelength, laser power
                keys: category names
                values: single values, or slice(None)
        time_idx : None, 'latest', 'last date', or list, len 1 or 2
            loads either the latest (if time_idx is None or a string)
            or a specific date (and time)

    Returns:
        cali_pars : dict
            keys: parameter names, vals: calibration parameters
    """
    # indexnames = list(index.keys()) + ['date', 'time']
    indexnames = DATABASE_INDEXLEVELS
    index_full = {name: slice(None) for name in indexnames}
    for n, v in index.items():
        index_full[n] = v
    index = index_full

    if isinstance(time_idx, list) or isinstance(time_idx, tuple):
        if len(time_idx) > 2:
            pass
        index['date'] = time_idx[0]
        if len(time_idx) > 1:
            index['time'] = time_idx[1]
    indexvals = tuple(list(index.values()))
    ic(index)
    # if time_idx==None or isinstance(time_idx, str):
    #     datim = [slice(None), slice(None)]
    # elif (isinstance(time_idx, list) or isinstance(time_idx, tuple) and len(time_idx)==2):
    #     datim = list(time_idx)
    # indexvals = tuple(list(index.values()) + datim)

    try:
        db = pd.read_excel(fname, index_col=list(range(len(indexnames))))
    except:
        raise FileNotFoundError('Problem loading file ' + fname)

    ic(db)

    # select for the index values
    try:
        db = db.loc[indexvals, :]
    except:
        # indexvals not present
        raise KeyError('index ' + str(indexvals) + ' not found in database.')

    # date selection
    if time_idx==None or time_idx=='latest':
        db = db.sort_index().iloc[-1, :]
    elif time_idx=='last date':
        last_date = db['date'].max()
        db = db.loc[db['date']==last_date, :]
    elif time_idx == 'last combinations':
        # for every non-time index, only one entry should remain (time
        # index should be redundant)
        newdb = pd.DataFrame(
            index=pd.MultiIndex.from_product([[0]]*len(list(index.keys())),
                                             names=list(index.keys())),
            columns=db.columns)
        for dfidx, subdf in db.groupby(list(index.keys())):
            if len(subdf.index)>0:
                keepentry = subdf.iloc[-1, :]
                # does this keep working if there are multiple entries?
                newdb.loc[dfidx, :] = keepentry
        newdb.drop(index=tuple([0]*len(list(index.keys()))), inplace=True)
        db = newdb
    elif time_idx == 'all':
        pass

    return db


def plot_device_history(db_fname, device, plot_dir):
    """Plot the historic evolution of model parameters. For each
    laser, a plot with subplots for each parameter is generated, with
    laser powers as different plots in the subplot.

    Args:
        db_fname : str
            the filename of the database
        device : str
            the device name to plot (eg. 'Voyager')
        plot_dir : str
            the directory to save the plots in.
    """
    # there was a QT error on voyager (220726) - avoid it by using tkagg
    import matplotlib
    matplotlib.use('tkagg')

    index = {DEVICE_TAG: device}
    db = load_database(db_fname, index, 'all')
    for laser, laser_df in db.groupby(LASER_TAG):
        powers = laser_df.index.get_level_values(POWER_TAG).unique()
        params = laser_df.columns
        fig, ax = plt.subplots(nrows=len(params), sharex=True)
        for i, param in enumerate(params):
            for power, power_df in laser_df.groupby(POWER_TAG):
                dates = power_df.index.get_level_values('date')
                times = power_df.index.get_level_values('time')

                dt = [datetime.strptime(date+';'+time, '%Y-%m-%d;%H:%M')
                      for date, time in zip(dates, times)]
                ax[i].plot(
                    dt, power_df[param], marker='x',
                    label='power={:.1f}'.format(power))
            ax[i].set_ylabel(str(param))
        ax[0].legend()
        # ax[-1].set_xlabel('datetime')
        ax[-1].set_xticklabels(ax[-1].get_xticks(), rotation=30)
        plot_fname = os.path.join(
            plot_dir, 'history_{:s}.png'.format(str(laser)))
        fig.savefig(plot_fname)
        plt.close(fig)


def restart_database(db_fname):
    """Save a backup of the current database and restart with the
    latest parameters
    """
    whole_db = load_database(db_fname, index={}, time_idx='all')
    today = datetime.now().strftime('%Y-%m-%d')
    root, ext = os.path.splitext(db_fname)
    bkup_fname = os.path.join(root+'_'+today, ext)
    if os.path.exists(bkup_fname):
        raise ValueError('File already exists: {:s}'.format(bkup_fname))
    whole_db.to_excel(bkup_fname)
    last_entries = load_database(
        db_fname, index={}, time_idx='last combinations')
    last_entries.to_excel(db_fname)
