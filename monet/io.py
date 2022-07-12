#!/usr/bin/env python
"""
    monet/io.py
    ~~~~~~~~~~~

    File in/output operations

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import pandas as pd


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
    """
    indexnames = list(index.keys()) + ['date', 'time']
    datim = [datetime.now().strftime('%Y-%m-%d'),
             datetime.now().strftime('%H:%M')]
    indexvals = tuple(list(index.values()) + datim)
    try:
        db = pd.read_excel(fname, index_col=list(range(len(indexvals))))
    except Exception as e:
        logger.debug('error loading database: ', str(e))
        # print('error loading database: ', str(e))
        midx = pd.MultiIndex.from_tuples(
            [indexvals], names=list(indexnames))
        db = pd.DataFrame(
            index=midx, columns=list(cali_pars.keys()))

    db.loc[indexvals, :] = list(cali_pars.values())
    db.to_excel(fname)


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
    sb_select = load_database(fname, index, time_idx=time_idx)

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
        time_idx : None, 'latest', 'last date', or list, len 2
            loads either the latest (if time_idx is None or a string)
            or a specific date and time

    Returns:
        cali_pars : dict
            keys: parameter names, vals: calibration parameters
    """
    indexnames = list(index.keys()) + ['date', 'time']

    if time_idx==None or isinstance(time_idx, str):
        datim = [slice(None), slice(None)]
    elif (isinstance(time_idx, list) or isinstance(time_idx, tuple) and len(time_idx)==2):
        datim = list(time_idx)
    indexvals = tuple(list(index.values()) + datim)

    try:
        db = pd.read_excel(fname, index_col=list(range(len(indexnames))))
    except:
        return

    # select for the index values
    try:
        db = db.loc[indexvals, :]
    except:
        # indexvals not present
        return

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
            index=pd.MultiIndex(levels=indexnames), columns=db.columns)
        for dfidx, subdf in db.groupby(index.keys()):
            if len(subdf.index)>0:
                keepentry = subdf.iloc[-1, :]
                newdb.loc[keepentry.index, :] = keepentry
        db = newdb

    return db
