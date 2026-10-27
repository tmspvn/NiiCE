"""Shared test setup: delete every Qt window after each test.

Windows left alive until the interpreter exits are freed in an arbitrary order, which can crash
PySide at shutdown (after all tests passed). Real NiiCE deletes its windows when they close.
"""
import pytest


@pytest.fixture(autouse=True)
def _delete_qt_windows():
    yield
    try:
        import shiboken6
        from PySide6 import QtWidgets
    except ImportError:
        return
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    for w in QtWidgets.QApplication.topLevelWidgets():
        if shiboken6.isValid(w):
            w.close()
            shiboken6.delete(w)
    app.processEvents()
