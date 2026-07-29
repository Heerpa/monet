"""Public Qt-level API for embedding Monet in other Qt applications.

This is a thin re-export module; importing it pulls in PyQt6. ``monet``
itself stays Qt-free — only host applications that actually want widgets
should import ``monet.qt``.

Examples
--------
Embed the whole 4-tab interface as a single widget::

    from monet.qt import MonetWidget

    widget = MonetWidget(show_toolbar=False)
    widget.set_pc(my_pc)  # see CalibrationProtocol* in monet.calibrate
    widget.status_changed.connect(host.statusBar().showMessage)
    host_layout.addWidget(widget)

Embed just one tab inside a host's own ``QTabWidget``::

    from monet.qt import SetPowerTab

    tab = SetPowerTab()
    tab.set_pc(my_pc)
    tab.status.connect(host.statusBar().showMessage)
    host_tabs.addTab(tab, 'Laser')

See ``examples/embed_monet.py`` for a runnable demonstration.
"""

from monet.gui import (
    AdjustTab,
    CalibrateTab,
    DatabaseTab,
    MonetMainWindow,
    MonetWidget,
    SetPowerTab,
)

__all__ = [
    "MonetWidget",
    "MonetMainWindow",
    "CalibrateTab",
    "SetPowerTab",
    "AdjustTab",
    "DatabaseTab",
]
