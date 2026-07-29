"""Embedding Monet in another Qt application.

Two patterns are shown:

  1. ``MonetWidget`` — the whole 4-tab interface as one ``QWidget``.
  2. ``SetPowerTab`` — a single tab dropped into the host's own layout.

Run with::

    python examples/embed_monet.py <MicroscopeName>

(omit the argument to skip auto-connection).
"""

import sys

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QLabel,
)

from monet.qt import MonetWidget, SetPowerTab


class HostApplication(QMainWindow):
    """A pretend host application with its own tab bar. Tab 1 holds the
    whole Monet GUI; tab 2 shows how to embed a single Monet tab next to
    the host's own widgets."""

    def __init__(self, initial_microscope=None):
        super().__init__()
        self.setWindowTitle("Example host — embedding Monet")
        self.resize(1000, 700)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        # --- Pattern 1: full Monet GUI in one tab ---------------------
        # show_toolbar=True (the default) lets users pick the microscope
        # from inside the embedded widget. Pass show_toolbar=False if the
        # host drives connection programmatically (Pattern 2).
        self.monet = MonetWidget(
            show_toolbar=True,
            initial_microscope=initial_microscope,
        )
        self.monet.status_changed.connect(self.statusBar().showMessage)
        tabs.addTab(self.monet, "Monet (full)")

        # --- Pattern 2: just Set Power, alongside host widgets --------
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.addWidget(
            QLabel("Host widgets above; just the Monet Set Power tab below.")
        )
        self.set_power = SetPowerTab()
        # Pipe its status into the host's status bar — note that the
        # individual tab exposes ``status`` (not ``status_changed``).
        self.set_power.status.connect(self.statusBar().showMessage)
        page_layout.addWidget(self.set_power)
        tabs.addTab(page, "Just Set Power")

        # Pattern 2 needs an explicitly-supplied calibration-protocol object.
        # When using the toolbar (Pattern 1) Monet builds one itself via
        # ``ConnectWorker``; here we mirror that by listening for the
        # ``connected`` signal of the full widget and reusing the protocol
        # object so the same hardware drives both tabs.
        self.monet.connected.connect(self.set_power.set_pc)

        self.statusBar().showMessage("Ready")

    def closeEvent(self, event):
        # MonetWidget owns hardware workers — let it clean up.
        self.monet.shutdown()
        event.accept()


def main():
    app = QApplication(sys.argv)
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    win = HostApplication(initial_microscope=initial)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
