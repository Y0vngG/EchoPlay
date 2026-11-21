"""Run update work away from the GUI thread and restart after preparation."""
import logging
from pathlib import Path
import shutil
import sys
from threading import Event

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QApplication

from echoplay.services.updater import check_release, prepare_update, launch_installer
from echoplay.ui.workers import Worker


class UpdateController(QObject):
    progress = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.cancelled = Event()
        self.worker = None
        self.prepared = None
        self.installing = False
        self.progress.connect(self._status)
        QApplication.instance().aboutToQuit.connect(self._stop)

    def start(self):
        # Source checkouts are updated by the developer, never overwritten.
        if not getattr(sys, "frozen", False) or sys.platform != "win32":
            return
        self.worker = Worker(self._prepare)
        self.worker.signals.succeeded.connect(self._ready)
        self.worker.signals.failed.connect(self._failed)
        QThreadPool.globalInstance().start(self.worker)

    def _prepare(self):
        release = check_release()
        if release is None or self.cancelled.is_set():
            return None
        prepared = prepare_update(release, Path(sys.executable), self.progress.emit,
                                  self.cancelled.is_set)
        if self.cancelled.is_set():
            shutil.rmtree(prepared.work)
            return None
        return prepared

    def _status(self, message):
        if not self.cancelled.is_set():
            self.window.statusBar().showMessage(message)

    def _ready(self, prepared):
        self.prepared = prepared
        if prepared is None:
            return
        if self.cancelled.is_set():
            shutil.rmtree(prepared.work)
            self.prepared = None
            return
        self._status(f"EchoPlay {prepared.version} 已就绪，10 秒后自动更新并重启…")
        QTimer.singleShot(10000, self._install)

    def _install(self):
        if self.cancelled.is_set():
            return
        try:
            launch_installer(self.prepared)
        except Exception as exc:
            self._failed(str(exc))
            return
        self.installing = True
        self.window.close()
        QApplication.instance().quit()

    def _failed(self, message):
        logging.getLogger("echoplay.updater").warning("Update skipped: %s", message)
        reason = " ".join(str(message).split())[:180]
        self._status(f"自动更新暂不可用：{reason}；下次启动会重试。")

    def _stop(self):
        self.cancelled.set()
        if self.prepared is not None and not self.installing:
            shutil.rmtree(self.prepared.work, ignore_errors=True)
