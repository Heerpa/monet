#!/usr/bin/env python
"""
    monet/gui.py
    ~~~~~~~~~~~~

    PyQt5 graphical interface for Monet. Provides tabs for calibration,
    laser/attenuator adjustment, power setting, and database management.

    :authors: Heinrich Grabmayr, 2024
    :copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""
import json
import logging

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
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
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from monet import CONFIGS, PROTOCOLS
import monet.io as io

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class CalibrationWorker(QThread):
    """Runs CalibrationProtocol2D.run_protocol() in a background thread."""

    progress = pyqtSignal(int, int, object, object)   # step, total, laser, lpwr
    log_message = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, pc, laser_filter, dry_run, wait_time=0.1, switch_time=10):
        super().__init__()
        self._pc = pc
        self._laser_filter = laser_filter
        self._dry_run = dry_run
        self._wait_time = wait_time
        self._switch_time = switch_time
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True

    def run(self):
        def _progress_callback(step, total, laser, lpwr):
            if self._cancel_requested:
                raise InterruptedError('Calibration cancelled by user.')
            self.progress.emit(step, total, laser, lpwr)
            self.log_message.emit(
                f'Step {step}/{total}: laser {laser} nm at {lpwr} mW done.')

        try:
            self._pc.run_protocol(
                wait_time=self._wait_time,
                switch_time=self._switch_time,
                laser_filter=self._laser_filter,
                dry_run=self._dry_run,
                progress_callback=_progress_callback,
                manage_laser_state=False,
            )
        except InterruptedError:
            self.log_message.emit('Calibration cancelled.')
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.finished.emit()


class ConnectWorker(QThread):
    """Connects to a microscope configuration in a background thread."""

    connected = pyqtSignal(object)   # calibration protocol object
    warning = pyqtSignal(str)        # non-fatal warning (e.g. no powermeter)
    error = pyqtSignal(str)

    def __init__(self, name, config, protocol):
        super().__init__()
        self._name = name
        self._config = config
        self._protocol = protocol

    def run(self):
        import monet.calibrate as mca
        try:
            if self._protocol:
                pc = mca.CalibrationProtocol2D(self._config, self._protocol)
            else:
                pc = mca.CalibrationProtocol1D(self._config)
            if not getattr(pc, 'powermeter_available', True):
                self.warning.emit('PowerMeter not available — calibration and power measurement disabled.')
            self.connected.emit(pc)
        except Exception as exc:
            self.error.emit(str(exc))


class GenericWorker(QThread):
    """Runs any callable in a background thread.

    Emits result(object) on success, error(str) on failure.
    The function's return value is passed to result; None is emitted if the
    function returns nothing.
    """

    result = pyqtSignal(object)
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
# Tab 1 — Calibrate
# ---------------------------------------------------------------------------

class CalibrateTab(QWidget):
    """Tab for running calibration protocols."""

    calibration_started = pyqtSignal()
    calibration_finished = pyqtSignal()

    def __init__(self, main_window):
        super().__init__()
        self._main_window = main_window
        self._pc = None
        self._worker = None
        self._checkboxes = {}
        self._build_ui()

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
        self._dry_run_cb = QCheckBox('Dry run (calibrate without saving to database)')
        layout.addWidget(self._dry_run_cb)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setFormat('Waiting…')
        self._progress.setValue(0)
        layout.addWidget(self._progress)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont('Courier', 9))
        self._log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
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
                wl = self._pc.instrument.config['index'].get('wavelength [nm]', '?')
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
        return [laser for laser, cb in self._checkboxes.items() if cb.isChecked()]

    def _on_start(self):
        if self._pc is None:
            QMessageBox.warning(self, 'Not connected', 'Connect to a microscope first.')
            return

        dry_run = self._dry_run_cb.isChecked()

        # 1D mode
        if not (hasattr(self._pc, 'protocol') and self._pc.protocol):
            if dry_run:
                reply = QMessageBox.question(
                    self, 'Dry run',
                    'Dry run enabled — calibration will NOT be saved. Continue?',
                    QMessageBox.Yes | QMessageBox.No)
                if reply != QMessageBox.Yes:
                    return
            self._log.append('Starting 1D calibration…')
            self._main_window.set_status('Running 1D calibration…')
            try:
                self._pc.calibrate(dry_run=dry_run)
                self._log.append('Done.')
                self._main_window.set_status('Calibration complete.', 5000)
            except Exception as exc:
                QMessageBox.critical(self, 'Error', str(exc))
                self._main_window.set_status('Calibration failed.', 5000)
            return

        selected = self._selected_lasers()
        if not selected:
            QMessageBox.warning(self, 'No wavelengths', 'Select at least one wavelength.')
            return

        if dry_run:
            reply = QMessageBox.question(
                self, 'Dry run',
                'Dry run enabled — calibration will NOT be saved. Continue?',
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        self._log.clear()
        self._log.append(f'Starting calibration for lasers: {selected}')
        self._progress.setValue(0)
        self._progress.setFormat('Starting…')
        self._btn_start.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._main_window.set_status('Calibration running…')

        self.calibration_started.emit()

        self._worker = CalibrationWorker(
            self._pc, laser_filter=selected, dry_run=dry_run)
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._log.append)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_cancel(self):
        if self._worker:
            self._worker.request_cancel()
            self._btn_cancel.setEnabled(False)
            self._main_window.set_status('Cancelling calibration…')

    def _on_progress(self, step, total, laser, lpwr):
        self._progress.setMaximum(total)
        self._progress.setValue(step)
        self._progress.setFormat(f'{laser} nm / {lpwr} mW  ({step}/{total})')
        self._main_window.set_status(
            f'Calibrating: laser {laser} nm at {lpwr} mW  ({step}/{total})')

    def _on_finished(self):
        self._log.append('Calibration complete.')
        self._progress.setFormat('Done')
        self._btn_start.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._worker = None
        self._main_window.set_status('Calibration complete.', 5000)
        self.calibration_finished.emit()

    def _on_error(self, msg):
        self._log.append(f'ERROR: {msg}')
        QMessageBox.critical(self, 'Calibration error', msg)
        self._btn_start.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._worker = None
        self._main_window.set_status(f'Calibration error: {msg}', 5000)
        self.calibration_finished.emit()

    def set_powermeter_available(self, available):
        """Enable or disable calibration controls based on powermeter availability."""
        self._btn_start.setEnabled(available)
        if not available:
            self._log.append(
                'WARNING: PowerMeter not available. Calibration is disabled.')

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

    def __init__(self, main_window):
        super().__init__()
        self._main_window = main_window
        self._pc = None
        self._active_worker = None   # keep alive to prevent GC
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Refresh row
        refresh_row = QHBoxLayout()
        self._btn_refresh = QPushButton('Refresh')
        self._btn_refresh.setToolTip(
            'Re-read attenuator position and laser power from hardware')
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
            self._main_window.set_status('Ready', 2000)

        self._run_hw(_do, 'Refreshing device values…', on_result=_on_result)

    # --- helpers for async hardware ops ---

    def _hw_buttons(self, enabled):
        for btn in (self._btn_att_set, self._btn_att_home,
                    self._btn_pwr_set, self._btn_bp_open, self._btn_bp_close,
                    self._btn_refresh):
            btn.setEnabled(enabled)

    def _run_hw(self, func, status_msg, on_done=None, on_result=None):
        """Run a hardware callable in a GenericWorker, updating status bar."""
        self._hw_buttons(False)
        self._main_window.set_status(status_msg)

        worker = GenericWorker(func)

        def _on_success(val):
            if on_result:
                on_result(val)
            if on_done:
                on_done()

        def _on_error(msg):
            self._status.setText(f'Error: {msg}')
            self._main_window.set_status(f'Error: {msg}', 5000)
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

        def _done():
            self._status.setText(f'Attenuator set to {pos}.')
            self._main_window.set_status('Ready', 2000)

        self._run_hw(_do, f'Setting attenuator to {pos}…', on_done=_done)

    def _on_att_home(self):
        if self._pc is None:
            return

        def _do():
            self._pc.instrument.attenuator.home()

        def _done():
            self._status.setText('Attenuator homed.')
            self._main_window.set_status('Ready', 2000)
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

        def _done():
            self._status.setText(f'Laser power set to {pwr} mW.')
            self._main_window.set_status('Ready', 2000)

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
            self._main_window.set_status('Ready', 2000)

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
            self._main_window.set_status('Ready', 2000)

        self._run_hw(_do, 'Closing beampath…', on_done=_done)

    def _on_autoshutter(self, state):
        if self._pc is None:
            return
        try:
            self._pc.instrument.beampath.objects['shutter'].autoshutter = (
                state == Qt.Checked)
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))


# ---------------------------------------------------------------------------
# Tab 3 — Set Power
# ---------------------------------------------------------------------------

class SetPowerTab(QWidget):
    """Tab for setting output power using calibration data."""

    def __init__(self, main_window):
        super().__init__()
        self._main_window = main_window
        self._pc = None
        self._active_worker = None   # keep alive to prevent GC
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Laser selector + on/off
        laser_row = QHBoxLayout()
        laser_row.addWidget(QLabel('Laser:'))
        self._laser_combo = QComboBox()
        self._laser_combo.currentIndexChanged.connect(self._on_laser_changed)
        laser_row.addWidget(self._laser_combo)
        self._btn_onoff = QPushButton('ON')
        self._btn_onoff.setCheckable(True)
        self._btn_onoff.clicked.connect(self._on_toggle_laser)
        laser_row.addWidget(self._btn_onoff)
        laser_row.addStretch()
        layout.addLayout(laser_row)

        # ── Adjustment group box ──────────────────────────────────────────
        adj_group = QGroupBox('Power adjustment')
        adj_layout = QVBoxLayout()
        adj_group.setLayout(adj_layout)

        # Mode selector
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel('Mode:'))
        self._mode_combo = QComboBox()
        self._mode_combo.addItem('Adjust attenuator', 'attenuator')
        self._mode_combo.addItem('Adjust laser power (fixed attenuator)', 'laser')
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
        feedback_row.addStretch()
        adj_layout.addLayout(feedback_row)

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
        pwr_row.addStretch()
        adj_layout.addLayout(pwr_row)

        layout.addWidget(adj_group)
        # ─────────────────────────────────────────────────────────────────

        # Multi-laser checkbox
        self._multi_cb = QCheckBox('Multi-laser mode (keep other lasers on when switching)')
        layout.addWidget(self._multi_cb)

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

    def _on_laser_changed(self, idx):
        if self._pc is None:
            return
        laser = self._laser_combo.currentData()
        if laser is None:
            return
        try:
            enabled = self._pc.instrument.lasers[laser].enabled
            self._btn_onoff.setChecked(enabled)
            self._btn_onoff.setText('OFF' if enabled else 'ON')
        except Exception as exc:
            self._status.setText(str(exc))

    # --- helpers ---

    def _beampath_matches(self, target_positions):
        """Return True if every key in target_positions already matches the
        current beampath position, so the move can be skipped."""
        try:
            if not getattr(self._pc.instrument, 'use_beampath', False):
                return False
            current = self._pc.instrument.beampath.positions
            return all(current.get(k) == v for k, v in target_positions.items())
        except Exception:
            return False

    def _action_buttons(self, enabled):
        for btn in (self._btn_onoff, self._btn_set, self._btn_bp_open,
                    self._btn_bp_close, self._btn_measure, self._btn_alloff):
            btn.setEnabled(enabled)

    def _run_hw(self, func, status_msg, on_done=None, on_result=None):
        """Run a hardware callable in a GenericWorker."""
        self._action_buttons(False)
        self._main_window.set_status(status_msg)

        worker = GenericWorker(func)

        def _on_success(val):
            if on_result:
                on_result(val)
            if on_done:
                on_done()

        def _on_error(msg):
            self._status.setText(f'Error: {msg}')
            self._main_window.set_status(f'Error: {msg}', 5000)
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
            self._btn_onoff.setText('OFF' if checked else 'ON')
            self._status.setText(f'Laser {laser} nm {"on" if checked else "off"}.')
            self._main_window.set_status('Ready', 2000)

        self._run_hw(_do, f'{"Enabling" if checked else "Disabling"} laser {laser} nm…',
                     on_done=_done)

    def _on_set_power(self):
        if self._pc is None:
            return
        pwr = self._pwr_spin.value()
        laser = self._laser_combo.currentData()
        mode = self._mode_combo.currentData()

        if mode == 'laser' and not hasattr(self._pc.instrument, 'set_power_fixed_attenuator'):
            QMessageBox.warning(
                self, 'Not supported',
                'Fixed-attenuator mode requires a multi-laser instrument '
                'with calibrations at multiple laser power levels.')
            return

        # --- Resolve feedback / beampath settings on the main thread ---
        use_feedback = (self._feedback_cb.isChecked()
                        and getattr(self._pc, 'powermeter_available', False))
        protocol = getattr(self._pc, 'protocol', None) or {}
        bp_dict = protocol.get('beampath') or {}
        bp_for_laser = bp_dict.get(laser)          # (1) open for wavelength
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
                    self, 'Feedback — beampath',
                    'Move beampath to "start_calibrate" position before '
                    'feedback measurement?\n\n'
                    'Click Cancel to perform the initial set without feedback.',
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
                if reply == QMessageBox.Cancel:
                    use_feedback = False
                elif reply == QMessageBox.Yes:
                    do_start_cal = True

        max_dev_pct = self._feedback_tol_spin.value()
        MAX_ITER = 20

        # --- Build the background callable ---
        if not use_feedback:
            if mode == 'laser':
                def _do():
                    self._pc.instrument.set_power_fixed_attenuator(pwr, laser)
                done_msg = (f'Laser power adjusted for {pwr} mW output '
                            f'(laser {laser} nm, attenuator fixed).')
                status_msg = f'Adjusting laser power for {pwr} mW…'
            else:
                def _do():
                    self._pc.instrument.laser = laser
                    self._pc.instrument.power = pwr
                done_msg = f'Power set to {pwr} mW for laser {laser} nm.'
                status_msg = f'Setting power to {pwr} mW…'

            def _done():
                self._status.setText(done_msg)
                self._main_window.set_status('Ready', 2000)

            self._run_hw(_do, status_msg, on_done=_done)

        else:
            def _do():
                import time
                import numpy as _np

                # Initial power setting
                if mode == 'laser':
                    self._pc.instrument.set_power_fixed_attenuator(pwr, laser)
                else:
                    self._pc.instrument.laser = laser
                    self._pc.instrument.power = pwr

                # (1) Open beampath for the selected wavelength first
                if bp_for_laser is not None:
                    self._pc.instrument.beampath.positions = bp_for_laser

                # Move to measurement position (start_calibrate) and settle
                moved_to_meas = False
                if do_start_cal and bp_start_cal is not None:
                    self._pc.instrument.beampath.positions = bp_start_cal
                    moved_to_meas = True
                if bp_for_laser is not None or moved_to_meas:
                    time.sleep(2)

                # Feedback loop
                converged = False
                out_of_range_warned = False
                measured = self._pc.powermeter.read()
                for _ in range(MAX_ITER):
                    dev_pct = (abs(measured - pwr) / pwr * 100.0
                               if pwr > 0 else 0.0)
                    if dev_pct <= max_dev_pct:
                        converged = True
                        break
                    if measured <= 0:
                        break  # cannot correct without light
                    if mode == 'laser':
                        # Proportional correction: scale current laser power
                        curr_lp = self._pc.instrument.lasers[laser].power
                        self._pc.instrument.lasers[laser].power = (
                            curr_lp * pwr / measured)
                    else:
                        # (4) Attenuator: clamp corrected target to analyzer
                        # output range before calling estimate() — without
                        # clamping, the ValueError raised by the analyzer for
                        # out-of-range targets would abort the loop.
                        corrected_target = pwr * pwr / measured
                        try:
                            out_range = (
                                self._pc.instrument.analyzer.output_range())
                            lo = float(out_range[0])
                            hi = float(out_range[1])
                            clamped = float(
                                _np.clip(corrected_target, lo, hi))
                            if abs(clamped - corrected_target) > 1e-9:
                                out_of_range_warned = True
                            corrected_target = clamped
                        except Exception:
                            corrected_target = max(0.0, corrected_target)
                        att_pos = self._pc.instrument.analyzer.estimate(
                            corrected_target)
                        self._pc.instrument.attenuator.set(att_pos)
                        time.sleep(2)
                    time.sleep(0.5)
                    measured = self._pc.powermeter.read()

                # Restore beampath, mirroring the calibration routine
                if bp_end_calibrate is not None:
                    self._pc.instrument.beampath.positions = bp_end_calibrate
                if bp_end is not None:
                    self._pc.instrument.beampath.positions = bp_end

                # (5) Calibration deviation — for laser-power mode use the
                # linear interpolation model so that the intermediate laser
                # power set by feedback is properly accounted for.
                cali_pred = None
                try:
                    if (mode == 'laser' and
                            hasattr(self._pc.instrument,
                                    'predict_power_fixed_attenuator')):
                        curr_lp = self._pc.instrument.lasers[laser].power
                        cali_pred = (
                            self._pc.instrument
                            .predict_power_fixed_attenuator(curr_lp, laser))
                    else:
                        # Attenuator mode: calibration reads att pos through
                        # the analyzer directly
                        cali_pred = self._pc.instrument.power
                except Exception:
                    cali_pred = None

                return measured, converged, cali_pred, out_of_range_warned

            def _on_result(res):
                measured, converged, cali_pred, out_of_range_warned = res
                dev_pct = (abs(measured - pwr) / pwr * 100.0
                           if pwr > 0 else 0.0)
                parts = [f'Target {pwr} mW → measured {measured:.3f} mW '
                         f'({dev_pct:.1f}% deviation)']
                if not converged:
                    parts.append(f'(did not converge within {MAX_ITER} steps)')
                if out_of_range_warned:
                    parts.append('(attenuator range limit reached)')
                self._status.setText('  '.join(parts))
                if cali_pred is not None and cali_pred > 0:
                    cali_dev_pct = (measured - cali_pred) / cali_pred * 100.0
                    self._main_window.set_status(
                        f'Calibration deviation: {cali_dev_pct:+.1f}%'
                        f'  (calibration predicts {cali_pred:.3f} mW,'
                        f' measured {measured:.3f} mW)')
                else:
                    self._main_window.set_status('Ready', 2000)

            self._run_hw(_do, f'Setting {pwr} mW with feedback…',
                         on_result=_on_result)

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
            self._main_window.set_status('Ready', 2000)

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
            self._main_window.set_status('Ready', 2000)

        self._run_hw(_do, 'Closing beampath…', on_done=_done)

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
                do_start_cal = True   # already in position, no need to ask
            else:
                reply = QMessageBox.question(
                    self, 'Measurement beampath',
                    'Set beampath to "start_calibrate" position before measuring?',
                    QMessageBox.Yes | QMessageBox.No)
                do_start_cal = (reply == QMessageBox.Yes)

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
            measured = self._pc.powermeter.read()
            try:
                cali_pred = self._pc.instrument.power
            except Exception:
                cali_pred = None
            return measured, cali_pred

        def _on_val(res):
            measured, cali_pred = res
            try:
                unit = self._pc.powermeter.unit
            except Exception:
                unit = 'a.u.'
            self._status.setText(f'Measured power: {measured:.3f} {unit}')
            if cali_pred is not None and cali_pred > 0:
                cali_dev_pct = (measured - cali_pred) / cali_pred * 100.0
                self._main_window.set_status(
                    f'Calibration deviation: {cali_dev_pct:+.1f}%'
                    f'  (calibration predicts {cali_pred:.3f} {unit},'
                    f' measured {measured:.3f} {unit})')
            else:
                self._main_window.set_status('Ready', 2000)

        self._run_hw(_do, 'Measuring power…', on_result=_on_val)

    def set_powermeter_available(self, available):
        """Enable or disable powermeter-dependent controls."""
        self._btn_measure.setEnabled(available)
        self._feedback_cb.setEnabled(available)
        self._feedback_tol_spin.setEnabled(available)
        if not available:
            self._feedback_cb.setChecked(False)

    def _on_all_off(self):
        if self._pc is None:
            return

        def _do():
            for laser in self._pc.instrument.lasers:
                self._pc.instrument.lasers[laser].enabled = False

        def _done():
            self._status.setText('All lasers switched off.')
            self._btn_onoff.setChecked(False)
            self._btn_onoff.setText('ON')
            self._main_window.set_status('Ready', 2000)

        self._run_hw(_do, 'Switching all lasers off…', on_done=_done)


# ---------------------------------------------------------------------------
# Tab 4 — Database
# ---------------------------------------------------------------------------

class DatabaseTab(QWidget):
    """Tab for viewing and managing calibration records."""

    COLUMNS = ['Microscope', 'Wavelength (nm)', 'Power (mW)', 'Date', 'Time',
               'Model', 'Parameters']

    def __init__(self, main_window):
        super().__init__()
        self._main_window = main_window
        self._pc = None
        self._db_fname = None
        self._build_ui()

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
        btn_delete = QPushButton('Delete selected')
        btn_delete.clicked.connect(self._on_delete)
        ctrl_row.addWidget(btn_delete)
        btn_restart = QPushButton('Restart DB')
        btn_restart.clicked.connect(self._on_restart)
        ctrl_row.addWidget(btn_restart)
        layout.addLayout(ctrl_row)

        # Table
        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)

        # Status
        self._status = QLabel('')
        layout.addWidget(self._status)

    def set_pc(self, pc):
        self._pc = pc
        if pc is not None:
            self._db_fname = pc.instrument.config.get('database')
        else:
            self._db_fname = None
        self._on_refresh()

    def _on_refresh(self):
        if self._db_fname is None:
            self._status.setText('No database configured.')
            return
        try:
            scope_filter = self._scope_combo.currentData()
            index = {}
            if scope_filter:
                index['name'] = scope_filter
            records_df = io.load_database(self._db_fname, index, time_idx='all')
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
                    {k: round(v, 4) if isinstance(v, float) else v
                     for k, v in params.items()})
                params_short = params_str[:40] + ('…' if len(params_str) > 40 else '')

                values = [str(scope), str(wl), str(pwr), str(date), str(time_val),
                          '', params_short]
                for col, val in enumerate(values):
                    item = QTableWidgetItem(val)
                    if col == len(values) - 1:
                        item.setToolTip(params_str)
                    self._table.setItem(row_pos, col, item)

            # Repopulate scope combo without clearing "all"
            current_scopes = {self._scope_combo.itemText(i)
                              for i in range(1, self._scope_combo.count())}
            for sc in sorted(known_scopes - current_scopes):
                self._scope_combo.addItem(sc, sc)

        total = self._table.rowCount()
        self._status.setText(f'{total} record(s)')

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
            QMessageBox.information(self, 'Nothing selected', 'Select row(s) to delete.')
            return
        reply = QMessageBox.question(
            self, 'Confirm delete',
            f'Delete {len(rows)} record(s)? This cannot be undone.',
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
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
            self, 'Restart database',
            'This will backup the database and keep only the latest entries.\nContinue?',
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        if self._db_fname is None:
            QMessageBox.warning(self, 'No database', 'No database configured.')
            return
        try:
            backup_path = io.restart_database(self._db_fname)
            QMessageBox.information(self, 'Done', f'Backup saved to: {backup_path}')
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))
        self._on_refresh()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MonetMainWindow(QMainWindow):
    """Main application window with microscope selector and four tabs."""

    def __init__(self, initial_microscope=None):
        super().__init__()
        self._pc = None
        self._connect_worker = None
        self.setWindowTitle('Monet — Laser Power Calibration')
        self.resize(900, 650)
        self._build_ui()
        if initial_microscope:
            idx = self._scope_combo.findText(initial_microscope)
            if idx >= 0:
                self._scope_combo.setCurrentIndex(idx)
                # Auto-connect after the event loop starts and window is shown
                QTimer.singleShot(100, self._on_connect)

    def _build_ui(self):
        # Toolbar
        toolbar = QToolBar('Connection')
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel('Microscope: '))
        self._scope_combo = QComboBox()
        for name in sorted(CONFIGS.keys()):
            self._scope_combo.addItem(name)
        toolbar.addWidget(self._scope_combo)

        self._btn_connect = QPushButton('Connect')
        self._btn_connect.clicked.connect(self._on_connect)
        toolbar.addWidget(self._btn_connect)

        # Central widget with tabs
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        self._tab_calibrate = CalibrateTab(self)
        self._tab_adjust = AdjustTab(self)
        self._tab_setpower = SetPowerTab(self)
        self._tab_database = DatabaseTab(self)

        self._tabs.addTab(self._tab_calibrate, 'Calibrate')
        self._tabs.addTab(self._tab_adjust, 'Adjust')
        self._tabs.addTab(self._tab_setpower, 'Set Power')
        self._tabs.addTab(self._tab_database, 'Database')

        # Connect calibration signals
        self._tab_calibrate.calibration_started.connect(self._on_calibration_started)
        self._tab_calibrate.calibration_finished.connect(self._on_calibration_finished)

        # Switch matplotlib to non-interactive Agg backend before any
        # calibration plotting runs.  Must happen before pyplot creates
        # any Qt-backed figures.  Failures are non-fatal.
        try:
            import matplotlib.pyplot as _plt
            _plt.switch_backend('agg')
        except Exception:
            pass

        # Status bar
        self.statusBar().showMessage('Not connected')

    def set_status(self, msg, timeout_ms=0):
        """Update the status bar. timeout_ms=0 means persistent."""
        self.statusBar().showMessage(msg, timeout_ms)

    def _on_connect(self):
        name = self._scope_combo.currentText()
        if not name:
            QMessageBox.warning(self, 'No microscope', 'Select a microscope first.')
            return

        try:
            config = CONFIGS[name]
            protocol = PROTOCOLS.get(name)
        except KeyError as exc:
            QMessageBox.critical(self, 'Config not found', str(exc))
            return

        self._btn_connect.setEnabled(False)
        self._scope_combo.setEnabled(False)
        self.set_status(f'Connecting to {name}…')

        self._connect_worker = ConnectWorker(name, config, protocol)
        self._connect_worker.connected.connect(self._on_connected)
        self._connect_worker.warning.connect(self._on_connect_warning)
        self._connect_worker.error.connect(self._on_connect_error)
        self._connect_worker.finished.connect(self._on_connect_finished)
        self._connect_worker.start()

    def _on_connected(self, pc):
        self._pc = pc
        name = self._scope_combo.currentText()
        self.set_status(f'Loading data for {name}…')
        self._refresh_all_tabs()
        powermeter_ok = getattr(pc, 'powermeter_available', True)
        self._apply_powermeter_state(powermeter_ok)
        self.setWindowTitle(f'Monet — {name}')
        status = f'Connected to {name}.'
        if not powermeter_ok:
            status += '  [PowerMeter unavailable]'
        self.set_status(status)

    def _apply_powermeter_state(self, available):
        """Grey out calibrate tab and measure button when powermeter is absent."""
        self._tabs.setTabEnabled(0, available)   # Calibrate tab
        self._tab_calibrate.set_powermeter_available(available)
        self._tab_setpower.set_powermeter_available(available)

    def _on_connect_warning(self, msg):
        QMessageBox.warning(self, 'PowerMeter unavailable', msg)

    def _on_connect_error(self, msg):
        QMessageBox.critical(self, 'Connection error', msg)
        self.set_status(f'Connection failed: {msg}', 8000)

    def _on_connect_finished(self):
        self._btn_connect.setEnabled(True)
        self._scope_combo.setEnabled(True)

    def _refresh_all_tabs(self):
        self._tab_calibrate.set_pc(self._pc)
        self._tab_adjust.set_pc(self._pc)
        self._tab_setpower.set_pc(self._pc)
        self._tab_database.set_pc(self._pc)

    def _on_calibration_started(self):
        self._tabs.setTabEnabled(1, False)   # Adjust
        self._tabs.setTabEnabled(2, False)   # Set Power

    def _on_calibration_finished(self):
        self._tabs.setTabEnabled(1, True)
        self._tabs.setTabEnabled(2, True)

    def closeEvent(self, event):
        # Cancel any running calibration
        self._tab_calibrate.cancel_worker_and_wait()

        # Disable all lasers
        if self._pc is not None:
            try:
                for laser in self._pc.instrument.lasers:
                    self._pc.instrument.lasers[laser].enabled = False
            except Exception:
                pass

        event.accept()
