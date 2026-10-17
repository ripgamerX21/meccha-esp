#!/usr/bin/env python3
"""MECCA CHAMELEON ESP — external overlay entry point."""
import sys
import ctypes

from PyQt5.QtWidgets import QApplication, QMessageBox

from meccha_chameleon_tools.core import MecchaESP
from meccha_chameleon_tools.config import load_config, save_config
from meccha_chameleon_tools.translations import _tr
from meccha_chameleon_tools.ui import Menu, Overlay


def _set_dpi_aware():
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    _set_dpi_aware()
    app = QApplication(sys.argv)

    config = load_config()
    _tr.set_language("EN")

    try:
        esp = MecchaESP()
    except Exception as e:
        QMessageBox.critical(
            None, "Game Not Found",
            "Could not connect to MECCA CHAMELEON.\n\n"
            "Make sure the game is running before launching this tool.\n\n"
            f"Error: {e}"
        )
        sys.exit(1)

    menu = Menu(config, esp, tabs=["ESP", "RADAR"])
    overlay = Overlay(esp, config)
    overlay.show()
    menu.show()

    app.aboutToQuit.connect(lambda: save_config(config))
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
