#!/usr/bin/env python
"""
monet/io.py
~~~~~~~~~~~

File in/output operations

:authors: Heinrich Grabmayr, 2022
:copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""

import json
import logging
import os
import shutil
import time
from datetime import datetime

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from icecream import ic

from monet import (
    DATABASE_INDEXLEVELS,
    DEVICE_TAG,
    LASER_TAG,
    POWER_TAG,
    POWERMETER_BFP,
    POWERMETER_SAMPLE,
    normalize_powermeter_type,
)
from monet.cache import _get_cache

FACTOR_SHEET = "factors"
FACTOR_INDEXLEVELS = [DEVICE_TAG, LASER_TAG, "date"]

logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)

# Exceptions that mean the server is unreachable (not a logical HTTP error)
_CONNECTION_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)

# Per-server timestamp of the last flush attempt that failed due to
# connectivity. Prevents hammering the server on every call when down.
_last_flush_failure: dict = {}
_FLUSH_COOLDOWN_SECS = 30


def _is_server_url(fname):
    """Check if fname is a server URL rather than a file path."""
    return fname.startswith("http://") or fname.startswith("https://")


def _flush_outbox(server_url: str) -> None:
    """Replay any queued outbox entries against the server.

    Called at the start of every HTTP operation so that offline-queued writes
    are delivered as soon as connectivity is restored.  Stops immediately on
    the first connection error (server still down) to avoid redundant retries.
    """
    import time as _time

    # Honour the cooldown to avoid spamming a server that is still down.
    # None means "never failed" — cooldown only applies after an actual
    # failure.
    last_fail = _last_flush_failure.get(server_url)
    if (
        last_fail is not None
        and _time.monotonic() - last_fail < _FLUSH_COOLDOWN_SECS
    ):
        return

    cache = _get_cache(server_url)
    pending = cache.get_pending_outbox()
    if not pending:
        return

    logger.info("Flushing %d outbox entries to %s", len(pending), server_url)
    for entry_id, endpoint, payload, local_key in pending:
        try:
            resp = requests.post(
                f"{server_url}{endpoint}", json=payload, timeout=10
            )
            resp.raise_for_status()

            # On success: update the local cache to reflect the server's state
            if endpoint == "/calibrations":
                rec = resp.json()
                cache.upsert_calibration(
                    {**rec, "parameters": payload["parameters"]}
                )
                # Remove the entry saved with a locally-generated
                # timestamp
                if local_key is not None:
                    cache.delete_calibrations(local_key)
            elif endpoint == "/factors":
                cache.upsert_factor(resp.json())
            # For '/calibrations/delete' the local cache was already
            # updated offline

            cache.remove_outbox_entry(entry_id)
            logger.debug("Synced outbox entry %d (%s)", entry_id, endpoint)

        except _CONNECTION_ERRORS as exc:
            # Server is still unreachable — record failure and stop
            cache.record_outbox_failure(entry_id, str(exc))
            _last_flush_failure[server_url] = _time.monotonic()
            logger.debug(
                "Outbox flush aborted — server still unreachable: %s", exc
            )
            return

        except Exception as exc:
            # Logical error on this specific entry (e.g. 4xx); skip and
            # continue
            cache.record_outbox_failure(entry_id, str(exc))
            logger.warning(
                "Outbox entry %d (%s) failed with server error: %s",
                entry_id,
                endpoint,
                exc,
            )


def _records_to_pandas(records: list, time_idx) -> "pd.Series | pd.DataFrame":
    """Convert calibration record dicts to the appropriate pandas type.

    Mirrors the structure returned by the server so that the offline cache path
    produces the same types as the online path.
    """
    if time_idx is None or time_idx == "latest":
        return pd.Series(records[0]["parameters"])

    rows = []
    index_tuples = []
    for rec in records:
        index_tuples.append(
            (
                rec["device_name"],
                rec["wavelength_nm"],
                rec["laser_power_mw"],
                rec["calibration_date"],
                rec["calibration_time"],
            )
        )
        rows.append(rec["parameters"])

    midx = pd.MultiIndex.from_tuples(index_tuples, names=DATABASE_INDEXLEVELS)
    df = pd.DataFrame(rows, index=midx)
    for col in df.columns:
        converted = pd.to_numeric(df[col], errors="coerce")
        if converted.notna().any():
            df[col] = converted
    return df


# ──────────────────────────────────────────────
# save_calibration
# ──────────────────────────────────────────────


def save_calibration(fname, index, cali_pars):
    """Save the calibration to the database.

    Parameters
    ----------
    fname : str
        File name of the database (Excel) or server URL.
    index : dict
        Index values for the database entry, e.g. microscope name,
        wavelength, laser power.
    cali_pars : dict
        Keys are parameter names, values the calibration parameters.

    Returns
    -------
    indexnames : list of str
        The names of indices in the database.
    indexvals : list of str
        The values of indices in the database.
    """
    if _is_server_url(fname):
        return _save_calibration_http(fname, index, cali_pars)
    return _save_calibration_excel(fname, index, cali_pars)


def _save_calibration_http(server_url, index, cali_pars):
    """Save calibration via HTTP server.

    Falls back to the local cache if the server is unreachable.
    """
    _flush_outbox(server_url)
    cache = _get_cache(server_url)
    try:
        resp = requests.post(
            f"{server_url}/calibrations",
            json={"index": index, "parameters": cali_pars},
            timeout=10,
        )
        resp.raise_for_status()
        record = resp.json()
        # Keep the local cache in sync with what the server stored
        cache.upsert_calibration({**record, "parameters": cali_pars})
    except _CONNECTION_ERRORS:
        # Server unreachable — save locally and queue for later sync
        local_date = datetime.now().strftime("%Y-%m-%d")
        local_time = datetime.now().strftime("%H:%M")
        record = {
            "device_name": index.get("name", ""),
            "wavelength_nm": float(index.get("wavelength [nm]", 0)),
            "laser_power_mw": float(index.get("laser_power [mW]", 0)),
            "calibration_date": local_date,
            "calibration_time": local_time,
        }
        cache.upsert_calibration({**record, "parameters": cali_pars})
        # local_key lets _flush_outbox clean up this offline entry after sync
        local_key = {
            "name": index.get("name"),
            "wavelength [nm]": index.get("wavelength [nm]"),
            "laser_power [mW]": index.get("laser_power [mW]"),
            "date": local_date,
            "time": local_time,
        }
        cache.add_to_outbox(
            "/calibrations",
            {"index": index, "parameters": cali_pars},
            local_key=local_key,
        )

    indexnames = DATABASE_INDEXLEVELS
    indexvals = (
        record["device_name"],
        record["wavelength_nm"],
        record["laser_power_mw"],
        record["calibration_date"],
        record["calibration_time"],
    )
    return indexnames, indexvals


def _save_calibration_excel(fname, index, cali_pars):
    """Save calibration to Excel file (original implementation)."""
    indexnames = list(index.keys()) + ["date", "time"]
    indexnames = DATABASE_INDEXLEVELS + list(
        set(indexnames) - set(DATABASE_INDEXLEVELS)
    )
    index["date"] = datetime.now().strftime("%Y-%m-%d")
    index["time"] = datetime.now().strftime("%H:%M")
    indexvals = tuple([index[k] for k in indexnames])
    if not os.path.exists(fname):
        logger.debug("Database file does not exist, creating it")
        midx = pd.MultiIndex.from_tuples([indexvals], names=list(indexnames))
        db = pd.DataFrame(index=midx, columns=list(cali_pars.keys()))
    else:
        tic = time.time()
        while True:
            if time.time() - tic > 10:
                logger.debug(
                    "Persistent problem loading database. Creating anew"
                )
                # print('error loading database: ', str(e))
                ic(indexnames)
                ic(indexvals)
                midx = pd.MultiIndex.from_tuples(
                    [indexvals], names=list(indexnames)
                )
                db = pd.DataFrame(index=midx, columns=list(cali_pars.keys()))
                break
            try:
                db = pd.read_excel(
                    fname, index_col=list(range(len(indexvals)))
                )
            except Exception as e:
                logger.debug(
                    "Problem loading database: "
                    + str(e)
                    + " Probably busy with separate read/write. Trying again."
                )
                time.sleep(0.05)
                continue
            else:
                break

    for k, v in cali_pars.items():
        db.loc[indexvals, k] = v
        db = db.sort_index()

    if os.path.exists(fname):
        with pd.ExcelWriter(
            fname, engine="openpyxl", mode="a", if_sheet_exists="replace"
        ) as writer:
            db.to_excel(writer)
    else:
        db.to_excel(fname)

    return indexnames, indexvals


# ──────────────────────────────────────────────
# load_calibration
# ──────────────────────────────────────────────


def load_calibration(fname, index, time_idx="latest"):
    """Load a calibration from the database.

    Parameters
    ----------
    fname : str
        File name of the database or server URL.
    index : dict
        Index values for the database entry, e.g. microscope name,
        wavelength, laser power. Keys are category names; values are
        single values or ``slice(None)``.
    time_idx : None, 'latest', or list, len 2
        Loads either the latest (if None or a string) or a specific date
        and time.

    Returns
    -------
    cali_pars : dict
        Keys are parameter names, values the calibration parameters.
    """
    db_select = load_database(fname, index, time_idx=time_idx)

    cali_pars = {}
    for col, val in zip(db_select.index, db_select.values):
        try:
            if not np.isnan(val):
                cali_pars[col] = val
        except (TypeError, ValueError):
            cali_pars[col] = val  # string columns pass through
    return cali_pars


# ──────────────────────────────────────────────
# load_database
# ──────────────────────────────────────────────


def load_database(fname, index, time_idx="last combinations"):
    """Load the database.

    Parameters
    ----------
    fname : str
        File name of the database or server URL.
    index : dict
        Index values for the database entry, e.g. microscope name,
        wavelength, laser power. Keys are category names; values are
        single values or ``slice(None)``.
    time_idx : None, 'latest', 'last date', or list, len 1 or 2
        Loads either the latest (if None or a string) or a specific date
        (and time).

    Returns
    -------
    cali_pars : dict
        Keys are parameter names, values the calibration parameters.
    """
    if _is_server_url(fname):
        return _load_database_http(fname, index, time_idx)
    return _load_database_excel(fname, index, time_idx)


def _load_database_http(server_url, index, time_idx):
    """Load database via HTTP server, with offline cache fallback.

    Returns a Series for time_idx='latest'/None, DataFrame otherwise.
    """
    # Convert slice(None) values to None for JSON serialisation
    json_index = {
        k: (None if isinstance(v, slice) and v == slice(None) else v)
        for k, v in index.items()
    }
    _flush_outbox(server_url)
    cache = _get_cache(server_url)
    try:
        resp = requests.post(
            f"{server_url}/calibrations/query",
            json={"index": json_index, "time_idx": time_idx},
            timeout=10,
        )
        resp.raise_for_status()
        records = resp.json()["records"]
        # Populate the local cache so reads work offline later
        for rec in records:
            cache.upsert_calibration(rec)
    except _CONNECTION_ERRORS:
        logger.warning(
            "Server unreachable — loading calibrations from local cache"
        )
        records = cache.query_calibrations(json_index, time_idx)

    if not records:
        raise KeyError(f"index {index} not found in database.")

    return _records_to_pandas(records, time_idx)


def _load_database_excel(fname, index, time_idx):
    """Load database from Excel file (original implementation)."""
    indexnames = DATABASE_INDEXLEVELS
    index_full = {name: slice(None) for name in indexnames}
    for n, v in index.items():
        index_full[n] = v
    index = index_full

    if isinstance(time_idx, list) or isinstance(time_idx, tuple):
        if len(time_idx) > 2:
            pass
        index["date"] = time_idx[0]
        if len(time_idx) > 1:
            index["time"] = time_idx[1]
    indexvals = tuple(list(index.values()))
    ic(index)

    try:
        db = pd.read_excel(fname, index_col=list(range(len(indexnames))))
    except Exception as e:
        raise FileNotFoundError(
            "Could not load database file {!r}: {}".format(fname, e)
        ) from e

    db = db.sort_index()
    ic(db)

    # select for the index values
    try:
        db = db.loc[indexvals, :]
    except Exception as e:
        raise KeyError(
            "Index {} not found in database {!r}. "
            "The device name, wavelength, or laser power may not match "
            "any existing calibration entry.".format(indexvals, fname)
        ) from e

    # date selection
    if time_idx is None or time_idx == "latest":
        # A fully-specified index (e.g. name + wavelength + power + date +
        # time, as written by save_calibration) collapses the selection to a
        # single-row Series — in that case the latest entry is already
        # selected. Only pick the last row when several remain.
        if getattr(db, "ndim", 1) == 2:
            db = db.sort_index().iloc[-1, :]
    elif time_idx == "last date":
        last_date = db.index.get_level_values("date").max()
        db = db.loc[db.index.get_level_values("date") == last_date, :]
    elif time_idx == "last combinations":
        # for every non-time index, only one entry should remain (time
        # index should be redundant)
        nontimedateidx = [k for k in index.keys() if k not in ["date", "time"]]
        newdb = db.copy()
        for dfidx, subdf in db.groupby(nontimedateidx):
            idxlen = len(subdf.index)
            for i, (idx, row) in enumerate(subdf.iterrows()):
                if i < idxlen - 1:
                    newdb.drop(idx, inplace=True)
        db = newdb
    elif time_idx == "all":
        pass

    return db


# ──────────────────────────────────────────────
# restart_database
# ──────────────────────────────────────────────


def restart_database(db_fname):
    """Save a backup of the current database and restart with the
    latest parameters
    """
    if _is_server_url(db_fname):
        return _restart_database_http(db_fname)
    return _restart_database_excel(db_fname)


def _restart_database_http(server_url):
    """Restart database via HTTP server."""
    resp = requests.post(f"{server_url}/database/restart", timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["backup_path"]


def _restart_database_excel(db_fname):
    """Restart database from Excel file (original implementation)."""
    today = datetime.now().strftime("%Y-%m-%d")
    root, ext = os.path.splitext(db_fname)
    bkup_fname = os.path.join(root + "_" + today, ext)
    if os.path.exists(bkup_fname):
        raise ValueError("File already exists: {:s}".format(bkup_fname))
    shutil.copy2(db_fname, bkup_fname)
    last_entries = load_database(
        db_fname, index={}, time_idx="last combinations"
    )
    last_entries.to_excel(db_fname)


# ──────────────────────────────────────────────
# delete_calibration
# ──────────────────────────────────────────────


def delete_calibration(fname, index):
    """Delete records matching index from the database.

    None values act as wildcards.

    Parameters
    ----------
    fname : str
        File name of the database or server URL.
    index : dict
        Index values to match for deletion. Use None values as wildcards.

    Returns
    -------
    int
        Number of deleted rows.
    """
    if _is_server_url(fname):
        return _delete_calibration_http(fname, index)
    return _delete_calibration_excel(fname, index)


def _delete_calibration_http(server_url, index):
    """Delete calibration records via HTTP server.

    Queues the deletion offline if the server is unreachable.
    """
    _flush_outbox(server_url)
    payload = {
        "device_name": index.get("name"),
        "wavelength_nm": index.get("wavelength [nm]"),
        "laser_power_mw": index.get("laser_power [mW]"),
        "calibration_date": index.get("date"),
        "calibration_time": index.get("time"),
    }
    cache = _get_cache(server_url)
    try:
        resp = requests.post(
            f"{server_url}/calibrations/delete", json=payload, timeout=10
        )
        resp.raise_for_status()
        count = resp.json()["deleted_count"]
        # Mirror the deletion in the local cache
        cache.delete_calibrations(index)
        return count
    except _CONNECTION_ERRORS:
        logger.warning(
            "Server unreachable — applying delete to local cache and "
            "queuing outbox"
        )
        count = cache.delete_calibrations(index)
        cache.add_to_outbox("/calibrations/delete", payload)
        return count


def _delete_calibration_excel(fname, index):
    """Delete calibration records from Excel file matching index.

    Returns number of deleted rows.
    """
    try:
        db = pd.read_excel(
            fname, index_col=list(range(len(DATABASE_INDEXLEVELS)))
        )
    except Exception as e:
        raise FileNotFoundError("Problem loading file " + fname) from e

    original_len = len(db)
    mask = pd.Series(True, index=db.index)

    name = index.get("name")
    if name is not None:
        mask &= db.index.get_level_values("name") == name

    wavelength = index.get("wavelength [nm]")
    if wavelength is not None:
        mask &= db.index.get_level_values("wavelength [nm]") == wavelength

    laser_power = index.get("laser_power [mW]")
    if laser_power is not None:
        mask &= db.index.get_level_values("laser_power [mW]") == laser_power

    date = index.get("date")
    if date is not None:
        mask &= db.index.get_level_values("date") == date

    time_val = index.get("time")
    if time_val is not None:
        mask &= db.index.get_level_values("time") == time_val

    db = db[~mask]
    db.to_excel(fname)
    return original_len - len(db)


# ──────────────────────────────────────────────
# Plotting functions (always client-side)
# ──────────────────────────────────────────────


def plot_device_history(db_fname, device, plot_dir):
    """Plot the historic evolution of model parameters. For each
    laser, a plot with subplots for each parameter is generated, with
    laser powers as different plots in the subplot.

    Parameters
    ----------
    db_fname : str
        The filename of the database.
    device : str
        The device name to plot (e.g. 'Voyager').
    plot_dir : str
        The directory to save the plots in. If None/empty, plotting is
        skipped (e.g. when no 'dest_calibration_plot' is configured).
    """
    if not plot_dir:
        logger.debug("No plot directory configured; skipping device history.")
        return
    plt.switch_backend("agg")

    index = {DEVICE_TAG: device}
    db = load_database(db_fname, index, "all")
    for laser, laser_df in db.groupby(LASER_TAG):
        params = laser_df.select_dtypes(include="number").columns
        if len(params) == 0:
            continue
        fig, ax = plt.subplots(nrows=len(params), sharex=True, squeeze=False)
        ax = ax[:, 0]
        for i, param in enumerate(params):
            for power, power_df in laser_df.groupby(POWER_TAG):
                dates = power_df.index.get_level_values("date")
                times = power_df.index.get_level_values("time")

                dt = [
                    datetime.strptime(f"{date};{time}", "%Y-%m-%d;%H:%M")
                    for date, time in zip(dates, times)
                ]
                ax[i].plot(
                    dt,
                    power_df.loc[:, param].values.flatten(),
                    marker="x",
                    label="power={:.1f}".format(power),
                )
            ax[i].set_ylabel(str(param))
        ax[0].legend()
        # ax[-1].set_xlabel('datetime')
        ax[-1].xaxis.set_major_locator(mdates.MonthLocator())
        ax[-1].xaxis.set_minor_locator(
            mdates.WeekdayLocator(byweekday=mdates.MO)
        )
        ax[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%b"))
        for label in ax[-1].get_xticklabels(which="major"):
            label.set(rotation=30, horizontalalignment="right")
        plot_fname = os.path.join(
            plot_dir, "history_{:s}.png".format(str(laser))
        )
        fig.set_size_inches((8, 7))
        fig.savefig(plot_fname)
        plt.close(fig)


def plot_device_amplitude_history(db_fname, device, plot_dir, analyzer):
    """Plot the historic evolution of model parameters. For each
    laser, a plot with subplots for each parameter is generated, with
    laser powers as different plots in the subplot.

    Parameters
    ----------
    db_fname : str
        The filename of the database.
    device : str
        The device name to plot (e.g. 'Voyager').
    plot_dir : str
        The directory to save the plots in. If None/empty, plotting is
        skipped (e.g. when no 'dest_calibration_plot' is configured).
    """
    if not plot_dir:
        logger.debug(
            "No plot directory configured; skipping amplitude history."
        )
        return
    plt.switch_backend("agg")

    index = {DEVICE_TAG: device}
    db = load_database(db_fname, index, "all")
    for laser, laser_df in db.groupby(LASER_TAG):
        fig, ax = plt.subplots(nrows=2, sharex=True)
        for power, power_df in laser_df.groupby(POWER_TAG):
            dates = power_df.index.get_level_values("date")
            times = power_df.index.get_level_values("time")

            dt = [
                datetime.strptime(f"{date};{time}", "%Y-%m-%d;%H:%M")
                for date, time in zip(dates, times)
            ]
            minpower = np.zeros(len(dates))
            maxpower = np.zeros(len(dates))
            for i, (idx, row) in enumerate(power_df.iterrows()):
                pars = {}
                for col in row.index:
                    val = row[col]
                    try:
                        if not np.isnan(val):
                            pars[col] = val
                    except (TypeError, ValueError):
                        pass  # skip non-numeric columns
                analyzer.load_model(pars)
                output_range = analyzer.output_range()
                minpower[i] = np.real(output_range[0])
                maxpower[i] = np.real(output_range[1])
            ax[0].plot(
                dt, minpower, marker="x", label="power={:.1f}".format(power)
            )
            ax[0].set_ylabel("Background [mW]")
            ax[1].plot(
                dt, maxpower, marker="x", label="power={:.1f}".format(power)
            )
            ax[1].set_ylabel("maximum power [mW]")
        ax[0].legend()
        # ax[-1].set_xlabel('datetime')
        ax[-1].xaxis.set_major_locator(mdates.MonthLocator())
        ax[-1].xaxis.set_minor_locator(
            mdates.WeekdayLocator(byweekday=mdates.MO)
        )
        ax[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%b"))
        for label in ax[-1].get_xticklabels(which="major"):
            label.set(rotation=30, horizontalalignment="right")
        plot_fname = os.path.join(
            plot_dir, "history_amplitude_{:s}.png".format(str(laser))
        )
        fig.set_size_inches((8, 7))
        fig.savefig(plot_fname)
        plt.close(fig)


def mad_outlier_mask(values, thresh=3.5):
    """Boolean mask of robust outliers via the median absolute deviation.

    Flags entries whose modified (MAD-based) z-score exceeds ``thresh``
    (default 3.5, the common Iglewicz–Hoaglin cutoff). Returns an all-False
    mask when there are fewer than three finite points or the MAD is zero, so
    callers never discard everything merely for lack of spread.

    Parameters
    ----------
    values : 1D array-like
        The values to test (e.g. fit residuals or transmission ratios).
    thresh : float
        Modified z-score above which a point counts as an outlier.

    Returns
    -------
    mask : np.ndarray of bool
        True where the corresponding value is an outlier.
    """
    arr = np.asarray(values, dtype=float)
    mask = np.zeros(arr.shape, dtype=bool)
    finite = np.isfinite(arr)
    if int(finite.sum()) < 3:
        return mask
    med = np.median(arr[finite])
    mad = np.median(np.abs(arr[finite] - med))
    if mad == 0:
        return mask
    # 0.6745 scales the MAD to the standard deviation of a normal sample.
    modified_z = 0.6745 * (arr - med) / mad
    return finite & (np.abs(modified_z) > thresh)


def flag_amplitude_outliers(lpwrs, amps, rel_thresh=0.02):
    """Indices of amplitude points that stray from the linear power trend.

    Amplitude (max measured power) vs. laser-power set-point is expected to be
    linear, so a single failed calibration shows up as a point off the line. A
    robust Theil–Sen line is fit (median of pairwise slopes, so the outlier
    cannot drag the fit toward itself the way least-squares would) and every
    point whose residual exceeds ``rel_thresh`` of its fitted value is flagged.

    Parameters
    ----------
    lpwrs : 1D array-like
        Laser-power set-points.
    amps : 1D array-like
        Measured amplitude (max power) at each set-point.
    rel_thresh : float
        Relative deviation from the fitted line above which a point counts as
        off-linear (default 0.02 = 2 %).

    Returns
    -------
    dict
        ``{index: relative_residual}`` for flagged points; empty for fewer
        than three points.
    """
    if len(amps) < 3:
        return {}
    x = np.asarray(lpwrs, dtype=float)
    y = np.asarray(amps, dtype=float)

    # Theil–Sen robust slope: median of all pairwise slopes.
    slopes = []
    n = len(x)
    for i in range(n):
        for j in range(i + 1, n):
            dx = x[j] - x[i]
            if dx != 0:
                slopes.append((y[j] - y[i]) / dx)
    slope = float(np.median(slopes)) if slopes else 0.0
    intercept = float(np.median(y - slope * x))
    fit = slope * x + intercept
    resid = y - fit

    out = {}
    for i in range(n):
        denom = abs(fit[i])
        if denom < 1e-12:
            denom = abs(y[i]) if abs(y[i]) > 1e-12 else 1.0
        rel = abs(resid[i]) / denom
        if rel > rel_thresh:
            out[int(i)] = float(rel)
    return out


def load_amplitude_history(
    db_fname, device, analyzer, laser=None, powermeter_type=None, max_runs=3
):
    """Amplitude (max fitted power) vs laser power for recent runs.

    Groups a device's calibrations by date (one calibration run per day) and,
    for the most recent ``max_runs`` days, evaluates each fitted model's
    maximum output power at every laser-power set-point. Used to draw previous
    runs as thin reference lines behind the live amplitude plot so the user can
    judge whether the system is still stable.

    Parameters
    ----------
    db_fname : str
        Excel database path or server URL.
    device : str
        Device / microscope name.
    analyzer : object
        An analysis instance with ``load_model`` and ``output_range``; reused
        across rows, so it must not be shared with a live calibration.
    laser : int/str or None
        If given, restrict to this wavelength.
    powermeter_type : str or None
        If given, restrict to calibrations taken at this power-meter position
        ('sample' or 'bfp'), so the overlay is comparable to the live run.
        Rows predating the ``powermeter_type`` column are always included.
    max_runs : int
        Number of most-recent dates to return per laser.

    Returns
    -------
    history : dict
        ``{laser (str): [{"date": str, "amplitudes": {lpwr: maxpower}}]}``,
        each list ordered oldest → newest with at most ``max_runs`` entries.
        Empty dict on any load error.
    """
    index = {DEVICE_TAG: device}
    if laser is not None:
        try:
            index[LASER_TAG] = int(laser)
        except (ValueError, TypeError):
            index[LASER_TAG] = laser
    try:
        db = load_database(db_fname, index, time_idx="all")
    except Exception as exc:
        logger.debug("load_amplitude_history: could not load db: %s", exc)
        return {}
    if not hasattr(db, "iterrows") or db.empty:
        return {}

    want_type = (
        normalize_powermeter_type(powermeter_type)
        if powermeter_type is not None
        else None
    )

    history = {}
    for las, laser_df in db.groupby(LASER_TAG):
        # Keep only the requested power-meter position when the column exists;
        # legacy rows without it are treated as matching.
        if want_type is not None and "powermeter_type" in laser_df.columns:
            pm_norm = laser_df["powermeter_type"].map(
                lambda v: normalize_powermeter_type(v) if pd.notna(v) else None
            )
            laser_df = laser_df.loc[(pm_norm == want_type) | pm_norm.isna()]
        if laser_df.empty:
            continue

        dates = laser_df.index.get_level_values("date")
        date_strs = np.array([str(d)[:10] for d in dates])
        recent_dates = sorted(set(date_strs))[-max_runs:]

        runs = []
        for date in recent_dates:
            date_df = laser_df.loc[date_strs == date]
            amplitudes = {}
            for lpwr, lpwr_df in date_df.groupby(
                date_df.index.get_level_values(POWER_TAG)
            ):
                row = lpwr_df.iloc[-1]  # latest time for this power level
                pars = {}
                for col in row.index:
                    val = row[col]
                    try:
                        if not np.isnan(val):
                            pars[col] = val
                    except (TypeError, ValueError):
                        pass
                try:
                    analyzer.load_model(pars)
                    maxpower = float(np.real(analyzer.output_range()[1]))
                except Exception:
                    continue
                amplitudes[float(lpwr)] = maxpower
            if amplitudes:
                runs.append({"date": date, "amplitudes": amplitudes})
        if runs:
            history[str(las)] = runs
    return history


# ──────────────────────────────────────────────
# Powermeter correction factors
# ──────────────────────────────────────────────


def compute_and_save_factor(db_fname, device, laser, ana_config):
    """Compute and save the objective transmission factor.

    Uses paired back focal plane (BFP) vs sample-plane calibrations
    recorded on the same day.

    transmission_objective = P_sample / P_bfp, sampled at 50 attenuator
    positions per common laser-power level. Saved to the 'factors' sheet.
    Silently skipped for HTTP databases.

    Parameters
    ----------
    db_fname : str
        Excel database path.
    device : str
        Device name.
    laser : int or str
        Laser wavelength.
    ana_config : dict
        Analysis config with 'classpath' and 'init_kwargs'.
    """
    if not _is_server_url(db_fname) and not os.path.exists(db_fname):
        return

    today = datetime.now().strftime("%Y-%m-%d")

    try:
        if _is_server_url(db_fname):
            index = {DEVICE_TAG: device, LASER_TAG: int(laser)}
            db = load_database(db_fname, index, time_idx="all")
        else:
            # Read directly to avoid _load_database_excel's fragile slice-based
            # loc selection which can silently return empty for type
            # mismatches.
            db = pd.read_excel(
                db_fname,
                sheet_name=0,
                index_col=list(range(len(DATABASE_INDEXLEVELS))),
            )
            device_mask = db.index.get_level_values(DEVICE_TAG) == device
            laser_float = float(int(laser))
            try:
                laser_mask = (
                    db.index.get_level_values(LASER_TAG).astype(float)
                    == laser_float
                )
            except Exception:
                laser_mask = db.index.get_level_values(LASER_TAG) == int(laser)
            db = db.loc[device_mask & laser_mask]
    except Exception as exc:
        logger.warning("compute_and_save_factor: could not load db: %s", exc)
        return

    if db.empty:
        logger.warning(
            "compute_and_save_factor: no calibrations found for %s / %s nm",
            device,
            laser,
        )
        return
    if "powermeter_type" not in db.columns:
        logger.warning(
            "compute_and_save_factor: powermeter_type column missing — "
            "calibrations were likely recorded before this feature was added"
        )
        return

    # Filter to today — normalize dates to 'YYYY-MM-DD' strings regardless of
    # whether Excel stored them as strings or Timestamps.
    if "date" in db.index.names:
        dates_str = [str(d)[:10] for d in db.index.get_level_values("date")]
        db = db.loc[[d == today for d in dates_str]]
    if db.empty:
        logger.warning(
            "compute_and_save_factor: no calibrations for today (%s) for "
            "%s / %s nm — both types must be calibrated on the same day",
            today,
            device,
            laser,
        )
        return

    pm_type_norm = db["powermeter_type"].map(normalize_powermeter_type)
    db_manual = db.loc[pm_type_norm == POWERMETER_SAMPLE]
    db_beampath = db.loc[pm_type_norm == POWERMETER_BFP]
    if db_manual.empty or db_beampath.empty:
        logger.warning(
            "compute_and_save_factor: need both sample-plane and BFP "
            "calibrations on the same day; found sample=%d bfp=%d",
            len(db_manual),
            len(db_beampath),
        )
        return

    from monet.util import load_class

    manual_lpwrs = set(db_manual.index.get_level_values(POWER_TAG))
    beampath_lpwrs = set(db_beampath.index.get_level_values(POWER_TAG))
    common_lpwrs = manual_lpwrs & beampath_lpwrs
    if not common_lpwrs:
        logger.warning(
            "compute_and_save_factor: no common laser power levels between "
            "manual %s and beampath %s",
            manual_lpwrs,
            beampath_lpwrs,
        )
        return

    att_min = ana_config["init_kwargs"].get("min", 0)
    att_max = ana_config["init_kwargs"].get("max", 180)
    positions = np.linspace(att_min, att_max, 50)

    all_ratios = []
    for lpwr in common_lpwrs:
        manual_rows = db_manual.loc[
            db_manual.index.get_level_values(POWER_TAG) == lpwr
        ]
        beampath_rows = db_beampath.loc[
            db_beampath.index.get_level_values(POWER_TAG) == lpwr
        ]

        manual_pars = {}
        beampath_pars = {}
        for col in manual_rows.columns:
            val = manual_rows.iloc[-1][col]
            try:
                if not np.isnan(val):
                    manual_pars[col] = val
            except (TypeError, ValueError):
                pass
        for col in beampath_rows.columns:
            val = beampath_rows.iloc[-1][col]
            try:
                if not np.isnan(val):
                    beampath_pars[col] = val
            except (TypeError, ValueError):
                pass

        try:
            ana_m = load_class(
                ana_config["classpath"], ana_config["init_kwargs"]
            )
            ana_m.load_model(manual_pars)
            ana_b = load_class(
                ana_config["classpath"], ana_config["init_kwargs"]
            )
            ana_b.load_model(beampath_pars)
        except Exception as exc:
            logger.warning(
                "compute_and_save_factor: analyzer error at %s mW: %s",
                lpwr,
                exc,
            )
            continue

        for pos in positions:
            try:
                p_m = ana_m.estimate_power(pos)
                p_b = ana_b.estimate_power(pos)
                if p_m > 0 and p_b > 0:
                    all_ratios.append(p_m / p_b)
            except Exception:
                pass

    if not all_ratios:
        logger.warning(
            "compute_and_save_factor: no valid power ratios computed "
            "(all powers were zero or negative at sampled positions)"
        )
        return

    # A single failed calibration among the pooled ratios would skew the mean;
    # drop robust outliers (a failed run shows up as a cluster far from the
    # median) before averaging. Never drop everything — fall back to the full
    # set if the mask would empty it.
    ratios = np.asarray(all_ratios, dtype=float)
    outliers = mad_outlier_mask(ratios, thresh=3.5)
    kept = ratios[~outliers]
    n_dropped = int(outliers.sum())
    if kept.size == 0:
        kept = ratios
        n_dropped = 0

    factor_mean = float(np.mean(kept))
    factor_std = float(np.std(kept))
    n_points = int(kept.size)
    logger.debug(
        "transmission_objective %s/%s: mean=%.4f std=%.4f n=%d "
        "(dropped %d outlier ratio(s))",
        device,
        laser,
        factor_mean,
        factor_std,
        n_points,
        n_dropped,
    )
    if _is_server_url(db_fname):
        _save_factor_http(
            db_fname, device, laser, today, factor_mean, factor_std, n_points
        )
    else:
        _save_factor_excel(
            db_fname, device, laser, today, factor_mean, factor_std, n_points
        )


def _row_model_pars(row):
    """Numeric model parameters from a calibration row (drop NaNs/strings)."""
    pars = {}
    for col in row.index:
        val = row[col]
        try:
            if not np.isnan(val):
                pars[col] = val
        except (TypeError, ValueError):
            pass
    return pars


def compute_pair_factor(sample_pars, bfp_pars, ana_config):
    """Robust P_sample / P_bfp factor from two calibrations' model params.

    Rebuilds both models, samples 50 attenuator positions across the analysis
    range, forms the sample/BFP power ratio at each and returns the MAD-robust
    mean as ``(factor, n_kept)``; ``(None, 0)`` if it cannot be evaluated.
    """
    from monet.util import load_class

    att_min = ana_config["init_kwargs"].get("min", 0)
    att_max = ana_config["init_kwargs"].get("max", 180)
    positions = np.linspace(att_min, att_max, 50)
    try:
        ana_s = load_class(ana_config["classpath"], ana_config["init_kwargs"])
        ana_s.load_model(sample_pars)
        ana_b = load_class(ana_config["classpath"], ana_config["init_kwargs"])
        ana_b.load_model(bfp_pars)
    except Exception:
        return None, 0
    ratios = []
    for pos in positions:
        try:
            p_s = ana_s.estimate_power(pos)
            p_b = ana_b.estimate_power(pos)
            if p_s > 0 and p_b > 0:
                ratios.append(p_s / p_b)
        except Exception:
            pass
    if not ratios:
        return None, 0
    arr = np.asarray(ratios, dtype=float)
    keep = arr[~mad_outlier_mask(arr, thresh=3.5)]
    if keep.size == 0:
        keep = arr
    return float(np.mean(keep)), int(keep.size)


def _pair_factor(df_sample, df_bfp, lpwr, ana_config):
    """Robust P_sample / P_bfp factor at one power level.

    Returns ``(factor, n_kept)`` from the latest sample-plane and BFP
    calibration at ``lpwr``; ``(None, 0)`` if the pair cannot be evaluated.
    """
    s_rows = df_sample.loc[df_sample.index.get_level_values(POWER_TAG) == lpwr]
    b_rows = df_bfp.loc[df_bfp.index.get_level_values(POWER_TAG) == lpwr]
    if s_rows.empty or b_rows.empty:
        return None, 0
    return compute_pair_factor(
        _row_model_pars(s_rows.iloc[-1]),
        _row_model_pars(b_rows.iloc[-1]),
        ana_config,
    )


def compute_factor_breakdown(db_fname, device, ana_config, laser=None):
    """Per-input objective transmission factors, for visualization.

    Where :func:`compute_and_save_factor` pools every P_sample / P_bfp ratio
    into one saved number per device/laser/day, this returns a *separate*
    factor for each (date, wavelength, laser power) that has paired
    sample-plane and BFP calibrations, so the factor's stability across those
    inputs can be plotted.

    Parameters
    ----------
    db_fname : str
        Excel database path or server URL.
    device : str
        Device / microscope name.
    ana_config : dict
        Analysis config with 'classpath' and 'init_kwargs'.
    laser : int/str or None
        Restrict to one wavelength.

    Returns
    -------
    df : pandas DataFrame
        Columns ``date, wavelength, laser_power, factor, n_points``; empty if
        nothing can be paired.
    """
    cols = ["date", "wavelength", "laser_power", "factor", "n_points"]
    empty = pd.DataFrame(columns=cols)

    try:
        if _is_server_url(db_fname):
            index = {DEVICE_TAG: device}
            if laser is not None:
                index[LASER_TAG] = int(laser)
            db = load_database(db_fname, index, time_idx="all")
        else:
            if not os.path.exists(db_fname):
                return empty
            db = pd.read_excel(
                db_fname,
                sheet_name=0,
                index_col=list(range(len(DATABASE_INDEXLEVELS))),
            )
            db = db.loc[db.index.get_level_values(DEVICE_TAG) == device]
            if laser is not None:
                try:
                    lmask = db.index.get_level_values(LASER_TAG).astype(
                        float
                    ) == float(int(laser))
                except Exception:
                    lmask = db.index.get_level_values(LASER_TAG) == int(laser)
                db = db.loc[lmask]
    except Exception as exc:
        logger.debug("compute_factor_breakdown: load failed: %s", exc)
        return empty

    if not hasattr(db, "iterrows") or db.empty:
        return empty
    if "powermeter_type" not in db.columns:
        return empty

    rows = []
    for las, laser_df in db.groupby(LASER_TAG):
        dates = np.array(
            [str(d)[:10] for d in laser_df.index.get_level_values("date")]
        )
        for date in sorted(set(dates)):
            date_df = laser_df.loc[dates == date]
            pm_norm = date_df["powermeter_type"].map(normalize_powermeter_type)
            df_sample = date_df.loc[pm_norm == POWERMETER_SAMPLE]
            df_bfp = date_df.loc[pm_norm == POWERMETER_BFP]
            if df_sample.empty or df_bfp.empty:
                continue
            common = set(df_sample.index.get_level_values(POWER_TAG)) & set(
                df_bfp.index.get_level_values(POWER_TAG)
            )
            for lpwr in sorted(common):
                factor, n = _pair_factor(df_sample, df_bfp, lpwr, ana_config)
                if factor is not None:
                    rows.append(
                        {
                            "date": date,
                            "wavelength": float(las),
                            "laser_power": float(lpwr),
                            "factor": factor,
                            "n_points": n,
                        }
                    )
    if not rows:
        return empty
    return pd.DataFrame(rows, columns=cols)


# ──────────────────────────────────────────────
# Manually-selected transmission-factor pairs
#
# An operator-curated overlay: the user picks exactly which sample-plane and
# BFP calibrations to pair for the objective transmission factor, instead of
# relying on same-day auto-pairing. Persisted in a local JSON sidecar (next to
# the Excel database, or in the HTTP cache directory for server databases) so
# no shared-database schema is touched, and used to drive the factor plot.
# ──────────────────────────────────────────────

FACTOR_PAIR_KEYS = (
    "device",
    "wavelength",
    "laser_power",
    "date",
    "sample_time",
    "bfp_time",
)
FACTOR_PAIR_COLUMNS = [
    "date",
    "wavelength",
    "laser_power",
    "factor",
    "n_points",
    "sample_time",
    "bfp_time",
    "device",
]


def _factor_pairs_path(db_fname):
    """Local JSON path holding the manually-selected factor pairs."""
    if _is_server_url(db_fname):
        import hashlib

        import monet.cache as _cache

        h = hashlib.md5(db_fname.encode()).hexdigest()[:8]
        return os.path.join(
            str(_cache._DEFAULT_CACHE_DIR), "factor_pairs_{}.json".format(h)
        )
    return os.path.splitext(db_fname)[0] + ".factor_pairs.json"


def _pair_key(pair):
    return tuple(pair.get(k) for k in FACTOR_PAIR_KEYS)


def _load_factor_pairs_raw(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.debug("could not read factor pairs %s: %s", path, exc)
        return []


def _write_factor_pairs_raw(path, pairs):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(pairs, f, indent=2)


def save_factor_pair(db_fname, pair):
    """Persist a manually-selected transmission-factor pair (upsert by key).

    Parameters
    ----------
    db_fname : str
        Database path / URL the pairs belong to (locates the sidecar).
    pair : dict
        Keys ``device, wavelength, laser_power, date, sample_time, bfp_time,
        factor, n_points``.
    """
    path = _factor_pairs_path(db_fname)
    pairs = _load_factor_pairs_raw(path)
    key = _pair_key(pair)
    pairs = [p for p in pairs if _pair_key(p) != key]
    pairs.append(pair)
    _write_factor_pairs_raw(path, pairs)


def delete_factor_pair(db_fname, pair):
    """Remove the stored pair whose key matches ``pair``. Returns count."""
    path = _factor_pairs_path(db_fname)
    pairs = _load_factor_pairs_raw(path)
    key = _pair_key(pair)
    kept = [p for p in pairs if _pair_key(p) != key]
    if len(kept) != len(pairs):
        _write_factor_pairs_raw(path, kept)
    return len(pairs) - len(kept)


def load_factor_pairs(db_fname, device=None):
    """Load manually-selected transmission-factor pairs as a DataFrame.

    Columns match :data:`FACTOR_PAIR_COLUMNS`; empty DataFrame if none.
    """
    pairs = _load_factor_pairs_raw(_factor_pairs_path(db_fname))
    if device is not None:
        pairs = [p for p in pairs if p.get("device") == device]
    if not pairs:
        return pd.DataFrame(columns=FACTOR_PAIR_COLUMNS)
    return pd.DataFrame(pairs, columns=FACTOR_PAIR_COLUMNS)


def save_transmission_factor(
    db_fname, device, laser, date, factor_mean, factor_std, n_points
):
    """Write a transmission_objective factor to the DB (Excel sheet or HTTP).

    Public wrapper so a manually-computed pair can update the same factor store
    that :func:`compute_and_save_factor` writes and that the power-projection
    path (:mod:`monet.control`) reads.
    """
    if _is_server_url(db_fname):
        _save_factor_http(
            db_fname, device, laser, date, factor_mean, factor_std, n_points
        )
    else:
        _save_factor_excel(
            db_fname, device, laser, date, factor_mean, factor_std, n_points
        )


def _save_factor_http(
    server_url, device, laser, date, factor_mean, factor_std, n_points
):
    """Save transmission_objective factor via HTTP, offline fallback."""
    payload = {
        "device_name": device,
        "wavelength_nm": int(laser),
        "calibration_date": date,
        "transmission_objective_mean": factor_mean,
        "transmission_objective_std": factor_std,
        "n_points": n_points,
    }
    cache = _get_cache(server_url)
    try:
        resp = requests.post(f"{server_url}/factors", json=payload, timeout=10)
        resp.raise_for_status()
        cache.upsert_factor(payload)
    except _CONNECTION_ERRORS:
        logger.warning(
            "Server unreachable — saving factor to local cache and outbox"
        )
        cache.upsert_factor(payload)
        cache.add_to_outbox("/factors", payload)
    except Exception as exc:
        logger.warning("Failed to save factor via HTTP: %s", exc)


def _save_factor_excel(
    db_fname, device, laser, date, factor_mean, factor_std, n_points
):
    """Write or update a transmission_objective row in the 'factors' sheet."""
    index_key = (device, int(laser), date)
    try:
        if os.path.exists(db_fname):
            try:
                df = pd.read_excel(
                    db_fname,
                    sheet_name=FACTOR_SHEET,
                    index_col=list(range(len(FACTOR_INDEXLEVELS))),
                )
            except Exception:
                df = pd.DataFrame(
                    columns=[
                        "transmission_objective_mean",
                        "transmission_objective_std",
                        "n_points",
                    ],
                    index=pd.MultiIndex.from_tuples(
                        [], names=FACTOR_INDEXLEVELS
                    ),
                )
        else:
            df = pd.DataFrame(
                columns=[
                    "transmission_objective_mean",
                    "transmission_objective_std",
                    "n_points",
                ],
                index=pd.MultiIndex.from_tuples([], names=FACTOR_INDEXLEVELS),
            )

        df.loc[index_key, "transmission_objective_mean"] = factor_mean
        df.loc[index_key, "transmission_objective_std"] = factor_std
        df.loc[index_key, "n_points"] = n_points
        df.index.names = FACTOR_INDEXLEVELS

        with pd.ExcelWriter(
            db_fname, engine="openpyxl", mode="a", if_sheet_exists="replace"
        ) as writer:
            df.to_excel(writer, sheet_name=FACTOR_SHEET)
    except Exception as exc:
        logger.warning("Failed to save factor: %s", exc)


def load_factors(db_fname, device=None, laser=None):
    """Load powermeter correction factors from the database.

    Parameters
    ----------
    db_fname : str
        Excel database path or server URL.
    device : str or None
        Filter by device name.
    laser : int/str or None
        Filter by laser wavelength.

    Returns
    -------
    df : pandas DataFrame
        Indexed by (device, wavelength, date) with columns
        transmission_objective_mean, transmission_objective_std and
        n_points. Empty DataFrame if not found.
    """
    if _is_server_url(db_fname):
        _flush_outbox(db_fname)
        payload: dict = {}
        if device is not None:
            payload["device_name"] = device
        if laser is not None:
            try:
                payload["wavelength_nm"] = float(int(laser))
            except (ValueError, TypeError):
                pass
        cache = _get_cache(db_fname)
        try:
            resp = requests.post(
                f"{db_fname}/factors/query", json=payload, timeout=10
            )
            resp.raise_for_status()
            records = resp.json().get("records", [])
            for r in records:
                cache.upsert_factor(r)
        except _CONNECTION_ERRORS:
            logger.warning(
                "Server unreachable — loading factors from local cache"
            )
            records = cache.query_factors(device, laser)
        except Exception as exc:
            logger.debug("load_factors HTTP error: %s", exc)
            return pd.DataFrame()
        if not records:
            return pd.DataFrame()
        rows = []
        index_tuples = []
        for r in records:
            index_tuples.append(
                (r["device_name"], r["wavelength_nm"], r["calibration_date"])
            )
            rows.append(
                {
                    "transmission_objective_mean": r[
                        "transmission_objective_mean"
                    ],
                    "transmission_objective_std": r[
                        "transmission_objective_std"
                    ],
                    "n_points": r["n_points"],
                }
            )
        midx = pd.MultiIndex.from_tuples(
            index_tuples, names=FACTOR_INDEXLEVELS
        )
        return pd.DataFrame(rows, index=midx)
    if not os.path.exists(db_fname):
        return pd.DataFrame()
    try:
        df = pd.read_excel(
            db_fname,
            sheet_name=FACTOR_SHEET,
            index_col=list(range(len(FACTOR_INDEXLEVELS))),
        )
    except Exception:
        return pd.DataFrame()

    if device is not None:
        mask = df.index.get_level_values(DEVICE_TAG) == device
        df = df.loc[mask]
    if laser is not None:
        try:
            laser = int(laser)
        except (ValueError, TypeError):
            pass
        mask = df.index.get_level_values(LASER_TAG) == laser
        df = df.loc[mask]
    return df
