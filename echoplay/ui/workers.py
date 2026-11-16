import logging

from PySide6.QtCore import QObject, QRunnable, Signal


class WorkerSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal(object)


class Worker(QRunnable):
    def __init__(self, function, *args, **kwargs):
        super().__init__()
        self.setAutoDelete(False)
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self):
        try:
            logging.getLogger("echoplay.worker").info("Worker started: %s", self.function.__name__)
            result = self.function(*self.args, **self.kwargs)
            logging.getLogger("echoplay.worker").info("Worker finished: %s", self.function.__name__)
            self.signals.succeeded.emit(result)
        except Exception as exc:
            logging.getLogger("echoplay.worker").exception("Worker failed: %s", self.function.__name__)
            self.signals.failed.emit(str(exc))
        finally:
            self.signals.finished.emit(self)
