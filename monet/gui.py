#!/usr/bin/env python
"""
monet/gui.py
~~~~~~~~~~~~

PyQt6 graphical interface for Monet. Provides tabs for calibration,
laser/attenuator adjustment, power setting, and database management.

:authors: Heinrich Grabmayr, 2024
:copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""

import json
import logging

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import monet.io as io
from monet import (
    CONFIGS,
    POWERMETER_BFP,
    POWERMETER_SAMPLE,
    PROTOCOLS,
)
from monet import __version__ as _monet_version
from monet.control import run_power_feedback
from monet.util import update_mm_acquisition_comment

# Window-title prefix, e.g. 'Monet v0.3.3'. Falls back to plain 'Monet' when
# the package metadata is unavailable (running from an uninstalled source
# tree).
_TITLE_BASE = (
    'Monet'
    if _monet_version == 'unknown'
    else 'Monet v{}'.format(_monet_version)
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------


class CalibrationWorker(QThread):
    """Runs CalibrationProtocol2D.run_protocol() in a background thread."""

    progress = pyqtSignal(int, int, object, object)  # step, total, laser, lpwr
    log_message = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(
        self,
        pc,
        laser_filter,
        dry_run,
        wait_time=0.1,
        switch_time=10,
        powermeter_type=POWERMETER_SAMPLE,
    ):
        super().__init__()
        self._pc = pc
        self._laser_filter = laser_filter
        self._dry_run = dry_run
        self._wait_time = wait_time
        self._switch_time = switch_time
        self._powermeter_type = powermeter_type
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True

    def run(self):
        def _progress_callback(step, total, laser, lpwr):
            if self._cancel_requested:
                raise InterruptedError('Calibration cancelled by user.')
            self.progress.emit(step, total, laser, lpwr)
            self.log_message.emit(
                f'Step {step}/{total}: laser {laser} nm at {lpwr} mW done.'
            )

        try:
            self._pc.run_protocol(
                wait_time=self._wait_time,
                switch_time=self._switch_time,
                laser_filter=self._laser_filter,
                dry_run=self._dry_run,
                progress_callback=_progress_callback,
                manage_laser_state=False,
                powermeter_type=self._powermeter_type,
            )
        except InterruptedError:
            self.log_message.emit('Calibration cancelled.')
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.finished.emit()


class ConnectWorker(QThread):
    """Connects to a microscope configuration in a background thread."""

    connected = pyqtSignal(object)  # calibration protocol object
    warning = pyqtSignal(str)  # non-fatal warning (e.g. no powermeter)
    error = pyqtSignal(str)

    def __init__(self, name, config, protocol, old_pc=None):
        super().__init__()
        self._name = name
        self._config = config
        self._protocol = protocol
        self._old_pc = old_pc

    def run(self):
        import monet.calibrate as mca

        try:
            # Release the previous connection first so its serial ports /
            # SDK sessions are freed; otherwise re-opening the same hardware
            # (e.g. after switching on another laser) fails with the ports
            # still held by the old instance. Done here (worker thread) so
            # the potentially-blocking close does not freeze the UI.
            if self._old_pc is not None:
                try:
                    self._old_pc.disconnect()
                except Exception:
                    logging.getLogger(__name__).debug(
                        'old connection teardown failed', exc_info=True
                    )

            if self._protocol:
                pc = mca.CalibrationProtocol2D(self._config, self._protocol)
            else:
                pc = mca.CalibrationProtocol1D(self._config)
            if not getattr(pc, 'powermeter_available', True):
                msg = (
                    'PowerMeter not available — calibration and power '
                    'measurement disabled.'
                )
                detail = getattr(pc, 'powermeter_error', None)
                if detail:
                    msg += '\n\nReason: ' + detail
                self.warning.emit(msg)
            self.connected.emit(pc)
        except Exception as exc:
            self.error.emit(str(exc))


class GenericWorker(QThread):
    """Runs any callable in a background thread.

    Emits result(object) on success, error(str) on failure.
    The function's return value is passed to result; None is emitted if the
    function returns nothing.  progress(object) is available for intermediate
    updates emitted during the run.
    """

    result = pyqtSignal(object)
    progress = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, func):
        super().__init__()
        self._func = func

    def run(self):
        try:
            val = self._func()
            self.result.emit(val)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Feedback live-plot dialog
# ---------------------------------------------------------------------------


class FeedbackPlotDialog(QDialog):
    """Non-modal dialog showing measured power and setpoint vs. iteration
    during the feedback control loop.  Updated via add_point() which is
    connected to GenericWorker.progress (queued cross-thread connection).
    """

    def __init__(self, target_pwr, max_dev_pct, mode, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Feedback — live progress')
        self.setModal(False)
        self.resize(540, 380)

        self._target = target_pwr
        self._tol = max_dev_pct
        self._mode = mode  # 'fixed_laser' or 'fixed_attenuator'
        self._iters = []
        self._measured_vals = []
        self._setpoint_vals = []

        layout = QVBoxLayout(self)

        try:
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure

            self._fig = Figure(figsize=(5.4, 3.5), tight_layout=True)
            self._ax = self._fig.add_subplot(111)
            self._canvas = FigureCanvasQTAgg(self._fig)
            layout.addWidget(self._canvas)
            self._has_mpl = True
            self._init_plot()
        except Exception:
            self._has_mpl = False
            layout.addWidget(
                QLabel('matplotlib not available — cannot show live plot.')
            )

        btn = QPushButton('Close')
        btn.clicked.connect(self.close)
        layout.addWidget(btn)

    def _init_plot(self):
        ax = self._ax
        lo = self._target * (1.0 - self._tol / 100.0)
        hi = self._target * (1.0 + self._tol / 100.0)

        ax.axhline(
            self._target,
            color='green',
            linestyle='--',
            linewidth=1.5,
            label=f'Target ({self._target:.2f} mW)',
            zorder=2,
        )
        ax.axhspan(
            lo, hi, alpha=0.15, color='green', label=f'±{self._tol:.1f}% band'
        )

        (self._line_meas,) = ax.plot(
            [], [], 'o-', color='royalblue', label='Measured', zorder=3
        )
        # Setpoint line only meaningful for attenuator mode (PI corrects
        # target)
        (self._line_set,) = ax.plot(
            [],
            [],
            's--',
            color='darkorange',
            alpha=0.7,
            label='PI setpoint',
            visible=(self._mode == 'fixed_laser'),
        )

        ax.set_xlabel('Iteration')
        ax.set_ylabel('Power (mW)')
        ax.set_title('Feedback convergence')
        ax.legend(loc='best', fontsize=8)
        self._canvas.draw()

    def add_point(self, data):
        """Slot connected to GenericWorker.progress (called on main thread)."""
        if not self._has_mpl:
            return
        iteration, setpoint, measured = data
        self._iters.append(iteration)
        self._measured_vals.append(measured)
        self._setpoint_vals.append(setpoint)

        self._line_meas.set_data(self._iters, self._measured_vals)
        self._line_set.set_data(self._iters, self._setpoint_vals)

        # Auto-scale y to data + target band
        all_y = self._measured_vals + self._setpoint_vals + [self._target]
        margin = max(abs(self._target) * 0.05, 0.01)
        self._ax.set_xlim(-0.5, max(self._iters) + 0.5)
        self._ax.set_ylim(min(all_y) - margin, max(all_y) + margin)

        self._canvas.draw_idle()


# ---------------------------------------------------------------------------
# Tab 1 — Calibrate
# ---------------------------------------------------------------------------


class CalibrateTab(QWidget):
    """Tab for running calibration protocols."""

    # Status messages are emitted via the `status` signal so the tab can be
    # embedded anywhere — the host connects it to its own status bar.
    status = pyqtSignal(str, int)
    calibration_started = pyqtSignal()
    calibration_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pc = None
        self._worker = None
        self._checkboxes = {}
        self._build_ui()

    def _emit_status(self, msg, timeout_ms=0):
        """Emit a status message; ``timeout_ms=0`` means persistent."""
        self.status.emit(msg, timeout_ms)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Wavelength selection group
        self._wl_group = QGroupBox('Wavelengths to calibrate')
        self._wl_layout = QVBoxLayout()
        self._wl_group.setLayout(self._wl_layout)

        btn_row = QHBoxLayout()
        btn_select_all = QPushButton('Select all')
        btn_deselect_all = QPushButton('Deselect all')
        btn_select_all.clicked.connect(self._select_all)
        btn_deselect_all.clicked.connect(self._deselect_all)
        btn_row.addWidget(btn_select_all)
        btn_row.addWidget(btn_deselect_all)
        btn_row.addStretch()
        self._wl_layout.addLayout(btn_row)

        layout.addWidget(self._wl_group)

        # Dry-run checkbox
        self._dry_run_cb = QCheckBox(
            'Dry run (calibrate without saving to database)'
        )
        layout.addWidget(self._dry_run_cb)

        # Back focal plane (BFP) powermeter checkbox
        self._bfp_pm_cb = QCheckBox('Use back focal plane (BFP) powermeter')
        self._bfp_pm_cb.setEnabled(True)
        self._bfp_pm_cb.setToolTip(
            'When checked, the beampath is moved to the BFP powermeter '
            'position during calibration. A correction factor is '
            'computed if both sample-plane and BFP calibrations exist '
            'for the same day.'
        )
        layout.addWidget(self._bfp_pm_cb)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setFormat('Waiting…')
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont('Courier', 9))
        self._log.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._log)

        # Start / Cancel buttons
        btn_row2 = QHBoxLayout()
        self._btn_start = QPushButton('Start')
        self._btn_cancel = QPushButton('Cancel')
        self._btn_cancel.setEnabled(False)
        self._btn_start.clicked.connect(self._on_start)
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row2.addWidget(self._btn_start)
        btn_row2.addWidget(self._btn_cancel)
        btn_row2.addStretch()
        layout.addLayout(btn_row2)

    def set_pc(self, pc):
        self._pc = pc
        self._rebuild_checkboxes()
        has_beampath = (
            pc is not None
            and hasattr(pc, 'instrument')
            and hasattr(pc.instrument, 'use_beampath')
            and pc.instrument.use_beampath
        )
        self._bfp_pm_cb.setEnabled(has_beampath)

    def _rebuild_checkboxes(self):
        # Remove old checkboxes (keep the button row at index 0)
        while self._wl_layout.count() > 1:
            item = self._wl_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        self._checkboxes.clear()

        if self._pc is None:
            return

        if hasattr(self._pc, 'protocol') and self._pc.protocol:
            for laser in self._pc.protocol['laser_sequence']:
                try:
                    enabled = self._pc.instrument.lasers[laser].enabled
                    state_str = 'ON' if enabled else 'off'
                except Exception:
                    state_str = '?'
                cb = QCheckBox(f'{laser} nm  [{state_str}]')
                cb.setChecked(True)
                self._checkboxes[laser] = cb
                self._wl_layout.addWidget(cb)
        else:
            # 1D microscope — single disabled placeholder
            try:
                wl = self._pc.instrument.config['index'].get(
                    'wavelength [nm]', '?'
                )
            except Exception:
                wl = '?'
            cb = QCheckBox(f'{wl} nm  (single-wavelength mode)')
            cb.setChecked(True)
            cb.setEnabled(False)
            self._wl_layout.addWidget(cb)

    def _select_all(self):
        for cb in self._checkboxes.values():
            cb.setChecked(True)

    def _deselect_all(self):
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def _selected_lasers(self):
        return [
            laser for laser, cb in self._checkboxes.items() if cb.isChecked()
        ]

    def _on_start(self):
        if self._pc is None:
            QMessageBox.warning(
                self, 'Not connected', 'Connect to a microscope first.'
            )
            return

        dry_run = self._dry_run_cb.isChecked()

        # 1D mode
        if not (hasattr(self._pc, 'protocol') and self._pc.protocol):
            if dry_run:
                reply = QMessageBox.question(
                    self,
                    'Dry run',
                    'Dry run enabled — calibration will NOT be saved. '
                    'Continue?',
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
            self._log.append('Starting 1D calibration…')
            self._emit_status('Running 1D calibration…')
            try:
                self._pc.calibrate(dry_run=dry_run)
                self._log.append('Done.')
                self._emit_status('Calibration complete.', 5000)
            except Exception as exc:
                QMessageBox.critical(self, 'Error', str(exc))
                self._emit_status('Calibration failed.', 5000)
            return

        selected = self._selected_lasers()
        if not selected:
            QMessageBox.warning(
                self, 'No wavelengths', 'Select at least one wavelength.'
            )
            return

        if dry_run:
            reply = QMessageBox.question(
                self,
                'Dry run',
                'Dry run enabled — calibration will NOT be saved. Continue?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._log.clear()
        self._log.append(f'Starting calibration for lasers: {selected}')
        self._progress.setValue(0)
        self._progress.setFormat('Starting…')
        self._btn_start.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._emit_status('Calibration running…')

        self.calibration_started.emit()

        powermeter_type = (
            POWERMETER_BFP
            if self._bfp_pm_cb.isChecked()
            else POWERMETER_SAMPLE
        )
        self._worker = CalibrationWorker(
            self._pc,
            laser_filter=selected,
            dry_run=dry_run,
            powermeter_type=powermeter_type,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._log.append)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_cancel(self):
        if self._worker:
            self._worker.request_cancel()
            self._btn_cancel.setEnabled(False)
            self._emit_status('Cancelling calibration…')

    def _on_progress(self, step, total, laser, lpwr):
        self._progress.setMaximum(total)
        self._progress.setValue(step)
        self._progress.setFormat(f'{laser} nm / {lpwr} mW  ({step}/{total})')
        self._emit_status(
            f'Calibrating: laser {laser} nm at {lpwr} mW  ({step}/{total})'
        )

    def _on_finished(self):
        self._log.append('Calibration complete.')
        self._progress.setFormat('Done')
        self._btn_start.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._worker = None
        self._emit_status('Calibration complete.', 5000)
        self.calibration_finished.emit()

    def _on_error(self, msg):
        self._log.append(f'ERROR: {msg}')
        QMessageBox.critical(self, 'Calibration error', msg)
        self._btn_start.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._worker = None
        self._emit_status(f'Calibration error: {msg}', 5000)
        self.calibration_finished.emit()

    def set_powermeter_available(self, available):
        """Enable or disable calibration controls per powermeter state."""
        self._btn_start.setEnabled(available)
        if not available:
            self._log.append(
                'WARNING: PowerMeter not available. Calibration is disabled.'
            )

    def cancel_worker_and_wait(self):
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
            self._worker.wait(5000)


# ---------------------------------------------------------------------------
# Tab 2 — Adjust
# ---------------------------------------------------------------------------


class AdjustTab(QWidget):
    """Tab for direct attenuator and laser power adjustment.

    No laser selector — operations act on whichever laser is currently
    active in the instrument (``instrument.curr_laser``).
    """

    status = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pc = None
        self._active_worker = None  # keep alive to prevent GC
        self._build_ui()

    def _emit_status(self, msg, timeout_ms=0):
        self.status.emit(msg, timeout_ms)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Refresh row
        refresh_row = QHBoxLayout()
        self._btn_refresh = QPushButton('Refresh')
        self._btn_refresh.setToolTip(
            'Re-read attenuator position and laser power from hardware'
        )
        self._btn_refresh.clicked.connect(self._on_refresh)
        refresh_row.addWidget(self._btn_refresh)
        refresh_row.addStretch()
        layout.addLayout(refresh_row)

        # Attenuator group
        att_group = QGroupBox('Attenuator')
        att_layout = QHBoxLayout()
        att_layout.addWidget(QLabel('Position:'))
        self._att_spin = QDoubleSpinBox()
        self._att_spin.setRange(-1e6, 1e6)
        self._att_spin.setDecimals(3)
        att_layout.addWidget(self._att_spin)
        self._btn_att_set = QPushButton('Set')
        self._btn_att_set.clicked.connect(self._on_att_set)
        self._btn_att_home = QPushButton('Home')
        self._btn_att_home.clicked.connect(self._on_att_home)
        att_layout.addWidget(self._btn_att_set)
        att_layout.addWidget(self._btn_att_home)
        att_layout.addStretch()
        att_group.setLayout(att_layout)
        layout.addWidget(att_group)

        # Laser power output group
        pwr_group = QGroupBox('Laser power output')
        pwr_layout = QHBoxLayout()
        pwr_layout.addWidget(QLabel('Power (mW):'))
        self._pwr_spin = QDoubleSpinBox()
        self._pwr_spin.setRange(0, 10000)
        self._pwr_spin.setDecimals(1)
        pwr_layout.addWidget(self._pwr_spin)
        self._btn_pwr_set = QPushButton('Set')
        self._btn_pwr_set.clicked.connect(self._on_pwr_set)
        pwr_layout.addWidget(self._btn_pwr_set)
        pwr_layout.addStretch()
        pwr_group.setLayout(pwr_layout)
        layout.addWidget(pwr_group)

        # Beampath controls
        bp_row = QHBoxLayout()
        self._btn_bp_open = QPushButton('Open beampath')
        self._btn_bp_close = QPushButton('Close beampath')
        self._btn_bp_open.clicked.connect(self._on_bp_open)
        self._btn_bp_close.clicked.connect(self._on_bp_close)
        bp_row.addWidget(self._btn_bp_open)
        bp_row.addWidget(self._btn_bp_close)
        self._autoshutter_cb = QCheckBox('Autoshutter')
        self._autoshutter_cb.stateChanged.connect(self._on_autoshutter)
        bp_row.addWidget(self._autoshutter_cb)
        bp_row.addStretch()
        layout.addLayout(bp_row)

        # Status
        self._status = QLabel('')
        layout.addWidget(self._status)
        layout.addStretch()

    def set_pc(self, pc):
        self._pc = pc
        if pc is None:
            return
        # Populate initial hardware values
        try:
            pos = pc.instrument.attenuator.curr_pos()
            if pos is not None:
                self._att_spin.setValue(float(pos))
        except Exception:
            pass
        try:
            laser = pc.instrument.curr_laser
            pwr = pc.instrument.lasers[laser].power
            if pwr is not None:
                self._pwr_spin.setValue(float(pwr))
        except Exception:
            pass
        # Overlay the last persisted settings for the current laser so the
        # user can restore them after a restart with a single Set. Populate
        # only — nothing is sent to the hardware here.
        try:
            saved = pc.instrument.saved_state(pc.instrument.curr_laser)
        except Exception:
            saved = None
        if saved:
            if saved.get('laser_power') is not None:
                self._pwr_spin.setValue(float(saved['laser_power']))
            if saved.get('attenuator') is not None:
                self._att_spin.setValue(float(saved['attenuator']))

    def _on_refresh(self):
        """Read attenuator position and laser power from hardware."""
        if self._pc is None:
            return

        def _do():
            result = {}
            try:
                pos = self._pc.instrument.attenuator.curr_pos()
                if pos is not None:
                    result['att_pos'] = float(pos)
            except Exception:
                pass
            try:
                laser = self._pc.instrument.curr_laser
                pwr = self._pc.instrument.lasers[laser].power
                if pwr is not None:
                    result['laser_pwr'] = float(pwr)
            except Exception:
                pass
            return result

        def _on_result(result):
            if 'att_pos' in result:
                self._att_spin.setValue(result['att_pos'])
            if 'laser_pwr' in result:
                self._pwr_spin.setValue(result['laser_pwr'])
            self._status.setText('Values refreshed.')
            self._emit_status('Ready', 2000)

        self._run_hw(_do, 'Refreshing device values…', on_result=_on_result)

    # --- helpers for async hardware ops ---

    def _hw_buttons(self, enabled):
        for btn in (
            self._btn_att_set,
            self._btn_att_home,
            self._btn_pwr_set,
            self._btn_bp_open,
            self._btn_bp_close,
            self._btn_refresh,
        ):
            btn.setEnabled(enabled)

    def _run_hw(self, func, status_msg, on_done=None, on_result=None):
        """Run a hardware callable in a GenericWorker, updating status bar."""
        self._hw_buttons(False)
        self._emit_status(status_msg)

        worker = GenericWorker(func)

        def _on_success(val):
            if on_result:
                on_result(val)
            if on_done:
                on_done()

        def _on_error(msg):
            self._status.setText(f'Error: {msg}')
            self._emit_status(f'Error: {msg}', 5000)
            QMessageBox.critical(self, 'Error', msg)

        def _on_finished():
            self._hw_buttons(True)
            self._active_worker = None

        worker.result.connect(_on_success)
        worker.error.connect(_on_error)
        worker.finished.connect(_on_finished)
        self._active_worker = worker
        worker.start()

    def _on_att_set(self):
        if self._pc is None:
            return
        pos = self._att_spin.value()

        def _do():
            self._pc.instrument.attenuator.set(pos)
            self._pc.instrument.record_state()

        def _done():
            self._status.setText(f'Attenuator set to {pos}.')
            self._emit_status('Ready', 2000)

        self._run_hw(_do, f'Setting attenuator to {pos}…', on_done=_done)

    def _on_att_home(self):
        if self._pc is None:
            return

        def _do():
            self._pc.instrument.attenuator.home()
            self._pc.instrument.record_state()

        def _done():
            self._status.setText('Attenuator homed.')
            self._emit_status('Ready', 2000)
            # Read back new position after homing
            try:
                pos = self._pc.instrument.attenuator.curr_pos()
                if pos is not None:
                    self._att_spin.setValue(float(pos))
            except Exception:
                pass

        self._run_hw(_do, 'Homing attenuator…', on_done=_done)

    def _on_pwr_set(self):
        if self._pc is None:
            return
        pwr = self._pwr_spin.value()

        def _do():
            self._pc.instrument.laserpower = pwr
            self._pc.instrument.record_state()

        def _done():
            self._status.setText(f'Laser power set to {pwr} mW.')
            self._emit_status('Ready', 2000)

        self._run_hw(_do, f'Setting laser power to {pwr} mW…', on_done=_done)

    def _on_bp_open(self):
        if self._pc is None:
            return
        try:
            laser = self._pc.instrument.curr_laser
        except Exception:
            laser = None
        protocol = getattr(self._pc, 'protocol', None) or {}
        bp_positions = (protocol.get('beampath') or {}).get(laser)
        if bp_positions is None:
            self._status.setText('No beampath config for current laser.')
            return

        def _do():
            self._pc.instrument.beampath.positions = bp_positions

        def _done():
            self._status.setText('Beampath opened.')
            self._emit_status('Ready', 2000)

        self._run_hw(_do, 'Opening beampath…', on_done=_done)

    def _on_bp_close(self):
        if self._pc is None:
            return
        protocol = getattr(self._pc, 'protocol', None) or {}
        end_pos = (protocol.get('beampath') or {}).get('end')
        if end_pos is None:
            self._status.setText('No beampath end position configured.')
            return

        def _do():
            self._pc.instrument.beampath.positions = end_pos

        def _done():
            self._status.setText('Beampath closed.')
            self._emit_status('Ready', 2000)

        self._run_hw(_do, 'Closing beampath…', on_done=_done)

    def _on_autoshutter(self, state):
        if self._pc is None:
            return
        try:
            self._pc.instrument.beampath.objects['shutter'].autoshutter = (
                Qt.CheckState(state) == Qt.CheckState.Checked
            )
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))


# ---------------------------------------------------------------------------
# Tab 3 — Set Power
# ---------------------------------------------------------------------------


class SetPowerTab(QWidget):
    """Tab for setting output power using calibration data."""

    status = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pc = None
        self._active_worker = None  # keep alive to prevent GC
        self._cancel_feedback = False
        self._laser_state: dict = {}  # {laser: (pwr_value, mode_data)}
        self._build_ui()

    def _emit_status(self, msg, timeout_ms=0):
        self.status.emit(msg, timeout_ms)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Laser selector + on/off
        laser_row = QHBoxLayout()
        laser_row.addWidget(QLabel('Laser:'))
        self._laser_combo = QComboBox()
        self._laser_combo.currentIndexChanged.connect(self._on_laser_changed)
        laser_row.addWidget(self._laser_combo)
        self._btn_onoff = QPushButton('switch ON')
        self._btn_onoff.setCheckable(True)
        self._btn_onoff.clicked.connect(self._on_toggle_laser)
        laser_row.addWidget(self._btn_onoff)
        laser_row.addStretch()
        layout.addLayout(laser_row)

        # Multi-laser checkbox
        self._multi_cb = QCheckBox(
            'Multi-laser mode (keep other lasers on when switching)'
        )
        self._multi_cb.setChecked(True)
        layout.addWidget(self._multi_cb)

        # ── Adjustment group box ──────────────────────────────────────────
        adj_group = QGroupBox('Power adjustment')
        adj_layout = QVBoxLayout()
        adj_group.setLayout(adj_layout)

        # Mode selector
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel('Mode:'))
        self._mode_combo = QComboBox()
        self._mode_combo.addItem(
            'Combined: laser power + attenuator', 'combined'
        )
        self._mode_combo.addItem(
            'Fixed laser power: adjust attenuator only', 'fixed_laser'
        )
        self._mode_combo.addItem(
            'Fixed attenuator: adjust laser power only', 'fixed_attenuator'
        )
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode_combo)
        mode_row.addStretch()
        adj_layout.addLayout(mode_row)

        # Feedback control
        feedback_row = QHBoxLayout()
        self._feedback_cb = QCheckBox('Feedback control')
        feedback_row.addWidget(self._feedback_cb)
        feedback_row.addWidget(QLabel('  Max deviation:'))
        self._feedback_tol_spin = QDoubleSpinBox()
        self._feedback_tol_spin.setRange(0.1, 50.0)
        self._feedback_tol_spin.setDecimals(1)
        self._feedback_tol_spin.setValue(1.0)
        self._feedback_tol_spin.setSuffix(' %')
        feedback_row.addWidget(self._feedback_tol_spin)
        self._btn_pi_params = QPushButton('▶ PI parameters')
        self._btn_pi_params.setFlat(True)
        self._btn_pi_params.clicked.connect(self._toggle_pi_params)
        feedback_row.addWidget(self._btn_pi_params)
        feedback_row.addStretch()
        adj_layout.addLayout(feedback_row)

        # PI parameters panel (hidden by default)
        self._pi_panel = QWidget()
        pi_layout = QHBoxLayout(self._pi_panel)
        pi_layout.setContentsMargins(16, 0, 0, 0)
        pi_layout.addWidget(QLabel('Kp:'))
        self._kp_spin = QDoubleSpinBox()
        self._kp_spin.setRange(0.01, 5.0)
        self._kp_spin.setDecimals(2)
        self._kp_spin.setSingleStep(0.05)
        self._kp_spin.setValue(0.85)
        self._kp_spin.setToolTip(
            'Proportional gain — fraction of current error applied per step.\n'
            'Lower values converge more slowly but without overshoot.'
        )
        pi_layout.addWidget(self._kp_spin)
        pi_layout.addWidget(QLabel('  Ki:'))
        self._ki_spin = QDoubleSpinBox()
        self._ki_spin.setRange(0.0, 2.0)
        self._ki_spin.setDecimals(3)
        self._ki_spin.setSingleStep(0.01)
        self._ki_spin.setValue(0.15)
        self._ki_spin.setToolTip(
            'Integral gain — accumulates past error to eliminate '
            'steady-state offset.\n'
            'Set to 0 to disable integral action.'
        )
        pi_layout.addWidget(self._ki_spin)
        pi_layout.addStretch()
        self._pi_panel.setVisible(False)
        adj_layout.addWidget(self._pi_panel)

        # Power range label
        self._range_label = QLabel('Range: N/A')
        adj_layout.addWidget(self._range_label)

        # Target power + Set button
        pwr_row = QHBoxLayout()
        pwr_row.addWidget(QLabel('Target power (mW):'))
        self._pwr_spin = QDoubleSpinBox()
        self._pwr_spin.setRange(0, 10000)
        self._pwr_spin.setDecimals(2)
        pwr_row.addWidget(self._pwr_spin)
        self._btn_set = QPushButton('Set')
        self._btn_set.clicked.connect(self._on_set_power)
        pwr_row.addWidget(self._btn_set)
        self._btn_cancel_feedback = QPushButton('Cancel')
        self._btn_cancel_feedback.clicked.connect(self._on_cancel_feedback)
        self._btn_cancel_feedback.setVisible(False)
        pwr_row.addWidget(self._btn_cancel_feedback)
        pwr_row.addStretch()
        adj_layout.addLayout(pwr_row)

        layout.addWidget(adj_group)
        # ─────────────────────────────────────────────────────────────────

        # Beampath controls
        bp_row = QHBoxLayout()
        self._btn_bp_open = QPushButton('Open beampath')
        self._btn_bp_close = QPushButton('Close beampath')
        self._btn_bp_open.clicked.connect(self._on_bp_open)
        self._btn_bp_close.clicked.connect(self._on_bp_close)
        bp_row.addWidget(self._btn_bp_open)
        bp_row.addWidget(self._btn_bp_close)
        bp_row.addStretch()
        layout.addLayout(bp_row)

        # Measure + All off
        misc_row = QHBoxLayout()
        self._btn_measure = QPushButton('Measure')
        self._btn_measure.clicked.connect(self._on_measure)
        self._btn_alloff = QPushButton('All lasers OFF')
        self._btn_alloff.clicked.connect(self._on_all_off)
        misc_row.addWidget(self._btn_measure)
        misc_row.addWidget(self._btn_alloff)
        misc_row.addStretch()
        layout.addLayout(misc_row)

        self._status = QLabel('')
        layout.addWidget(self._status)

        # ── Separator ────────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # ── Hardware state section (formerly Adjust tab) ──────────────────
        hw_refresh_row = QHBoxLayout()
        self._btn_hw_refresh = QPushButton('Refresh hardware state')
        self._btn_hw_refresh.setToolTip(
            'Re-read attenuator position and laser power from hardware'
        )
        self._btn_hw_refresh.clicked.connect(self._on_hw_refresh)
        hw_refresh_row.addWidget(self._btn_hw_refresh)
        hw_refresh_row.addStretch()
        layout.addLayout(hw_refresh_row)

        # Attenuator direct control
        hw_att_group = QGroupBox('Attenuator (direct)')
        hw_att_layout = QHBoxLayout()
        hw_att_layout.addWidget(QLabel('Position:'))
        self._hw_att_spin = QDoubleSpinBox()
        self._hw_att_spin.setRange(-1e6, 1e6)
        self._hw_att_spin.setDecimals(3)
        hw_att_layout.addWidget(self._hw_att_spin)
        self._btn_hw_att_set = QPushButton('Set')
        self._btn_hw_att_set.clicked.connect(self._on_hw_att_set)
        self._btn_hw_att_home = QPushButton('Home')
        self._btn_hw_att_home.clicked.connect(self._on_hw_att_home)
        hw_att_layout.addWidget(self._btn_hw_att_set)
        hw_att_layout.addWidget(self._btn_hw_att_home)
        hw_att_layout.addStretch()
        hw_att_group.setLayout(hw_att_layout)
        layout.addWidget(hw_att_group)

        # Laser power direct control
        hw_pwr_group = QGroupBox('Laser power (direct)')
        hw_pwr_layout = QHBoxLayout()
        hw_pwr_layout.addWidget(QLabel('Power (mW):'))
        self._hw_pwr_spin = QDoubleSpinBox()
        self._hw_pwr_spin.setRange(0, 10000)
        self._hw_pwr_spin.setDecimals(1)
        hw_pwr_layout.addWidget(self._hw_pwr_spin)
        self._btn_hw_pwr_set = QPushButton('Set')
        self._btn_hw_pwr_set.clicked.connect(self._on_hw_pwr_set)
        hw_pwr_layout.addWidget(self._btn_hw_pwr_set)
        hw_pwr_layout.addStretch()
        hw_pwr_group.setLayout(hw_pwr_layout)
        layout.addWidget(hw_pwr_group)

        # Autoshutter
        self._autoshutter_cb = QCheckBox('Autoshutter')
        self._autoshutter_cb.stateChanged.connect(self._on_autoshutter)
        layout.addWidget(self._autoshutter_cb)

        layout.addStretch()

    def set_pc(self, pc):
        self._pc = pc
        self._laser_combo.blockSignals(True)
        self._laser_combo.clear()
        if pc is not None:
            try:
                pc.instrument.load_calibration_database()
            except Exception:
                pass
            for laser in pc.instrument.lasers:
                self._laser_combo.addItem(str(laser), laser)
        self._laser_combo.blockSignals(False)
        if self._laser_combo.count():
            self._on_laser_changed(0)
        # Populate hardware spinboxes
        if pc is not None:
            try:
                pos = pc.instrument.attenuator.curr_pos()
                if pos is not None:
                    self._hw_att_spin.setValue(float(pos))
            except Exception:
                pass
            try:
                laser = pc.instrument.curr_laser
                pwr = pc.instrument.lasers[laser].power
                if pwr is not None:
                    self._hw_pwr_spin.setValue(float(pwr))
            except Exception:
                pass
            # Overlay the last persisted settings for the current laser so
            # the user can restore them after a restart with a single Set.
            self._apply_saved_state_to_ui(pc.instrument.curr_laser)

    def _apply_saved_state_to_ui(self, laser):
        """Populate the direct hardware spin boxes from persisted settings.

        Loads the laser power set-point and attenuator position last saved for
        ``laser`` (see :mod:`monet.hwstate`) into the direct-control spin boxes.
        This only updates the UI — nothing is sent to the hardware until the
        user clicks Set. Does nothing if no settings were stored.
        """
        if self._pc is None:
            return
        try:
            saved = self._pc.instrument.saved_state(laser)
        except Exception:
            saved = None
        if not saved:
            return
        if saved.get('laser_power') is not None:
            self._hw_pwr_spin.setValue(float(saved['laser_power']))
        if saved.get('attenuator') is not None:
            self._hw_att_spin.setValue(float(saved['attenuator']))

    def _on_laser_changed(self, idx):
        if self._pc is None:
            return
        # Save current laser's UI state before switching
        prev_laser = getattr(self, '_current_laser', None)
        if prev_laser is not None:
            self._laser_state[prev_laser] = (
                self._pwr_spin.value(),
                self._mode_combo.currentData(),
            )

        laser = self._laser_combo.currentData()
        if laser is None:
            return
        self._current_laser = laser

        # Restore saved state for the newly selected laser
        if laser in self._laser_state:
            saved_pwr, saved_mode = self._laser_state[laser]
            self._pwr_spin.setValue(saved_pwr)
            mode_idx = self._mode_combo.findData(saved_mode)
            if mode_idx >= 0:
                self._mode_combo.setCurrentIndex(mode_idx)

        try:
            enabled = self._pc.instrument.lasers[laser].enabled
            self._btn_onoff.setChecked(enabled)
            self._btn_onoff.setText('switch OFF' if enabled else 'switch ON')
        except Exception as exc:
            self._status.setText(str(exc))
        # Show the persisted hardware settings for the newly selected laser
        # line (populate only — not applied to hardware).
        self._apply_saved_state_to_ui(laser)
        self._update_range_label()

    # --- helpers ---

    def _beampath_matches(self, target_positions):
        """Return True if every key in target_positions already matches the
        current beampath position, so the move can be skipped."""
        try:
            if not getattr(self._pc.instrument, 'use_beampath', False):
                return False
            current = self._pc.instrument.beampath.positions
            return all(
                current.get(k) == v for k, v in target_positions.items()
            )
        except Exception:
            return False

    def _on_cancel_feedback(self):
        self._cancel_feedback = True
        self._btn_cancel_feedback.setEnabled(False)

    def _action_buttons(self, enabled):
        for btn in (
            self._btn_onoff,
            self._btn_set,
            self._btn_bp_open,
            self._btn_bp_close,
            self._btn_measure,
            self._btn_alloff,
            self._btn_hw_refresh,
            self._btn_hw_att_set,
            self._btn_hw_att_home,
            self._btn_hw_pwr_set,
        ):
            btn.setEnabled(enabled)

    def _run_hw(self, func, status_msg, on_done=None, on_result=None):
        """Run a hardware callable in a GenericWorker."""
        self._action_buttons(False)
        self._emit_status(status_msg)

        worker = GenericWorker(func)

        def _on_success(val):
            if on_result:
                on_result(val)
            if on_done:
                on_done()

        def _on_error(msg):
            self._status.setText(f'Error: {msg}')
            self._emit_status(f'Error: {msg}', 5000)
            QMessageBox.critical(self, 'Error', msg)

        def _on_finished():
            self._action_buttons(True)
            self._active_worker = None

        worker.result.connect(_on_success)
        worker.error.connect(_on_error)
        worker.finished.connect(_on_finished)
        self._active_worker = worker
        worker.start()

    def _on_toggle_laser(self, checked):
        if self._pc is None:
            return
        laser = self._laser_combo.currentData()

        def _do():
            if not self._multi_cb.isChecked():
                for lsr in self._pc.instrument.lasers:
                    if lsr != laser:
                        self._pc.instrument.lasers[lsr].enabled = False
            self._pc.instrument.laser = laser
            self._pc.instrument.laser_enabled = checked

        def _done():
            self._btn_onoff.setText('switch OFF' if checked else 'switch ON')
            self._status.setText(
                f'Laser {laser} nm {"on" if checked else "off"}.'
            )
            self._emit_status('Ready', 2000)

        self._run_hw(
            _do,
            f'{"Enabling" if checked else "Disabling"} laser {laser} nm…',
            on_done=_done,
        )

    def _on_set_power(self):
        if self._pc is None:
            return
        pwr = self._pwr_spin.value()
        laser = self._laser_combo.currentData()
        mode = self._mode_combo.currentData()

        if mode in ('fixed_attenuator', 'fixed_laser') and not hasattr(
            self._pc.instrument, 'set_power_fixed_attenuator'
        ):
            QMessageBox.warning(
                self,
                'Not supported',
                'This mode requires a multi-laser instrument '
                'with calibrations at multiple laser power levels.',
            )
            return

        # --- Resolve feedback / beampath settings on the main thread ---
        use_feedback = self._feedback_cb.isChecked() and getattr(
            self._pc, 'powermeter_available', False
        )
        protocol = getattr(self._pc, 'protocol', None) or {}
        bp_dict = protocol.get('beampath') or {}
        bp_for_laser = bp_dict.get(laser)  # (1) open for wavelength
        bp_start_cal = bp_dict.get('start_calibrate')
        bp_end_calibrate = bp_dict.get('end_calibrate')
        bp_end = bp_dict.get('end')

        do_start_cal = False
        if use_feedback and bp_start_cal is not None:
            # (2) skip question if beampath is already at start_calibrate
            if self._beampath_matches(bp_start_cal):
                do_start_cal = True
            else:
                reply = QMessageBox.question(
                    self,
                    'Feedback — beampath',
                    'Move beampath to "start_calibrate" position before '
                    'feedback measurement?\n\n'
                    'Click Cancel to perform the initial set without '
                    'feedback.',
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                )
                if reply == QMessageBox.StandardButton.Cancel:
                    use_feedback = False
                elif reply == QMessageBox.StandardButton.Yes:
                    do_start_cal = True

        max_dev_pct = self._feedback_tol_spin.value()
        MAX_ITER = 20

        # --- Build the background callable ---
        if not use_feedback:
            if mode == 'fixed_attenuator':

                def _do():
                    self._pc.instrument.set_power_fixed_attenuator(pwr, laser)
                    self._pc.instrument.record_state(laser)

                done_msg = (
                    f'Laser power adjusted for {pwr} mW output '
                    f'(laser {laser} nm, attenuator fixed).'
                )
                status_msg = f'Adjusting laser power for {pwr} mW…'
            elif mode == 'fixed_laser':

                def _do():
                    self._pc.instrument.set_power_fixed_laser(pwr, laser)
                    self._pc.instrument.record_state(laser)

                done_msg = (
                    f'Attenuator set for {pwr} mW '
                    f'(laser {laser} nm, laser power fixed).'
                )
                status_msg = f'Setting attenuator for {pwr} mW…'
            else:

                def _do():
                    self._pc.instrument.laser = laser
                    self._pc.instrument.power = pwr
                    self._pc.instrument.record_state(laser)

                done_msg = f'Power set to {pwr} mW for laser {laser} nm.'
                status_msg = f'Setting power to {pwr} mW…'

            def _done():
                self._status.setText(done_msg)
                self._emit_status('Ready', 2000)
                self._refresh_hw_state(laser)
                self._update_range_label()

            self._run_hw(_do, status_msg, on_done=_done)

        else:
            # Capture PI gains on the main thread before the worker starts.
            kp_att = self._kp_spin.value()
            ki_att = self._ki_spin.value()

            # Mutable relay so _do() can call worker.progress.emit once the
            # worker object exists (set just before worker.start() below).
            progress_relay = [None]

            def _do():
                def _emit(iteration, setpoint, meas):
                    if progress_relay[0] is not None:
                        progress_relay[0]((iteration, setpoint, meas))

                # Open beampath for the selected wavelength, then move to the
                # measurement position (start_calibrate). run_power_feedback
                # performs the initial power set and settles before measuring.
                if bp_for_laser is not None:
                    self._pc.instrument.beampath.positions = bp_for_laser
                if do_start_cal and bp_start_cal is not None:
                    self._pc.instrument.beampath.positions = bp_start_cal

                # Closed-loop power setting (initial set, settle, PI feedback).
                result = run_power_feedback(
                    self._pc.instrument,
                    self._pc.powermeter,
                    pwr,
                    laser,
                    mode,
                    kp=kp_att,
                    ki=ki_att,
                    max_dev_pct=max_dev_pct,
                    max_iter=MAX_ITER,
                    progress_callback=_emit,
                    cancel_check=lambda: self._cancel_feedback,
                )

                # Restore beampath, mirroring the calibration routine
                if bp_end_calibrate is not None:
                    self._pc.instrument.beampath.positions = bp_end_calibrate
                if bp_end is not None:
                    self._pc.instrument.beampath.positions = bp_end

                self._pc.instrument.record_state(laser)

                return (
                    result['measured'],
                    result['converged'],
                    result['cali_pred'],
                    result['out_of_range'],
                    result['att_pos'],
                    result['laser_pwr'],
                )

            def _on_result(res):
                (
                    measured,
                    converged,
                    cali_pred,
                    out_of_range_warned,
                    att_pos,
                    laser_pwr,
                ) = res
                dev_pct = abs(measured - pwr) / pwr * 100.0 if pwr > 0 else 0.0
                parts = [
                    f'Target {pwr} mW → measured {measured:.3f} mW '
                    f'({dev_pct:.1f}% deviation)'
                ]
                if not converged:
                    parts.append(f'(did not converge within {MAX_ITER} steps)')
                if out_of_range_warned:
                    parts.append('(attenuator range limit reached)')
                self._status.setText('  '.join(parts))
                try:
                    unit = self._pc.powermeter.unit
                except Exception:
                    unit = 'mW'
                mm_err = self._update_mm_comment(
                    laser, measured, unit, att_pos, laser_pwr
                )
                if mm_err is not None:
                    self._status.setText(
                        '  '.join(parts) + f' — MM comment error: {mm_err}'
                    )
                if cali_pred is not None and cali_pred > 0:
                    cali_dev_pct = (measured - cali_pred) / cali_pred * 100.0
                    self._emit_status(
                        f'Calibration deviation: {cali_dev_pct:+.1f}%'
                        f'  (calibration predicts {cali_pred:.3f} mW,'
                        f' measured {measured:.3f} mW)'
                    )
                else:
                    self._emit_status('Ready', 2000)
                self._refresh_hw_state(laser)
                self._update_range_label()

            # Open the live-plot dialog and wire everything up manually
            # (cannot use _run_hw because we need access to the worker object
            # to hook up the progress signal before the thread starts).
            plot_dialog = FeedbackPlotDialog(pwr, max_dev_pct, mode, self)
            plot_dialog.show()
            self._feedback_dialog = plot_dialog  # keep alive (prevent GC)

            self._action_buttons(False)
            self._cancel_feedback = False
            self._btn_cancel_feedback.setVisible(True)
            self._btn_cancel_feedback.setEnabled(True)
            self._emit_status(f'Setting {pwr} mW with feedback…')

            worker = GenericWorker(_do)
            progress_relay[0] = worker.progress.emit
            worker.progress.connect(plot_dialog.add_point)

            def _on_success(val):
                _on_result(val)

            def _on_error(msg):
                self._status.setText(f'Error: {msg}')
                self._emit_status(f'Error: {msg}', 5000)
                QMessageBox.critical(self, 'Error', msg)

            def _on_finished():
                self._action_buttons(True)
                self._btn_cancel_feedback.setVisible(False)
                self._active_worker = None

            worker.result.connect(_on_success)
            worker.error.connect(_on_error)
            worker.finished.connect(_on_finished)
            self._active_worker = worker
            worker.start()

    def _on_bp_open(self):
        if self._pc is None:
            return
        laser = self._laser_combo.currentData()
        protocol = getattr(self._pc, 'protocol', None) or {}
        bp_positions = (protocol.get('beampath') or {}).get(laser)
        if bp_positions is None:
            self._status.setText('No beampath config for this laser.')
            return

        def _do():
            self._pc.instrument.beampath.positions = bp_positions

        def _done():
            self._status.setText('Beampath opened.')
            self._emit_status('Ready', 2000)

        self._run_hw(_do, 'Opening beampath…', on_done=_done)

    def _on_bp_close(self):
        if self._pc is None:
            return
        protocol = getattr(self._pc, 'protocol', None) or {}
        end_pos = (protocol.get('beampath') or {}).get('end')
        if end_pos is None:
            self._status.setText('No beampath end position configured.')
            return

        def _do():
            self._pc.instrument.beampath.positions = end_pos

        def _done():
            self._status.setText('Beampath closed.')
            self._emit_status('Ready', 2000)

        self._run_hw(_do, 'Closing beampath…', on_done=_done)

    def _update_mm_comment(
        self, laser, measured, unit, att_pos=None, laser_pwr=None
    ):
        """Write measured power into the MicroManager acquisition comment.

        Thin wrapper around util.update_mm_acquisition_comment.
        """
        return update_mm_acquisition_comment(
            laser, measured, unit, att_pos, laser_pwr
        )

    def _on_measure(self):
        if self._pc is None:
            return
        laser = self._laser_combo.currentData()
        protocol = getattr(self._pc, 'protocol', None) or {}
        bp_dict = protocol.get('beampath') or {}

        bp_for_laser = bp_dict.get(laser)
        bp_start_cal = bp_dict.get('start_calibrate')

        # Ask about start_calibrate only when beampath is not already there
        do_start_cal = False
        if bp_start_cal is not None:
            if self._beampath_matches(bp_start_cal):
                do_start_cal = True  # already in position, no need to ask
            else:
                reply = QMessageBox.question(
                    self,
                    'Measurement beampath',
                    'Set beampath to "start_calibrate" position before '
                    'measuring?',
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                )
                do_start_cal = reply == QMessageBox.StandardButton.Yes

        mode = self._mode_combo.currentData()

        def _do():
            import time

            moved = False
            # Open beampath for this laser
            if bp_for_laser is not None:
                try:
                    self._pc.instrument.beampath.positions = bp_for_laser
                    moved = True
                except Exception:
                    pass
            # Optionally move to start_calibrate position
            if do_start_cal and bp_start_cal is not None:
                try:
                    self._pc.instrument.beampath.positions = bp_start_cal
                    moved = True
                except Exception:
                    pass
            # Wait for beampath hardware to settle (no polling API available)
            if moved:
                time.sleep(2)
            # Project the raw reading to the sample plane (no-op unless the
            # active calibration used the BFP meter).
            measured = self._pc.instrument.to_sample_plane(
                self._pc.powermeter.read(), laser
            )
            try:
                if mode == 'fixed_attenuator' and hasattr(
                    self._pc.instrument, 'predict_power_fixed_attenuator'
                ):
                    curr_lp = self._pc.instrument.lasers[laser].power
                    cali_pred = (
                        self._pc.instrument.predict_power_fixed_attenuator(
                            curr_lp, laser
                        )
                    )
                else:
                    cali_pred = self._pc.instrument.power
            except Exception:
                cali_pred = None
            try:
                att_pos = self._pc.instrument.attenuator.curr_pos()
            except Exception:
                att_pos = None
            try:
                laser_pwr = self._pc.instrument.lasers[laser].power
            except Exception:
                laser_pwr = None
            return measured, cali_pred, att_pos, laser_pwr

        def _on_val(res):
            measured, cali_pred, att_pos, laser_pwr = res
            try:
                unit = self._pc.powermeter.unit
            except Exception:
                unit = 'a.u.'
            self._status.setText(f'Measured power: {measured:.3f} {unit}')

            mm_err = self._update_mm_comment(
                laser, measured, unit, att_pos, laser_pwr
            )

            if cali_pred is not None and cali_pred > 0:
                cali_dev_pct = (measured - cali_pred) / cali_pred * 100.0
                self._emit_status(
                    f'Calibration deviation: {cali_dev_pct:+.1f}%'
                    f'  (calibration predicts {cali_pred:.3f} {unit},'
                    f' measured {measured:.3f} {unit})'
                )
            else:
                self._emit_status('Ready', 2000)

            if mm_err is not None:
                self._status.setText(
                    f'Measured: {measured:.3f} {unit}'
                    f' — MM comment error: {mm_err}'
                )

        self._run_hw(_do, 'Measuring power…', on_result=_on_val)

    def _toggle_pi_params(self):
        visible = not self._pi_panel.isVisible()
        self._pi_panel.setVisible(visible)
        self._btn_pi_params.setText(
            '▼ PI parameters' if visible else '▶ PI parameters'
        )

    def _on_mode_changed(self, _idx):
        """Enable feedback only for single-axis modes (not combined)."""
        self._update_feedback_enabled()
        self._update_range_label()

    def _update_feedback_enabled(self):
        mode = self._mode_combo.currentData()
        powermeter_ok = self._btn_measure.isEnabled()
        feedback_ok = powermeter_ok and mode != 'combined'
        self._feedback_cb.setEnabled(feedback_ok)
        self._feedback_tol_spin.setEnabled(feedback_ok)
        if not feedback_ok:
            self._feedback_cb.setChecked(False)

    def set_powermeter_available(self, available):
        """Enable or disable powermeter-dependent controls."""
        self._btn_measure.setEnabled(available)
        self._update_feedback_enabled()

    def _on_all_off(self):
        if self._pc is None:
            return

        def _do():
            for laser in self._pc.instrument.lasers:
                self._pc.instrument.lasers[laser].enabled = False

        def _done():
            self._status.setText('All lasers switched off.')
            self._btn_onoff.setChecked(False)
            self._btn_onoff.setText('switch ON')
            self._emit_status('Ready', 2000)

        self._run_hw(_do, 'Switching all lasers off…', on_done=_done)

    # ── Hardware state helpers ───────────────────────────────────────────────

    def _refresh_hw_state(self, laser=None):
        """Read back attenuator position and laser power into spinboxes.

        Called on the main thread immediately after a set-power action
        finishes.
        """
        if self._pc is None:
            return
        if laser is None:
            laser = self._laser_combo.currentData()
        try:
            pos = self._pc.instrument.attenuator.curr_pos()
            if pos is not None:
                self._hw_att_spin.setValue(float(pos))
        except Exception:
            pass
        try:
            if laser and hasattr(self._pc.instrument, 'lasers'):
                pwr = self._pc.instrument.lasers[laser].power
                if pwr is not None:
                    self._hw_pwr_spin.setValue(float(pwr))
        except Exception:
            pass

    def _update_range_label(self):
        """Compute and display the accessible power range for the mode."""
        if self._pc is None or not getattr(
            self._pc.instrument, 'is_calibrated', False
        ):
            self._range_label.setText('Range: N/A (not calibrated)')
            return
        laser = self._laser_combo.currentData()
        mode = self._mode_combo.currentData()
        try:
            lo, hi = self._pc.instrument.accessible_power_range(mode, laser)
            self._range_label.setText(f'Range: {lo:.2f} – {hi:.2f} mW')
        except Exception:
            self._range_label.setText('Range: N/A')

    def _on_hw_refresh(self):
        """Read attenuator position and laser power from hardware."""
        if self._pc is None:
            return

        def _do():
            result = {}
            try:
                pos = self._pc.instrument.attenuator.curr_pos()
                if pos is not None:
                    result['att_pos'] = float(pos)
            except Exception:
                pass
            try:
                laser = self._pc.instrument.curr_laser
                pwr = self._pc.instrument.lasers[laser].power
                if pwr is not None:
                    result['laser_pwr'] = float(pwr)
            except Exception:
                pass
            return result

        def _on_result(result):
            if 'att_pos' in result:
                self._hw_att_spin.setValue(result['att_pos'])
            if 'laser_pwr' in result:
                self._hw_pwr_spin.setValue(result['laser_pwr'])
            self._status.setText('Hardware state refreshed.')
            self._emit_status('Ready', 2000)

        self._run_hw(_do, 'Refreshing hardware state…', on_result=_on_result)

    def _on_hw_att_set(self):
        if self._pc is None:
            return
        pos = self._hw_att_spin.value()

        def _do():
            self._pc.instrument.attenuator.set(pos)
            self._pc.instrument.record_state()

        def _done():
            self._status.setText(f'Attenuator set to {pos:.3f}.')
            self._emit_status('Ready', 2000)

        self._run_hw(_do, f'Setting attenuator to {pos:.3f}…', on_done=_done)

    def _on_hw_att_home(self):
        if self._pc is None:
            return

        def _do():
            self._pc.instrument.attenuator.home()
            self._pc.instrument.record_state()

        def _done():
            self._status.setText('Attenuator homed.')
            self._emit_status('Ready', 2000)
            try:
                pos = self._pc.instrument.attenuator.curr_pos()
                if pos is not None:
                    self._hw_att_spin.setValue(float(pos))
            except Exception:
                pass

        self._run_hw(_do, 'Homing attenuator…', on_done=_done)

    def _on_hw_pwr_set(self):
        if self._pc is None:
            return
        pwr = self._hw_pwr_spin.value()

        def _do():
            self._pc.instrument.laserpower = pwr
            self._pc.instrument.record_state()

        def _done():
            self._status.setText(f'Laser power set to {pwr} mW.')
            self._emit_status('Ready', 2000)

        self._run_hw(_do, f'Setting laser power to {pwr} mW…', on_done=_done)

    def _on_autoshutter(self, state):
        if self._pc is None:
            return
        try:
            self._pc.instrument.beampath.objects['shutter'].autoshutter = (
                Qt.CheckState(state) == Qt.CheckState.Checked
            )
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))


# ---------------------------------------------------------------------------
# Tab 4 — Database
# ---------------------------------------------------------------------------


class DatabaseTab(QWidget):
    """Tab for viewing and managing calibration records."""

    status = pyqtSignal(str, int)

    COLUMNS = [
        'Microscope',
        'Wavelength (nm)',
        'Power (mW)',
        'Date',
        'Time',
        'Model',
        'Parameters',
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pc = None
        self._db_fname = None
        self._active_worker = None
        self._build_ui()

    def _emit_status(self, msg, timeout_ms=0):
        self.status.emit(msg, timeout_ms)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Top controls
        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel('Microscope filter:'))
        self._scope_combo = QComboBox()
        self._scope_combo.addItem('(all)', None)
        ctrl_row.addWidget(self._scope_combo)
        btn_refresh = QPushButton('Refresh')
        btn_refresh.clicked.connect(self._on_refresh)
        ctrl_row.addWidget(btn_refresh)
        ctrl_row.addStretch()
        # btn_delete = QPushButton('Delete selected')
        # btn_delete.clicked.connect(self._on_delete)
        # ctrl_row.addWidget(btn_delete)
        btn_restart = QPushButton('Restart DB')
        btn_restart.clicked.connect(self._on_restart)
        ctrl_row.addWidget(btn_restart)
        layout.addLayout(ctrl_row)

        # Database link (shown when database is an HTTP server URL)
        self._db_link_label = QLabel('')
        self._db_link_label.setOpenExternalLinks(True)
        self._db_link_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._db_link_label)

        # Table
        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)

        # Status
        self._status = QLabel('')
        layout.addWidget(self._status)

        # Correction factors section
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)
        factors_hdr = QHBoxLayout()
        factors_hdr.addWidget(QLabel('Objective Transmission (sample / BFP)'))
        factors_hdr.addStretch()
        self._btn_compute_transmission = QPushButton('Compute transmission')
        self._btn_compute_transmission.setEnabled(False)
        self._btn_compute_transmission.clicked.connect(
            self._on_compute_transmission
        )
        factors_hdr.addWidget(self._btn_compute_transmission)
        layout.addLayout(factors_hdr)
        self._factors_table = QTableWidget(0, 5)
        self._factors_table.setHorizontalHeaderLabels(
            [
                'Microscope',
                'Wavelength (nm)',
                'Date',
                'transmission_objective',
                'Std Dev',
            ]
        )
        self._factors_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._factors_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._factors_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._factors_table.horizontalHeader().setStretchLastSection(True)
        self._factors_table.setMaximumHeight(150)
        layout.addWidget(self._factors_table)

    def set_pc(self, pc):
        self._pc = pc
        if pc is not None:
            self._db_fname = pc.instrument.config.get('database')
        else:
            self._db_fname = None
        self._btn_compute_transmission.setEnabled(pc is not None)
        self._update_db_link()
        self._on_refresh()

    def _update_db_link(self):
        db = self._db_fname or ''
        if db.startswith('http://') or db.startswith('https://'):
            docs_url = db.rstrip('/') + '/dashboard'
            self._db_link_label.setText(
                f'Server: <a href="{docs_url}">{docs_url}</a>'
            )
        else:
            self._db_link_label.setText(f'File: {db}' if db else '')

    def _on_refresh(self):
        if self._db_fname is None:
            self._status.setText('No database configured.')
            return
        try:
            scope_filter = self._scope_combo.currentData()
            index = {}
            if scope_filter:
                index['name'] = scope_filter
            records_df = io.load_database(
                self._db_fname, index, time_idx='all'
            )
        except Exception as exc:
            self._status.setText(f'Error loading database: {exc}')
            self._table.setRowCount(0)
            return

        self._table.setRowCount(0)
        if hasattr(records_df, 'iterrows'):
            # DataFrame with MultiIndex
            known_scopes = set()
            for idx, row in records_df.iterrows():
                if not isinstance(idx, tuple):
                    idx = (idx,)
                row_pos = self._table.rowCount()
                self._table.insertRow(row_pos)
                # idx = (name, wavelength, power, date, time)
                scope = idx[0] if len(idx) > 0 else ''
                wl = idx[1] if len(idx) > 1 else ''
                pwr = idx[2] if len(idx) > 2 else ''
                date = idx[3] if len(idx) > 3 else ''
                time_val = idx[4] if len(idx) > 4 else ''
                known_scopes.add(str(scope))

                params = {col: row[col] for col in row.index}
                params_str = json.dumps(
                    {
                        k: round(v, 4) if isinstance(v, float) else v
                        for k, v in params.items()
                    }
                )
                params_short = params_str[:40] + (
                    '…' if len(params_str) > 40 else ''
                )

                values = [
                    str(scope),
                    str(wl),
                    str(pwr),
                    str(date),
                    str(time_val),
                    '',
                    params_short,
                ]
                for col, val in enumerate(values):
                    item = QTableWidgetItem(val)
                    if col == len(values) - 1:
                        item.setToolTip(params_str)
                    self._table.setItem(row_pos, col, item)

            # Repopulate scope combo without clearing "all"
            current_scopes = {
                self._scope_combo.itemText(i)
                for i in range(1, self._scope_combo.count())
            }
            for sc in sorted(known_scopes - current_scopes):
                self._scope_combo.addItem(sc, sc)

        total = self._table.rowCount()
        self._status.setText(f'{total} record(s)')

        # Refresh factors table
        self._factors_table.setRowCount(0)
        if self._db_fname is not None:
            try:
                scope_filter = self._scope_combo.currentData()
                factors_df = io.load_factors(
                    self._db_fname,
                    device=scope_filter if scope_filter else None,
                )
                if hasattr(factors_df, 'iterrows') and not factors_df.empty:
                    for idx, row in factors_df.iterrows():
                        if not isinstance(idx, tuple):
                            idx = (idx,)
                        r = self._factors_table.rowCount()
                        self._factors_table.insertRow(r)
                        device_val = idx[0] if len(idx) > 0 else ''
                        wl_val = idx[1] if len(idx) > 1 else ''
                        date_val = idx[2] if len(idx) > 2 else ''
                        factor_val = row.get('transmission_objective_mean', '')
                        std_val = row.get('transmission_objective_std', '')
                        vals = [
                            str(device_val),
                            str(wl_val),
                            str(date_val),
                            (
                                f'{factor_val:.4f}'
                                if isinstance(factor_val, float)
                                else str(factor_val)
                            ),
                            (
                                f'{std_val:.4f}'
                                if isinstance(std_val, float)
                                else str(std_val)
                            ),
                        ]
                        for c, v in enumerate(vals):
                            self._factors_table.setItem(
                                r, c, QTableWidgetItem(v)
                            )
            except Exception:
                pass

    def _selected_row_indices(self):
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        return rows

    def _row_to_index(self, row):
        return {
            'name': self._table.item(row, 0).text(),
            'wavelength [nm]': self._table.item(row, 1).text(),
            'laser_power [mW]': self._table.item(row, 2).text(),
            'date': self._table.item(row, 3).text(),
            'time': self._table.item(row, 4).text(),
        }

    def _on_delete(self):
        rows = self._selected_row_indices()
        if not rows:
            QMessageBox.information(
                self, 'Nothing selected', 'Select row(s) to delete.'
            )
            return
        reply = QMessageBox.question(
            self,
            'Confirm delete',
            f'Delete {len(rows)} record(s)? This cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self._db_fname is None:
            QMessageBox.warning(self, 'No database', 'No database configured.')
            return

        errors = []
        for row in rows:
            index = self._row_to_index(row)
            try:
                io.delete_calibration(self._db_fname, index)
            except Exception as exc:
                errors.append(str(exc))

        if errors:
            QMessageBox.critical(self, 'Errors', '\n'.join(errors))
        self._on_refresh()

    def _on_restart(self):
        reply = QMessageBox.question(
            self,
            'Restart database',
            'This will backup the database and keep only the latest '
            'entries.\nContinue?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._db_fname is None:
            QMessageBox.warning(self, 'No database', 'No database configured.')
            return
        try:
            backup_path = io.restart_database(self._db_fname)
            QMessageBox.information(
                self, 'Done', f'Backup saved to: {backup_path}'
            )
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))
        self._on_refresh()

    def _on_compute_transmission(self):
        if self._pc is None or self._db_fname is None:
            return

        try:
            device = self._pc.instrument.config['index']['name']
            lasers = list(self._pc.instrument.lasers.keys())
            ana_config = self._pc.instrument.config['analysis']
        except Exception as exc:
            QMessageBox.critical(
                self, 'Error', f'Could not read microscope config: {exc}'
            )
            return

        self._btn_compute_transmission.setEnabled(False)
        self._emit_status('Computing objective transmission…')

        def _do():
            for laser in lasers:
                io.compute_and_save_factor(
                    self._db_fname, device, laser, ana_config
                )

        def _on_done():
            self._btn_compute_transmission.setEnabled(True)
            self._active_worker = None
            self._emit_status('Objective transmission computed.', 4000)
            self._on_refresh()

        def _on_error(msg):
            self._btn_compute_transmission.setEnabled(True)
            self._active_worker = None
            self._emit_status('', 0)
            QMessageBox.critical(self, 'Error', msg)

        worker = GenericWorker(_do)
        worker.finished.connect(_on_done)
        worker.error.connect(_on_error)
        self._active_worker = worker
        worker.start()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MonetWidget(QWidget):
    """The Monet GUI as a single embeddable ``QWidget``.

    Drop this into any host Qt application — a tab in a host ``QTabWidget``,
    the central widget of a host ``QMainWindow``, anywhere a ``QWidget``
    fits. The host can drive connection programmatically (``set_pc`` or
    ``connect_microscope``) and hide the built-in toolbar.

    Signals
    -------
    status_changed (str, int)
        Bubbled-up status from any tab. ``timeout_ms == 0`` means persistent.
    connected (object)
        Emitted with the calibration-protocol object after a successful
        connection (either via the built-in toolbar or via ``set_pc``).
    connect_error (str)
        Emitted when the built-in ``connect_microscope`` flow fails.
    calibration_started / calibration_finished
        Forwarded from the embedded ``CalibrateTab``.

    Parameters
    ----------
    parent : QWidget, optional
        Standard Qt parent.
    show_toolbar : bool
        If True (default), include the microscope picker + Connect button at
        the top. Pass False to hide them and drive connection from the host.
    tabs : tuple[str, ...]
        Which tabs to include, in display order. Keys: ``'set_power'``,
        ``'calibrate'``, ``'database'``, ``'adjust'``.
    initial_microscope : str, optional
        If given (and ``show_toolbar=True``), select that microscope and
        auto-connect once the widget is shown.
    """

    status_changed = pyqtSignal(str, int)
    connected = pyqtSignal(object)
    connect_error = pyqtSignal(str)
    calibration_started = pyqtSignal()
    calibration_finished = pyqtSignal()

    # Tab key -> (display label, class)
    _TAB_CATALOG = {
        'set_power': ('Set Power', SetPowerTab),
        'calibrate': ('Calibrate', CalibrateTab),
        'database': ('Database', DatabaseTab),
        'adjust': ('Adjust', AdjustTab),
    }

    def __init__(
        self,
        parent=None,
        *,
        show_toolbar=True,
        tabs=('set_power', 'calibrate', 'database'),
        initial_microscope=None,
    ):
        super().__init__(parent)
        self._pc = None
        self._connect_worker = None
        self._tab_keys = tuple(tabs)
        self._tab_widgets = {}
        self._scope_combo = None
        self._btn_connect = None
        self._build_ui(show_toolbar)
        if initial_microscope and self._scope_combo is not None:
            idx = self._scope_combo.findText(initial_microscope)
            if idx >= 0:
                self._scope_combo.setCurrentIndex(idx)
                # Auto-connect after the event loop starts and widget is shown
                QTimer.singleShot(100, self._on_connect)

    def _build_ui(self, show_toolbar):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        if show_toolbar:
            tb_row = QHBoxLayout()
            tb_row.addWidget(QLabel('Microscope: '))
            self._scope_combo = QComboBox()
            for name in sorted(CONFIGS.keys()):
                self._scope_combo.addItem(name)
            tb_row.addWidget(self._scope_combo)
            self._btn_connect = QPushButton('Connect')
            self._btn_connect.clicked.connect(self._on_connect)
            tb_row.addWidget(self._btn_connect)
            tb_row.addStretch()
            layout.addLayout(tb_row)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # Build tabs in requested order; each tab's status signal is
        # re-emitted as the widget's status_changed.
        for key in self._tab_keys:
            if key not in self._TAB_CATALOG:
                raise ValueError(
                    "Unknown tab key {!r}; choose from {}".format(
                        key, list(self._TAB_CATALOG)
                    )
                )
            label, cls = self._TAB_CATALOG[key]
            w = cls()
            w.status.connect(self.status_changed)
            self._tab_widgets[key] = w
            self._tabs.addTab(w, label)

        # Forward calibration lifecycle if CalibrateTab is included.
        cal = self._tab_widgets.get('calibrate')
        if cal is not None:
            cal.calibration_started.connect(self._on_calibration_started)
            cal.calibration_finished.connect(self._on_calibration_finished)

        # Matplotlib backend safety: must happen before any Qt-backed figure
        # is created. Non-fatal if matplotlib isn't installed.
        try:
            import matplotlib.pyplot as _plt

            _plt.switch_backend('agg')
        except Exception:
            pass

    # ---- public API ----------------------------------------------------

    def tab(self, key):
        """Return the embedded tab widget for ``key``, or ``None``."""
        return self._tab_widgets.get(key)

    @property
    def current_microscope(self):
        """Name of the microscope currently selected in the toolbar, or
        ``None`` if the toolbar is hidden / nothing selected."""
        if self._scope_combo is None:
            return None
        return self._scope_combo.currentText() or None

    @property
    def pc(self):
        """The currently-bound calibration-protocol object, or ``None``."""
        return self._pc

    def set_microscope(self, name):
        """Select ``name`` in the toolbar combo (needs ``show_toolbar``)."""
        if self._scope_combo is None:
            raise RuntimeError(
                'Toolbar is hidden; construct with show_toolbar=True or '
                'use set_pc() to inject a protocol object directly.'
            )
        idx = self._scope_combo.findText(name)
        if idx >= 0:
            self._scope_combo.setCurrentIndex(idx)

    def connect_microscope(self, name=None):
        """Start a ``ConnectWorker`` for ``name`` (or the currently selected
        microscope). Emits ``connect_error`` on failure."""
        if name is not None and self._scope_combo is not None:
            self.set_microscope(name)
        if name is None:
            name = self.current_microscope
        if not name:
            self.connect_error.emit('No microscope selected.')
            return

        import copy

        try:
            config = copy.deepcopy(CONFIGS[name])
            protocol = copy.deepcopy(PROTOCOLS.get(name))
        except KeyError as exc:
            self.connect_error.emit(str(exc))
            return

        if self._btn_connect is not None:
            self._btn_connect.setEnabled(False)
        if self._scope_combo is not None:
            self._scope_combo.setEnabled(False)
        self.status_changed.emit('Connecting to {}…'.format(name), 0)

        self._connect_worker = ConnectWorker(
            name, config, protocol, old_pc=self._pc
        )
        self._connect_worker.connected.connect(self._on_connected)
        self._connect_worker.warning.connect(self._on_connect_warning)
        self._connect_worker.error.connect(self._on_connect_error)
        self._connect_worker.finished.connect(self._on_connect_finished)
        self._connect_worker.start()

    def set_pc(self, pc):
        """Bind an externally-built calibration-protocol object.

        Use this when the host application manages the hardware connection
        itself and just wants Monet's UI bound to an existing object
        exposing ``.instrument`` (an ``IlluminationLaserControl``),
        ``.powermeter``, and ``.protocol``. Skips the in-widget
        ``ConnectWorker``.
        """
        self._pc = pc
        for w in self._tab_widgets.values():
            w.set_pc(pc)
        powermeter_ok = getattr(pc, 'powermeter_available', True)
        self._apply_powermeter_state(powermeter_ok)
        self.connected.emit(pc)

    def shutdown(self):
        """Cancel any running calibration and disable all lasers. Call this
        from the host's close handler when the widget is being torn down."""
        cal = self._tab_widgets.get('calibrate')
        if cal is not None:
            try:
                cal.cancel_worker_and_wait()
            except Exception:
                pass
        if self._pc is not None:
            # disconnect() disables every laser and releases all hardware
            # (serial ports, SDK sessions); fall back to just disabling the
            # lasers if a custom pc object has no disconnect().
            try:
                self._pc.disconnect()
            except AttributeError:
                try:
                    for laser in self._pc.instrument.lasers:
                        self._pc.instrument.lasers[laser].enabled = False
                except Exception:
                    pass
            except Exception:
                pass

    # ---- internal handlers --------------------------------------------

    def _on_connect(self):
        name = self.current_microscope
        if not name:
            QMessageBox.warning(
                self, 'No microscope', 'Select a microscope first.'
            )
            return
        self.connect_microscope(name)

    def _on_connected(self, pc):
        self._pc = pc
        name = self.current_microscope or ''
        self.status_changed.emit('Loading data for {}…'.format(name), 0)
        for w in self._tab_widgets.values():
            w.set_pc(pc)
        powermeter_ok = getattr(pc, 'powermeter_available', True)
        self._apply_powermeter_state(powermeter_ok)
        status = 'Connected to {}.'.format(name)
        if not powermeter_ok:
            status += '  [PowerMeter unavailable]'
        self.status_changed.emit(status, 0)
        self.connected.emit(pc)

    def _apply_powermeter_state(self, available):
        """Grey out calibrate tab and feedback controls when no powermeter."""
        cal = self._tab_widgets.get('calibrate')
        if cal is not None:
            self._tabs.setTabEnabled(self._tabs.indexOf(cal), available)
            cal.set_powermeter_available(available)
        sp = self._tab_widgets.get('set_power')
        if sp is not None:
            sp.set_powermeter_available(available)

    def _on_connect_warning(self, msg):
        QMessageBox.warning(self, 'PowerMeter unavailable', msg)

    def _on_connect_error(self, msg):
        self.connect_error.emit(msg)
        QMessageBox.critical(self, 'Connection error', msg)
        self.status_changed.emit('Connection failed: {}'.format(msg), 8000)

    def _on_connect_finished(self):
        if self._btn_connect is not None:
            self._btn_connect.setEnabled(True)
        if self._scope_combo is not None:
            self._scope_combo.setEnabled(True)

    def _on_calibration_started(self):
        sp = self._tab_widgets.get('set_power')
        if sp is not None:
            self._tabs.setTabEnabled(self._tabs.indexOf(sp), False)
        self.calibration_started.emit()

    def _on_calibration_finished(self):
        sp = self._tab_widgets.get('set_power')
        if sp is not None:
            self._tabs.setTabEnabled(self._tabs.indexOf(sp), True)
        self.calibration_finished.emit()

    def closeEvent(self, event):
        # Only fires when this widget is the top-level window; when embedded,
        # the host's close handler is responsible for calling ``shutdown()``.
        self.shutdown()
        super().closeEvent(event)


class MonetMainWindow(QMainWindow):
    """Standalone top-level Monet window — a thin wrapper around
    :class:`MonetWidget`. Used by ``python -m monet gui``. Hosts wanting to
    embed Monet should use :class:`MonetWidget` directly."""

    def __init__(self, initial_microscope=None):
        super().__init__()
        self.setWindowTitle('{} — Laser Power Calibration'.format(_TITLE_BASE))
        self.resize(900, 650)
        self._widget = MonetWidget(self, initial_microscope=initial_microscope)
        self.setCentralWidget(self._widget)
        self._widget.status_changed.connect(self.statusBar().showMessage)
        self._widget.connected.connect(self._on_connected_title)
        self.statusBar().showMessage('Not connected')

    def set_status(self, msg, timeout_ms=0):
        """Back-compat shim: forward to the status bar."""
        self.statusBar().showMessage(msg, timeout_ms)

    def _on_connected_title(self, pc):
        name = self._widget.current_microscope
        if name:
            self.setWindowTitle('{} — {}'.format(_TITLE_BASE, name))

    def closeEvent(self, event):
        self._widget.shutdown()
        event.accept()
