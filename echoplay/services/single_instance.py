"""Keep one player per user and forward repeated launches to its window."""
import hashlib
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QLockFile, Signal, Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    activation_requested = Signal()

    def __init__(self, data_dir: Path, parent=None):
        super().__init__(parent)
        data_dir.mkdir(parents=True, exist_ok=True)
        identity = str(data_dir.resolve()).casefold().encode("utf-8")
        self.name = "EchoPlay-" + hashlib.sha256(identity).hexdigest()[:24]
        self.lock = QLockFile(str(data_dir / "instance.lock"))
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._activate)

    def start(self):
        """Return True for the owner, or notify the owner and return False."""
        deadline = time.monotonic() + 5
        while True:
            if self.lock.tryLock(0):
                # Only the lock owner may remove a stale endpoint after a crash.
                QLocalServer.removeServer(self.name)
                if not self.server.listen(self.name):
                    self.lock.unlock()
                    raise RuntimeError("无法创建 EchoPlay 单实例通信服务。")
                return True
            if self.lock.error() != QLockFile.LockError.LockFailedError:
                raise RuntimeError("无法创建 EchoPlay 单实例锁。")
            if sys.platform == "win32":
                # The newly launched process can grant the existing player focus.
                import ctypes
                pid, _, _ = self.lock.getLockInfo()
                if pid > 0:
                    ctypes.windll.user32.AllowSetForegroundWindow(int(pid))
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(200):
                socket.disconnectFromServer()
                return False
            if time.monotonic() >= deadline:
                raise RuntimeError("EchoPlay 正在启动或退出，请稍后重试。")
            time.sleep(0.05)

    def _activate(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.close()
            socket.deleteLater()
            self.activation_requested.emit()

    def close(self):
        self.server.close()
        self.lock.unlock()


def activate_window(window):
    if window.isMinimized():
        window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()
