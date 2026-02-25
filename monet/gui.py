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

from PyQt5.QtCore import Qt, QThread, pyqtSignal
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
# Background calibration worker
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
            try:
                self._pc.calibrate(dry_run=dry_run)
                self._log.append('Done.')
            except Exception as exc:
                QMessageBox.critical(self, 'Error', str(exc))
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

    def _on_progress(self, step, total, laser, lpwr):
        self._progress.setMaximum(total)
        self._progress.setValue(step)
        self._progress.setFormat(f'{laser} nm / {lpwr} mW  ({step}/{total})')

    def _on_finished(self):
        self._log.append('Calibration complete.')
        self._progress.setFormat('Done')
        self._btn_start.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._worker = None
        self.calibration_finished.emit()

    def _on_error(self, msg):
        self._log.append(f'ERROR: {msg}')
        QMessageBox.critical(self, 'Calibration error', msg)
        self._btn_start.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._worker = None
        self.calibration_finished.emit()

    def cancel_worker_and_wait(self):
        if self._worker and self._worker.isRunning():
            self._worker.request_cancel()
            self._worker.wait(5000)


# ---------------------------------------------------------------------------
# Tab 2 — Adjust
# ---------------------------------------------------------------------------

class AdjustTab(QWidget):
    """Tab for direct laser/attenuator adjustment."""

    def __init__(self, main_window):
        super().__init__()
        self._main_window = main_window
        self._pc = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Laser selector
        laser_row = QHBoxLayout()
        laser_row.addWidget(QLabel('Laser:'))
        self._laser_combo = QComboBox()
        self._laser_combo.currentIndexChanged.connect(self._on_laser_changed)
        laser_row.addWidget(self._laser_combo)
        self._btn_enable = QPushButton('Enable laser')
        self._btn_enable.setCheckable(True)
        self._btn_enable.clicked.connect(self._on_toggle_laser)
        laser_row.addWidget(self._btn_enable)
        laser_row.addStretch()
        layout.addLayout(laser_row)

        # Attenuator group
        att_group = QGroupBox('Attenuator')
        att_layout = QHBoxLayout()
        att_layout.addWidget(QLabel('Position:'))
        self._att_spin = QDoubleSpinBox()
        self._att_spin.setRange(-1e6, 1e6)
        self._att_spin.setDecimals(3)
        att_layout.addWidget(self._att_spin)
        btn_att_set = QPushButton('Set')
        btn_att_set.clicked.connect(self._on_att_set)
        btn_att_home = QPushButton('Home')
        btn_att_home.clicked.connect(self._on_att_home)
        att_layout.addWidget(btn_att_set)
        att_layout.addWidget(btn_att_home)
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
        btn_pwr_set = QPushButton('Set')
        btn_pwr_set.clicked.connect(self._on_pwr_set)
        pwr_layout.addWidget(btn_pwr_set)
        pwr_layout.addStretch()
        pwr_group.setLayout(pwr_layout)
        layout.addWidget(pwr_group)

        # Beampath controls
        bp_row = QHBoxLayout()
        btn_open = QPushButton('Open beampath')
        btn_close = QPushButton('Close beampath')
        btn_open.clicked.connect(self._on_bp_open)
        btn_close.clicked.connect(self._on_bp_close)
        bp_row.addWidget(btn_open)
        bp_row.addWidget(btn_close)
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
        self._laser_combo.blockSignals(True)
        self._laser_combo.clear()
        if pc is not None:
            for laser in pc.instrument.lasers:
                self._laser_combo.addItem(str(laser), laser)
        self._laser_combo.blockSignals(False)
        if self._laser_combo.count():
            self._on_laser_changed(0)

    def _current_laser_key(self):
        return self._laser_combo.currentData()

    def _on_laser_changed(self, idx):
        if self._pc is None:
            return
        laser = self._current_laser_key()
        if laser is None:
            return
        try:
            enabled = self._pc.instrument.lasers[laser].enabled
            self._btn_enable.setChecked(enabled)
            self._btn_enable.setText('Disable laser' if enabled else 'Enable laser')
        except Exception as exc:
            self._status.setText(str(exc))

    def _on_toggle_laser(self, checked):
        if self._pc is None:
            return
        laser = self._current_laser_key()
        try:
            self._pc.instrument.laser = laser
            self._pc.instrument.laser_enabled = checked
            self._btn_enable.setText('Disable laser' if checked else 'Enable laser')
            self._status.setText(f'Laser {laser} nm {"enabled" if checked else "disabled"}.')
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))

    def _on_att_set(self):
        if self._pc is None:
            return
        pos = self._att_spin.value()
        try:
            self._pc.instrument.attenuator.set(pos)
            self._status.setText(f'Attenuator set to {pos}.')
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))

    def _on_att_home(self):
        if self._pc is None:
            return
        try:
            self._pc.instrument.attenuator.home()
            self._status.setText('Attenuator homed.')
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))

    def _on_pwr_set(self):
        if self._pc is None:
            return
        pwr = self._pwr_spin.value()
        try:
            laser = self._current_laser_key()
            self._pc.instrument.laser = laser
            self._pc.instrument.laserpower = pwr
            self._status.setText(f'Laser power set to {pwr} mW.')
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))

    def _on_bp_open(self):
        if self._pc is None:
            return
        try:
            laser = self._current_laser_key()
            self._pc.instrument.beampath.positions = (
                self._pc.protocol['beampath'][laser])
            self._status.setText('Beampath opened.')
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))

    def _on_bp_close(self):
        if self._pc is None:
            return
        try:
            self._pc.instrument.beampath.positions = (
                self._pc.protocol['beampath']['end'])
            self._status.setText('Beampath closed.')
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))

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

        # Power setting
        pwr_row = QHBoxLayout()
        pwr_row.addWidget(QLabel('Target power (mW):'))
        self._pwr_spin = QDoubleSpinBox()
        self._pwr_spin.setRange(0, 10000)
        self._pwr_spin.setDecimals(2)
        pwr_row.addWidget(self._pwr_spin)
        btn_set = QPushButton('Set')
        btn_set.clicked.connect(self._on_set_power)
        pwr_row.addWidget(btn_set)
        pwr_row.addStretch()
        layout.addLayout(pwr_row)

        # Multi-laser checkbox
        self._multi_cb = QCheckBox('Multi-laser mode (keep other lasers on when switching)')
        layout.addWidget(self._multi_cb)

        # Measure + All off
        misc_row = QHBoxLayout()
        btn_measure = QPushButton('Measure')
        btn_measure.clicked.connect(self._on_measure)
        btn_alloff = QPushButton('All lasers OFF')
        btn_alloff.clicked.connect(self._on_all_off)
        misc_row.addWidget(btn_measure)
        misc_row.addWidget(btn_alloff)
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

    def _on_toggle_laser(self, checked):
        if self._pc is None:
            return
        laser = self._laser_combo.currentData()
        try:
            if not self._multi_cb.isChecked():
                for lsr in self._pc.instrument.lasers:
                    if lsr != laser:
                        self._pc.instrument.lasers[lsr].enabled = False
            self._pc.instrument.laser = laser
            self._pc.instrument.laser_enabled = checked
            self._btn_onoff.setText('OFF' if checked else 'ON')
            self._status.setText(f'Laser {laser} nm {"on" if checked else "off"}.')
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))

    def _on_set_power(self):
        if self._pc is None:
            return
        pwr = self._pwr_spin.value()
        laser = self._laser_combo.currentData()
        try:
            self._pc.instrument.laser = laser
            self._pc.instrument.power = pwr
            self._status.setText(f'Power set to {pwr} mW for laser {laser} nm.')
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))

    def _on_measure(self):
        if self._pc is None:
            return
        try:
            val = self._pc.powermeter.read()
            self._status.setText(f'Measured power: {val:.3f} {self._pc.powermeter.unit}')
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))

    def _on_all_off(self):
        if self._pc is None:
            return
        try:
            for laser in self._pc.instrument.lasers:
                self._pc.instrument.lasers[laser].enabled = False
            self._status.setText('All lasers switched off.')
            self._btn_onoff.setChecked(False)
            self._btn_onoff.setText('ON')
        except Exception as exc:
            QMessageBox.critical(self, 'Error', str(exc))


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
        self._worker = None
        self.setWindowTitle('Monet — Laser Power Calibration')
        self.resize(900, 650)
        self._build_ui()
        if initial_microscope:
            idx = self._scope_combo.findText(initial_microscope)
            if idx >= 0:
                self._scope_combo.setCurrentIndex(idx)

    def _build_ui(self):
        # Toolbar
        toolbar = QToolBar('Connection')
        self.addToolBar(toolbar)

        toolbar.addWidget(QLabel('Microscope: '))
        self._scope_combo = QComboBox()
        for name in sorted(CONFIGS.keys()):
            self._scope_combo.addItem(name)
        toolbar.addWidget(self._scope_combo)

        btn_connect = QPushButton('Connect')
        btn_connect.clicked.connect(self._on_connect)
        toolbar.addWidget(btn_connect)

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

    def _on_connect(self):
        import monet.calibrate as mca
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

        try:
            if protocol:
                self._pc = mca.CalibrationProtocol2D(config, protocol)
            else:
                self._pc = mca.CalibrationProtocol1D(config)
        except Exception as exc:
            QMessageBox.critical(self, 'Connection error', str(exc))
            return

        self._refresh_all_tabs()
        self.setWindowTitle(f'Monet — {name}')

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
