from pathlib import Path
import faulthandler
import logging
import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
import PySide6

from echoplay.services.app_logging import configure_logging
from echoplay.services.single_instance import SingleInstance, activate_window
from echoplay.storage import LibraryStore
from echoplay.ui.main_window import MainWindow
from echoplay.version import VERSION


_fault_log = None


def main() -> int:
    global _fault_log
    log_file = configure_logging()
    _fault_log = log_file.open("a", encoding="utf-8")
    faulthandler.enable(_fault_log)
    logging.getLogger("echoplay").info("EchoPlay starting")
    sys.excepthook = _log_uncaught_exception
    qt_plugin_path = Path(PySide6.__file__).resolve().parent / "plugins"
    if not qt_plugin_path.is_dir():
        raise RuntimeError(f"PySide6 插件目录不存在：{qt_plugin_path}")
    QCoreApplication.addLibraryPath(str(qt_plugin_path))
    logging.getLogger("echoplay").info("Added Qt plugin path: %s", qt_plugin_path)
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName("EchoPlay")
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("EchoPlay")
    app_icon_path = (
        Path(sys._MEIPASS) / "app_icon.png"
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent / "assets" / "app_icon.png"
    )
    app.setWindowIcon(QIcon(str(app_icon_path)))

    data_dir = Path.home() / "AppData" / "Local" / "EchoPlay"
    instance = SingleInstance(data_dir, app)
    if not instance.start():
        faulthandler.disable()
        _fault_log.close()
        return 0
    app.aboutToQuit.connect(instance.close)
    store = LibraryStore(data_dir / "library.sqlite3")
    window = MainWindow(store)
    window.setWindowIcon(QIcon(str(app_icon_path)))
    window.apply_saved_taskbar_icon()
    instance.activation_requested.connect(lambda: activate_window(window))
    window.show()
    try:
        return app.exec()
    finally:
        instance.close()
        logging.getLogger("echoplay").info("EchoPlay shutting down")
        faulthandler.disable()
        _fault_log.close()


def _log_uncaught_exception(exc_type, exc_value, traceback):
    logging.getLogger("echoplay").critical(
        "Uncaught exception", exc_info=(exc_type, exc_value, traceback)
    )
