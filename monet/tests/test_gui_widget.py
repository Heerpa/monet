"""
monet/tests/test_gui_widget.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Smoke tests for the embeddable :class:`MonetWidget` and its tabs.
Skipped automatically when PyQt6 is not installed.
"""

import os
import unittest

import pytest

pytest.importorskip('PyQt6')

# Headless backend for environments without a real display.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QApplication  # noqa: E402

from monet.gui import (  # noqa: E402
    AdjustTab,
    CalibrateTab,
    DatabaseTab,
    MonetWidget,
    SetPowerTab,
)

# A single QApplication is required for any QWidget construction.
_app = QApplication.instance() or QApplication([])


class TestMonetWidget(unittest.TestCase):

    def test_construct_without_toolbar(self):
        """show_toolbar=False yields a widget with no microscope combo."""
        w = MonetWidget(show_toolbar=False, tabs=('set_power',))
        self.assertIsNone(w._scope_combo)
        self.assertIsNone(w._btn_connect)
        self.assertIsNotNone(w.tab('set_power'))
        self.assertIsNone(w.tab('calibrate'))
        self.assertIsNone(w.current_microscope)

    def test_construct_with_toolbar(self):
        """show_toolbar=True (default) builds the microscope picker."""
        w = MonetWidget(show_toolbar=True, tabs=('set_power', 'database'))
        self.assertIsNotNone(w._scope_combo)
        self.assertIsNotNone(w._btn_connect)
        self.assertIsNotNone(w.tab('database'))

    def test_status_signal_bubbles_up(self):
        """A tab's ``status`` signal re-emits as status_changed."""
        w = MonetWidget(show_toolbar=False, tabs=('set_power',))
        received = []
        w.status_changed.connect(lambda msg, t: received.append((msg, t)))
        w.tab('set_power').status.emit('hello', 1234)
        self.assertEqual(received, [('hello', 1234)])

    def test_unknown_tab_key_raises(self):
        with self.assertRaises(ValueError):
            MonetWidget(show_toolbar=False, tabs=('nonexistent',))

    def test_individual_tabs_are_standalone(self):
        """Each tab can be constructed without a parent / main window."""
        for cls in (SetPowerTab, CalibrateTab, AdjustTab, DatabaseTab):
            tab = cls()
            received = []
            tab.status.connect(lambda msg, t: received.append((msg, t)))
            tab._emit_status('ping', 500)
            self.assertEqual(received, [('ping', 500)])


if __name__ == '__main__':
    unittest.main()
