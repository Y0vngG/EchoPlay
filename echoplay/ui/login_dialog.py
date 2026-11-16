from pathlib import Path
import os

from PySide6.QtCore import QLibraryInfo, Qt, QUrl, Signal
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
import PySide6


LOGIN_URL = "https://passport.bilibili.com/login"
WEBENGINE_RESOURCE_FILES = (
    "icudtl.dat",
    "qtwebengine_resources.pak",
    "v8_context_snapshot.bin",
)
SESSION_COOKIE_NAMES = {
    "SESSDATA",
    "bili_jct",
    "DedeUserID",
    "DedeUserID__ckMd5",
    "sid",
}
REQUIRED_SESSION_COOKIES = {"SESSDATA", "bili_jct"}


def prepare_webengine_runtime() -> str | None:
    package_dir = Path(PySide6.__file__).resolve().parent
    library_executables = Path(
        QLibraryInfo.path(QLibraryInfo.LibraryPath.LibraryExecutablesPath)
    )
    data_dir = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.DataPath))
    translations_dir = Path(
        QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    )

    process_candidates = []
    configured_process = os.environ.get("QTWEBENGINEPROCESS_PATH")
    if configured_process:
        process_candidates.append(Path(configured_process))
    process_candidates.extend(
        (
            library_executables / "QtWebEngineProcess.exe",
            package_dir / "QtWebEngineProcess.exe",
        )
    )
    process_path = next((path for path in process_candidates if path.is_file()), None)
    if process_path is None:
        return "找不到 QtWebEngineProcess.exe，无法启动内嵌登录页面。"

    resource_candidates = []
    configured_resources = os.environ.get("QTWEBENGINE_RESOURCES_PATH")
    if configured_resources:
        resource_candidates.append(Path(configured_resources))
    resource_candidates.extend(
        (data_dir / "resources", package_dir / "resources")
    )
    resources_path = next(
        (
            path
            for path in resource_candidates
            if all((path / name).is_file() for name in WEBENGINE_RESOURCE_FILES)
        ),
        None,
    )
    if resources_path is None:
        return "找不到完整的 Qt WebEngine 资源，无法启动内嵌登录页面。"

    locales_candidates = []
    configured_locales = os.environ.get("QTWEBENGINE_LOCALES_PATH")
    if configured_locales:
        locales_candidates.append(Path(configured_locales))
    locales_candidates.extend(
        (
            translations_dir / "qtwebengine_locales",
            package_dir / "translations" / "qtwebengine_locales",
        )
    )
    locales_path = next(
        (path for path in locales_candidates if path.is_dir()), None
    )

    os.environ["QTWEBENGINEPROCESS_PATH"] = str(process_path.resolve())
    os.environ["QTWEBENGINE_RESOURCES_PATH"] = str(resources_path.resolve())
    if locales_path is not None:
        os.environ["QTWEBENGINE_LOCALES_PATH"] = str(locales_path.resolve())
    return None


class BilibiliLoginDialog(QDialog):
    login_succeeded = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("登录哔哩哔哩")
        self.setModal(True)
        self.resize(980, 720)
        self.setMinimumSize(720, 560)
        self._cookies: dict[str, str] = {}
        self._login_emitted = False

        layout = QVBoxLayout(self)
        self.status = QLabel("请在下方登录 B 站账号，成功后会自动保存到 EchoPlay。")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)

        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(
            QWebEngineProfile.HttpCacheType.MemoryHttpCache
        )
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        self.profile.cookieStore().cookieAdded.connect(self._cookie_added)

        self.browser = QWebEngineView(self)
        self.page = QWebEnginePage(self.profile, self.browser)
        self.browser.setPage(self.page)
        self.browser.loadStarted.connect(self._load_started)
        self.browser.loadFinished.connect(self._load_finished)
        layout.addWidget(self.browser, 1)

        buttons = QHBoxLayout()
        self.reload_button = QPushButton("重新加载")
        self.reload_button.clicked.connect(self.browser.reload)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.reload_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        self.browser.load(QUrl(LOGIN_URL))

    def _load_started(self):
        if not self._login_emitted:
            self.status.setText("正在加载 B 站登录页面…")

    def _load_finished(self, succeeded: bool):
        if self._login_emitted:
            return
        if succeeded:
            self.status.setText(
                "请在页面中完成登录，成功后会自动保存登录状态。"
            )
        else:
            self.status.setText("B 站登录页面加载失败，请检查网络后重新加载。")

    def _cookie_added(self, cookie: QNetworkCookie):
        if self._login_emitted:
            return
        domain = cookie.domain().strip(".").lower()
        if domain != "bilibili.com" and not domain.endswith(".bilibili.com"):
            return

        name = bytes(cookie.name()).decode("utf-8")
        if name not in SESSION_COOKIE_NAMES:
            return
        value = bytes(cookie.value()).decode("utf-8")
        if value:
            self._cookies[name] = value
        else:
            self._cookies.pop(name, None)

        if REQUIRED_SESSION_COOKIES.issubset(self._cookies):
            self._login_emitted = True
            self.status.setText("登录成功，正在保存登录状态…")
            self.login_succeeded.emit(dict(self._cookies))
