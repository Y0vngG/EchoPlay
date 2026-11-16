from dataclasses import replace
from functools import cmp_to_key
from time import monotonic
import logging
import sqlite3

from PySide6.QtCore import (
    QCollator,
    QStringListModel,
    QIODevice,
    QEvent,
    QLocale,
    QSettings,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMainWindow,
    QFrame,
    QMessageBox,
    QPushButton,
    QSlider,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from echoplay.models import Track
from echoplay.services.auth import clear_session, load_session, save_session
from echoplay.services.bilibili import (
    fetch_account_avatar,
    fetch_account_profile,
    fetch_cover,
    cached_cover,
    fetch_video_from_url,
    fetch_video_pages,
    search_videos,
)
from echoplay.services.playback import (
    AudioStream,
    StreamingAudioDevice,
    resolve_audio_stream,
)
from echoplay.services.prefetch import AudioPrefetch
from echoplay.services.range_audio import RangeAudioDevice
from echoplay.services.stream_quality import STREAM_QUALITIES, DEFAULT_STREAM_QUALITY
from echoplay.storage import LibraryStore
from echoplay.ui.icons import _set_taskbar_large_icon, _asset_path, _make_control_icon
from echoplay.ui.library_helpers import group_library_tracks, item_for_widget
from echoplay.ui.settings import SettingsMixin
from echoplay.ui.theme import ThemeMixin
from echoplay.ui.widgets import (
    ExcelLibraryGridLayout,
    ThemeToggle,
    ExcelSheetStack,
    ExcelSheetRowHeader,
    ExcelRibbonImage,
    SmoothScrollListWidget,
    SmoothScrollArea,
    SettingsDetailLabel,
    MarqueeLabel,
    EasterEggLabel,
    SearchResultRow,
    SearchResultContainer,
    SearchResultDetails,
    RecentSearchPopup,
    AddToLibraryButton,
    ExcelSearchListWidget,
    ExcelPlaylistListWidget,
    ExcelLibraryListWidget,
    ExcelCollectionPageListWidget,
)
from echoplay.ui.workers import Worker


class MainWindow(SettingsMixin, ThemeMixin, QMainWindow):
    _audio_stream_ready = Signal(object, object)
    _audio_stream_failed = Signal(str, str)
    _audio_device_ready = Signal(object, object, object)
    _audio_download_complete = Signal(str, int, object)
    _audio_download_failed = Signal(str, str, object)
    _audio_download_progress = Signal(str, int, int)
    SHORTCUT_DEFAULTS = {
        "previous": "Ctrl+Alt+Left",
        "next": "Ctrl+Alt+Right",
        "play_pause": "Ctrl+Alt+Space",
        "minimize": "Ctrl+Alt+M",
    }
    SHORTCUT_LABELS = {
        "previous": "上一首",
        "next": "下一首",
        "play_pause": "播放/暂停",
        "minimize": "最小化界面",
    }
    TASKBAR_ICON_PRESETS = (
        ("current", "EchoPlay", "echoplay.png"),
        ("excel", "Excel", "microsoft_office_excel_logo_icon.png"),
        ("word", "Word", "microsoft_office_word_logo_icon.png"),
        ("wps_spreadsheet", "WPS 表格", "wpsofficeetmain.png"),
        ("wps_writer", "WPS 文字", "wpsofficewpsmain.png"),
    )

    def __init__(self, store: LibraryStore):
        super().__init__()
        self.store = store
        self._audio_stream_ready.connect(
            self._on_audio_stream_ready, Qt.ConnectionType.QueuedConnection
        )
        self._audio_stream_failed.connect(
            self._on_audio_stream_failed, Qt.ConnectionType.QueuedConnection
        )
        self._audio_device_ready.connect(
            self._on_audio_device_ready, Qt.ConnectionType.QueuedConnection
        )
        self._audio_download_complete.connect(
            self._on_audio_download_complete, Qt.ConnectionType.QueuedConnection
        )
        self._audio_download_failed.connect(
            self._on_audio_download_failed, Qt.ConnectionType.QueuedConnection
        )
        self._audio_download_progress.connect(
            self._on_audio_download_progress, Qt.ConnectionType.QueuedConnection
        )
        self.bilibili_cookies = load_session()
        self.pool = QThreadPool.globalInstance()
        self.search_pool = QThreadPool(self)
        self.search_pool.setMaxThreadCount(2)
        self.cover_pool = QThreadPool(self)
        self.cover_pool.setMaxThreadCount(6)
        self.metadata_pool = QThreadPool(self)
        self.metadata_pool.setMaxThreadCount(2)
        self._result_epoch = 0
        self._cover_requests = set()
        self._workers: set[Worker] = set()
        self._next_audio_prefetch = None
        self._preloaded_audio = None
        self.cover_labels: dict[str, list[QLabel]] = {}
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.70)
        self.media_devices = QMediaDevices(self)
        self.media_devices.audioOutputsChanged.connect(self._on_audio_outputs_changed)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.positionChanged.connect(self._on_position_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.media_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.media_player.bufferProgressChanged.connect(self._on_media_buffer_progress)
        self.media_player.errorOccurred.connect(self._on_media_error)
        self._playback_start_timer = QTimer(self)
        self._playback_start_timer.setSingleShot(True)
        self._playback_start_timer.setInterval(20000)
        self._playback_start_timer.timeout.connect(self._on_playback_start_timeout)
        self.audio_buffer: StreamingAudioDevice | RangeAudioDevice | None = None
        self._retired_audio_buffers: list[StreamingAudioDevice | RangeAudioDevice] = []
        self.current_stream_description = ""
        self.current_track_title = ""
        self.current_track: Track | None = None
        self.playlist: list[Track] = []
        self._seeking = False
        self._pending_seek_position: int | None = None
        self._resume_after_download = False
        self.pending_play_id: str | None = None
        self.active_play_id: str | None = None
        self.library_row_items: dict[int, QListWidgetItem] = {}
        self.library_page_panels: dict[int, QWidget] = {}
        self.library_page_lists: dict[int, QListWidget] = {}
        self.library_tracks_by_item: dict[int, list[Track]] = {}
        self.library_title_labels: dict[int, QLabel] = {}
        self.library_author_labels: dict[int, QLabel] = {}
        self.library_track_inner_layouts: dict[
            int, tuple[QVBoxLayout, QHBoxLayout]
        ] = {}
        self.library_track_inner_layout_defaults: dict[
            int, tuple[tuple[int, int, int, int, int], ...]
        ] = {}
        self.library_row_layout_spacings: dict[int, int] = {}
        self.library_play_indicators: dict[int, QFrame] = {}
        self.library_collection_badges: dict[int, QLabel] = {}
        self.selected_library_playlist_id: int | None = None
        self.hovered_library_row: QFrame | None = None
        empty_page_pixmap = QPixmap(16, 16)
        empty_page_pixmap.fill(Qt.GlobalColor.transparent)
        self.empty_page_icon = QIcon(empty_page_pixmap)
        self.library_pages_by_bvid: dict[str, list[Track]] = {}
        self.library_pages_pending: set[str] = set()
        self.search_result_row_items: dict[int, QListWidgetItem] = {}
        self.search_result_play_indicators: dict[int, QFrame] = {}
        self.search_result_collection_badges: dict[int, QLabel] = {}
        self.search_result_containers: dict[int, QWidget] = {}
        self.search_result_page_panels: dict[int, QWidget] = {}
        self.search_result_page_lists: dict[int, QListWidget] = {}
        self.search_result_pages: dict[int, list[Track]] = {}
        self.search_result_pages_pending: set[int] = set()
        self.search_result_pages_queue: list[QListWidgetItem] = []
        self.search_result_pages_active: set[int] = set()
        self.search_keyword: str | None = None
        self.search_generation = 0
        self.next_search_page = 1
        self.search_loading = False
        self.search_has_more = False
        self.search_result_bvids: set[str] = set()
        self.pending_search_result_click: QListWidgetItem | None = None
        self.search_result_click_timer = QTimer(self)
        self.search_result_click_timer.setSingleShot(True)
        self.search_result_click_timer.setInterval(
            QGuiApplication.styleHints().mouseDoubleClickInterval()
        )
        self.search_result_click_timer.timeout.connect(
            self._process_pending_search_result_click
        )
        self.pending_library_click: QListWidgetItem | None = None
        self.library_click_timer = QTimer(self)
        self.library_click_timer.setSingleShot(True)
        self.library_click_timer.setInterval(self.search_result_click_timer.interval())
        self.library_click_timer.timeout.connect(self._process_pending_library_click)
        self.login_dialog: BilibiliLoginDialog | None = None
        self.account_nickname: str | None = None
        self.account_is_vip = False
        self._account_avatar_ready = False
        self.setWindowTitle("EchoPlay powered by Young G")
        self.resize(980, 700)
        self.setMinimumSize(800, 550)
        self.settings = QSettings()
        saved_quality = self.settings.value("playback/stream_quality", DEFAULT_STREAM_QUALITY, type=str)
        self._preferred_stream_quality = (
            saved_quality if saved_quality in STREAM_QUALITIES else DEFAULT_STREAM_QUALITY
        )
        self.stream_quality = DEFAULT_STREAM_QUALITY
        self._taskbar_icon_source = self.settings.value(
            "appearance/taskbar_icon_source", "current", type=str
        )
        self._custom_taskbar_icon_path = self.settings.value(
            "appearance/custom_taskbar_icon_path", "", type=str
        )
        if self._taskbar_icon_source == "wps":
            self._taskbar_icon_source = "wps_writer"
        if self._taskbar_icon_source not in {
            "current",
            "excel",
            "word",
            "wps_spreadsheet",
            "wps_writer",
            "custom",
        }:
            self._taskbar_icon_source = "current"
        self._taskbar_icon_handle: int | None = None
        self._taskbar_shell_identity = None
        self._taskbar_shell_hwnd = None
        self._taskbar_icon_refresh_timer = QTimer(self)
        self._taskbar_icon_refresh_timer.setSingleShot(True)
        self._taskbar_icon_refresh_timer.setInterval(0)
        self._taskbar_icon_refresh_timer.timeout.connect(self._refresh_taskbar_icon)
        self.windowIconChanged.connect(lambda _icon: self._taskbar_icon_refresh_timer.start())
        saved_theme = self.settings.value("appearance/theme", "light", type=str)
        self.theme = saved_theme if saved_theme in ("light", "dark") else "light"
        saved_skin = self.settings.value("appearance/skin", "default", type=str)
        self.skin = saved_skin if saved_skin in ("default", "excel") else "default"
        self.library_notification = QLabel(self)
        self.library_notification.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.library_notification.setContentsMargins(16, 9, 16, 9)
        self.library_notification.setObjectName("libraryNotification")
        self.library_notification.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self.library_notification.hide()
        self.library_notification_timer = QTimer(self)
        self.library_notification_timer.setSingleShot(True)
        self.library_notification_timer.setInterval(2500)
        self.library_notification_timer.timeout.connect(
            self.library_notification.hide
        )

        root = QWidget()
        root.setObjectName("mainRoot")
        layout = QVBoxLayout(root)
        self.root_layout = layout
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(14)
        self.excel_ribbon = self._make_excel_ribbon()
        self.excel_ribbon.setParent(root)
        self.excel_ribbon.setVisible(self.skin == "excel")
        layout.addWidget(self.excel_ribbon)

        self.settings_account_label = SettingsDetailLabel(parent=root)
        self.account_avatar = QLabel(root)
        self.account_avatar.setObjectName("accountAvatar")
        self.account_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.account_avatar.setFixedSize(40, 40)
        self.login_button = QPushButton(root)
        self.login_button.clicked.connect(self.open_login_dialog)
        self.logout_button = QPushButton("退出登录", root)
        self.logout_button.clicked.connect(self.logout_bilibili)
        self.theme_toggle = ThemeToggle(self.theme, root)
        self.theme_toggle.themeChanged.connect(self._set_theme)

        self.default_header = QWidget(root)
        self.default_header.setFixedHeight(24)
        header = QHBoxLayout(self.default_header)
        header.setContentsMargins(9, 0, 9, 0)
        header.setSpacing(12)
        title = EasterEggLabel(
            "EchoPlay",
            "我Chovy！你给我听好的啊！！！！！",
            10,
        )
        title.setObjectName("brandTitle")
        brand_subtitle = QLabel("让喜欢的音乐，随时播放")
        brand_subtitle.setObjectName("brandSubtitle")
        header.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        header.addWidget(
            brand_subtitle,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._update_account_status()
        layout.addWidget(self.default_header)

        self.excel_column_header_row = self._make_excel_column_header_row()
        self.excel_column_header_row.setParent(root)
        self.excel_column_header_row.setVisible(self.skin == "excel")
        layout.addWidget(self.excel_column_header_row)

        self.search_tab = self._make_search_tab()
        self.library_tab = self._make_library_tab()
        self.settings_tab = self._make_settings_tab()
        self.page_stack = ExcelSheetStack()
        self.page_stack.setObjectName("pageStack")
        for page in (self.search_tab, self.library_tab, self.settings_tab):
            self.page_stack.addWidget(page)

        navigation = QGridLayout()
        navigation.setSpacing(10)
        navigation.setContentsMargins(0, 0, 0, 0)
        self.navigation_layout = navigation
        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        self.search_nav_button = QPushButton()
        self.library_nav_button = QPushButton()
        self.settings_nav_button = QPushButton()
        for button, label in (
            (self.search_nav_button, "查找"),
            (self.library_nav_button, "曲库"),
            (self.settings_nav_button, "设置"),
        ):
            button.setToolTip(label)
            button.setAccessibleName(label)
        for index, button in enumerate(
            (
                self.search_nav_button,
                self.library_nav_button,
                self.settings_nav_button,
            )
        ):
            button.setObjectName("navigationTab")
            button.setCheckable(True)
            button.setMinimumHeight(44)
            button.setIconSize(QSize(22, 22))
            button.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
            )
            button.toggled.connect(self._refresh_navigation_icons)
            self.navigation_group.addButton(button, index)
            navigation.addWidget(button, 0, index * 4, 1, 4)
        for column in range(12):
            navigation.setColumnStretch(column, 1)
        self.navigation_group.idClicked.connect(self.page_stack.setCurrentIndex)
        self.library_nav_button.setChecked(True)
        self.page_stack.setCurrentWidget(self.library_tab)

        self.excel_title_row = self._make_excel_title_row()
        self.excel_title_row.setParent(root)
        self.excel_title_row.setVisible(self.skin == "excel")
        self.excel_navigation_row_number = self._make_excel_row_number("2")
        self.excel_navigation_row_number.setParent(root)
        self.excel_navigation_row_number.setFixedHeight(
            ExcelSheetStack.cell_height
        )
        self.excel_navigation_row_number.setVisible(self.skin == "excel")
        navigation_row = QWidget()
        navigation_row.setObjectName("excelNavigationRow")
        navigation_row_layout = QHBoxLayout(navigation_row)
        navigation_row_layout.setContentsMargins(0, 0, 0, 0)
        navigation_row_layout.setSpacing(0)
        navigation_row_layout.addWidget(self.excel_navigation_row_number)
        navigation_row_layout.addLayout(navigation, 1)

        sheet_content = QWidget()
        sheet_content_layout = QHBoxLayout(sheet_content)
        sheet_content_layout.setContentsMargins(0, 0, 0, 0)
        sheet_content_layout.setSpacing(0)
        self.excel_row_header = self._make_excel_row_header()
        self.excel_row_header.setParent(sheet_content)
        self.excel_row_header.setVisible(self.skin == "excel")
        sheet_content_layout.addWidget(self.excel_row_header)
        sheet_content_layout.addWidget(self.page_stack, 1)
        self.excel_sheet_content = sheet_content

        self.excel_content_scroll_area = SmoothScrollArea()
        self.excel_content_scroll_area.setObjectName("excelContentScrollArea")
        self.excel_content_scroll_area.setWidgetResizable(True)
        self.excel_content_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.excel_content_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.excel_content_scroll_area.setWidget(sheet_content)
        self.page_stack.currentChanged.connect(
            self._update_excel_sheet_content_minimum_height
        )
        self.page_stack.currentChanged.connect(self._sync_search_sheet_scroll)

        self.excel_sheet_area = QWidget()
        self.excel_sheet_area.setObjectName("excelSheetArea")
        self.excel_sheet_layout = QVBoxLayout(self.excel_sheet_area)
        self.excel_sheet_layout.setContentsMargins(0, 0, 0, 0)
        self.excel_sheet_layout.setSpacing(10)
        self.excel_sheet_layout.addWidget(self.excel_title_row)
        self.excel_sheet_layout.addWidget(navigation_row)
        self.excel_sheet_layout.addWidget(self.excel_content_scroll_area, 1)
        layout.addWidget(self.excel_sheet_area, 1)
        layout.addWidget(self._make_playback_bar())
        self.setCentralWidget(root)
        self._setup_shortcuts()
        self._apply_theme()
        self.media_player.seekableChanged.connect(self.progress_slider.setEnabled)
        self.progress_slider.setEnabled(False)
        self.refresh_library()
        if self.bilibili_cookies.get("SESSDATA"):
            self._fetch_account_profile()

    def _set_theme(self, theme: str):
        self.theme = theme
        self.settings.setValue("appearance/theme", theme)
        self._apply_theme()

    def _set_skin(self, skin: str):
        if skin not in ("default", "excel"):
            raise ValueError(f"Unknown skin: {skin}")
        self.skin = skin
        self.settings.setValue("appearance/skin", skin)
        for option, button in self.skin_buttons.items():
            button.setChecked(option == skin)
        self._apply_theme()

    def _update_excel_sheet_content_minimum_height(self, *_):
        if self.skin == "excel":
            minimum_height = max(
                self.page_stack.minimumHeight(),
                self.page_stack.minimumSizeHint().height(),
            )
        else:
            minimum_height = 0
        self.excel_sheet_content.setMinimumHeight(minimum_height)

    def _theme_colors(self):
        dark = self.theme == "dark"
        if self.skin == "excel":
            return {
                "window": "#202020" if dark else "#f3f3f3",
                "panel": "#292929" if dark else "#ffffff",
                "foreground": "#f0f0f0" if dark else "#242424",
                "muted": "#b0b0b0" if dark else "#666666",
                "border": "#505050" if dark else "#c6c6c6",
                "accent": "#21a366" if dark else "#217346",
                "hover": "#333333" if dark else "#e2f0d9",
                "cover": "#333333" if dark else "#e7e6e6",
                "badge": "#333333" if dark else "#e2f0d9",
                "progress_track": "#505050" if dark else "#d9e2d3",
                "progress_unplayed": "#3c3c3c" if dark else "#e7e6e6",
                "scroll_track": "#303632" if dark else "#e8ede9",
                "scroll_handle": "#879c8e" if dark else "#637f6e",
                "scroll_hover": "#6eb58d" if dark else "#347a51",
                "scroll_pressed": "#91cea9" if dark else "#215c3a",
            }
        return {
            "window": "#171a21" if dark else "#f5f6f8",
            "panel": "#222733" if dark else "#ffffff",
            "foreground": "#e7eaf0" if dark else "#20242b",
            "muted": "#a0a8b7" if dark else "#69717d",
            "border": "#3b4352" if dark else "#d8dce4",
            "accent": "#586fe8" if dark else "#3978d4",
            "hover": "rgba(255, 255, 255, 18)" if dark else "rgba(30, 45, 70, 18)",
            "cover": "#303745" if dark else "#e9edf3",
            "badge": "#39445a" if dark else "#e8edf7",
            "progress_track": "#303846" if dark else "#c5ccd6",
            "progress_unplayed": "#374151" if dark else "#d5dbe4",
            "scroll_track": "#252d3a" if dark else "#e8edf3",
            "scroll_handle": "#586fe8" if dark else "#3978d4",
            "scroll_hover": "#7488f0" if dark else "#2f68bc",
            "scroll_pressed": "#91a0f5" if dark else "#25569f",
        }

    def _refresh_navigation_icons(self, *_):
        colors = self._theme_colors()
        muted = QColor(colors["muted"])
        accent = QColor(colors["accent"])
        for button, name in (
            (self.search_nav_button, "search"),
            (self.library_nav_button, "library"),
            (self.settings_nav_button, "settings"),
        ):
            color = accent if button.isChecked() else muted
            button.setIcon(_make_control_icon(name, color))

    def _refresh_play_pause_icon(self):
        is_playing = self.play_pause_action == "pause"
        color = QColor(self._theme_colors()["foreground"])
        action_label = (
            "暂停"
            if is_playing
            else "继续"
            if self.active_play_id is not None
            else "播放"
        )
        self.play_pause_button.setIcon(
            _make_control_icon(self.play_pause_action, color)
        )
        self.play_pause_button.setToolTip(action_label)
        self.play_pause_button.setAccessibleName(action_label)

    def _refresh_track_control_icons(self):
        color = QColor(self._theme_colors()["foreground"])
        self.previous_button.setIcon(_make_control_icon("previous", color))
        self.next_button.setIcon(_make_control_icon("next", color))

    def _set_play_pause_action(self, action: str):
        if action not in ("play", "pause"):
            raise ValueError(f"Unknown playback control action: {action}")
        self.play_pause_action = action
        self._refresh_play_pause_icon()

    def _refresh_volume_icon(self):
        volume = self.volume_slider.value()
        icon_name = (
            "volume_mute"
            if volume == 0
            else "volume_low"
            if volume <= 33
            else "volume_medium"
            if volume <= 66
            else "volume_high"
        )
        color = QColor(self._theme_colors()["muted"])
        self.volume_button.setIcon(_make_control_icon(icon_name, color))
        if volume == 0:
            description = "已静音，点击恢复音量"
        else:
            description = f"音量 {volume}%，点击静音"
        self.volume_button.setToolTip(description)
        self.volume_button.setAccessibleName(description)

    def _refresh_playback_order_icons(self):
        colors = self._theme_colors()
        muted = QColor(colors["muted"])
        accent = QColor(colors["accent"])
        for mode, icon_name in self.playback_order_options:
            button = self.playback_order_buttons[mode]
            button.setIcon(
                _make_control_icon(
                    icon_name,
                    accent if mode == self.playback_order else muted,
                )
            )
            button.setChecked(mode == self.playback_order)

    def _set_playback_order_by_id(self, order_id: int):
        if not 0 <= order_id < len(self.playback_order_options):
            raise ValueError(f"Unknown playback order id: {order_id}")
        self._set_playback_order(self.playback_order_options[order_id][0])

    def _set_playback_order(self, mode: str):
        if mode not in self.playback_order_icons:
            raise ValueError(f"Unknown playback order: {mode}")
        self.playback_order = mode
        self._refresh_playback_order_icons()
        self.settings.setValue("playback/order", mode)
        self.settings.sync()

    def _selected_playback_order(self) -> str:
        order_id = self.playback_order_group.checkedId()
        if order_id < 0:
            return "列表循环"
        return self.playback_order_options[order_id][0]

    def toggle_mute(self):
        if self.volume_slider.value() > 0:
            self._last_nonzero_volume = self.volume_slider.value()
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(
                max(1, min(100, self._last_nonzero_volume))
            )


    def _make_excel_ribbon(self) -> ExcelRibbonImage:
        return ExcelRibbonImage(
            _asset_path("assets", "excel_ribbon.png"),
            _asset_path("assets", "excel_ribbon_dark.png"),
        )

    @staticmethod
    def _make_excel_column_header_row() -> QWidget:
        row = QWidget()
        row.setObjectName("excelColumnHeaderRow")
        grid = QGridLayout(row)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        corner = QLabel()
        corner.setObjectName("excelRowHeader")
        corner.setFixedWidth(28)
        grid.addWidget(corner, 0, 0)
        for column in range(12):
            label = QLabel(chr(ord("A") + column))
            label.setObjectName("excelColumnHeader")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setFixedHeight(18)
            grid.addWidget(label, 0, column + 1)
            grid.setColumnStretch(column + 1, 1)
        return row

    @classmethod
    def _make_excel_title_row(cls) -> QWidget:
        row = QWidget()
        row.setObjectName("excelTitleRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        row_number = cls._make_excel_row_number("1")
        row_number.setFixedHeight(ExcelSheetStack.cell_height)
        layout.addWidget(row_number)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        title = QLabel("EchoPlay")
        title.setObjectName("excelTitleCell")
        title.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        title.setContentsMargins(2, 0, 0, 0)
        title.setFixedHeight(ExcelSheetStack.cell_height)
        title.setMinimumWidth(0)
        title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        grid.addWidget(title, 0, 0)
        for column in range(1, 12):
            blank_cell = QLabel()
            blank_cell.setObjectName("excelBlankCell")
            blank_cell.setFixedHeight(ExcelSheetStack.cell_height)
            grid.addWidget(blank_cell, 0, column)
        for column in range(12):
            grid.setColumnStretch(column, 1)
        layout.addLayout(grid, 1)
        return row

    @staticmethod
    def _make_excel_row_number(number: str) -> QLabel:
        label = QLabel(number)
        label.setObjectName("excelRowNumber")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedWidth(28)
        label.setFixedHeight(36)
        return label

    @staticmethod
    def _make_excel_row_header() -> ExcelSheetRowHeader:
        return ExcelSheetRowHeader()

    def _make_search_tab(self) -> QWidget:
        page = QStackedWidget()
        page.setObjectName("searchPage")
        self.search_default_view = QWidget()
        self.search_default_view.setObjectName("searchDefaultView")
        layout = QVBoxLayout(self.search_default_view)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)
        title = QLabel("发现")
        title.setObjectName("pageTitle")
        subtitle = QLabel("搜索 B 站音源，找到喜欢的内容后加入曲库。")
        subtitle.setObjectName("pageSubtitle")
        heading = QHBoxLayout()
        heading.addWidget(title, 0, Qt.AlignmentFlag.AlignBottom)
        heading.addStretch(1)
        heading.addWidget(
            subtitle, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom
        )
        layout.addLayout(heading)

        self.search_controls = QWidget()
        self.search_controls_layout = QHBoxLayout(self.search_controls)
        self.search_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.search_controls_layout.setSpacing(10)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("搜索关键词，或粘贴 B 站视频链接")
        self.search_input.returnPressed.connect(self.search)
        self.search_history_model = QStringListModel(self)
        self.search_history_popup = RecentSearchPopup(self.search_input, self)
        self.search_history_popup.setModel(self.search_history_model)
        self.search_history_popup.keywordSelected.connect(self._search_recent_keyword)
        self.search_history_popup.keywordDeleteRequested.connect(self._delete_recent_keyword)
        self.search_input.setCursor(Qt.CursorShape.IBeamCursor)
        self.search_input.installEventFilter(self)
        self.search_history_model.setStringList(self.store.list_search_history())
        self.search_button = QPushButton()
        self.search_button.setToolTip('\u67e5\u627e')
        self.search_button.setAccessibleName('\u67e5\u627e')
        self.search_button.setObjectName("searchButton")
        self.search_button.clicked.connect(self.search)
        self.history_button = QPushButton()
        self.history_button.setToolTip('\u5386\u53f2\u8bb0\u5f55')
        self.history_button.setAccessibleName('\u5386\u53f2\u8bb0\u5f55')
        self.history_button.setObjectName("searchButton")
        self.history_button.clicked.connect(self.show_playback_history)
        self._history_recorded_play_id = None
        self._record_current_play_in_history = False
        self.search_controls_layout.addWidget(self.search_input, 1)
        self.search_controls_layout.addWidget(self.history_button)
        self.search_controls_layout.addWidget(self.search_button)
        layout.addWidget(self.search_controls)

        self.search_status = QLabel("输入关键词开始查找。建议登录 B 站账号。")
        self.search_status.setObjectName("excelSearchStatusCell")
        layout.addWidget(self.search_status)

        self.results = ExcelSearchListWidget()
        self.results.setObjectName("searchResults")
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.results.itemDoubleClicked.connect(self.play_search_result)
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self.show_search_result_context_menu)
        self.results.verticalScrollBar().valueChanged.connect(self._sync_search_sheet_scroll)
        self.results.verticalScrollBar().valueChanged.connect(
            self._maybe_load_next_search_page
        )
        layout.addWidget(self.results, 1)

        self.search_default_layout = layout
        page.addWidget(self.search_default_view)

        self.excel_search_view = QWidget()
        self.excel_search_view.setObjectName("excelSearchView")
        self.excel_search_layout = QGridLayout(self.excel_search_view)
        self.excel_search_layout.setContentsMargins(0, 0, 0, 0)
        self.excel_search_layout.setSpacing(0)
        title_cell = QLabel("发现")
        title_cell.setObjectName("excelSearchTitleCell")
        title_cell.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        title_cell.setContentsMargins(4, 0, 0, 0)
        title_cell.setFixedHeight(ExcelSheetStack.cell_height)
        self.excel_search_layout.addWidget(title_cell, 0, 0)
        for column in range(1, 12):
            blank_cell = QLabel()
            blank_cell.setObjectName("excelSearchBlankCell")
            blank_cell.setFixedHeight(ExcelSheetStack.cell_height)
            self.excel_search_layout.addWidget(blank_cell, 0, column)
        self.excel_search_layout.addWidget(
            self.search_status, 2, 0, 1, 12
        )
        self.excel_search_layout.addWidget(self.results, 3, 0, 1, 12)
        for column in range(12):
            self.excel_search_layout.setColumnStretch(column, 1)
        self.excel_search_layout.setRowStretch(3, 1)
        page.addWidget(self.excel_search_view)
        return page

    def _set_excel_search_page(self, excel: bool):
        target_view = (
            self.excel_search_view if excel else self.search_default_view
        )
        self.search_controls_layout.removeWidget(self.search_input)
        self.search_controls_layout.removeWidget(self.history_button)
        self.search_controls_layout.removeWidget(self.search_button)
        self.search_default_layout.removeWidget(self.search_status)
        self.search_default_layout.removeWidget(self.results)
        self.excel_search_layout.removeWidget(self.search_input)
        self.excel_search_layout.removeWidget(self.history_button)
        self.excel_search_layout.removeWidget(self.search_button)
        self.excel_search_layout.removeWidget(self.search_status)
        self.excel_search_layout.removeWidget(self.results)
        if excel:
            for widget in (
                self.search_input,
                self.search_button,
                self.history_button,
                self.search_status,
                self.results,
            ):
                widget.setParent(self.excel_search_view)
            self.excel_search_layout.addWidget(self.search_input, 1, 0, 1, 10)
            self.excel_search_layout.addWidget(self.history_button, 1, 10)
            self.excel_search_layout.addWidget(self.search_button, 1, 11)
            self.excel_search_layout.addWidget(
                self.search_status, 2, 0, 1, 12
            )
            self.excel_search_layout.addWidget(
                self.results, 3, 0, 1, 12
            )
        else:
            self.search_input.setParent(self.search_controls)
            self.history_button.setParent(self.search_controls)
            self.search_button.setParent(self.search_controls)
            self.search_status.setParent(self.search_default_view)
            self.results.setParent(self.search_default_view)
            self.search_controls_layout.addWidget(self.search_input, 1)
            self.search_controls_layout.addWidget(self.history_button)
            self.search_controls_layout.addWidget(self.search_button)
            self.search_default_layout.addWidget(self.search_status)
            self.search_default_layout.addWidget(self.results, 1)
        self.search_tab.setCurrentWidget(target_view)
        self.search_status.setVisible(not excel or not self.search_result_row_items)
        for item in self.search_result_row_items.values():
            container = self.search_result_containers[id(item)]
            row = container.findChild(SearchResultRow)
            row.set_excel(excel)
            container.set_excel(excel, self._theme_colors()["border"])
            container.layout().setSpacing(0 if excel else 6)
            pages = self.search_result_pages.get(id(item))
            if pages is not None:
                self._populate_search_result_pages(item, pages)

    def _sync_search_sheet_scroll(self, *_):
        if self.skin == "excel" and hasattr(self, "history_button"):
            width = max(1, self.excel_search_view.width() // 12)
            self.history_button.setFixedWidth(width)
            self.search_button.setFixedWidth(width)
        if not hasattr(self, "excel_row_header"):
            return
        active = self.skin == "excel" and self.page_stack.currentWidget() is self.search_tab
        library_active = self.skin == "excel" and self.page_stack.currentWidget() is self.library_tab
        offset = self.results.verticalScrollBar().value() if active else (
            self.library_items.verticalScrollBar().value() if library_active else 0
        )
        self.excel_row_header.setProperty("searchScrollOffset", offset)
        # These leading rows are fixed-height cells. Using their known height
        # also avoids stale geometry while a skin change is being laid out.
        top = (2 + int(not self.search_status.isHidden())) * ExcelSheetStack.cell_height if active else 0
        if library_active:
            top = 2 * ExcelSheetStack.cell_height
        self.excel_row_header.setProperty("searchFixedHeight", max(0, top))
        self.excel_row_header.update()

    def show_search_result_context_menu(self, position):
        item = self.results.itemAt(position)
        track = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(track, Track):
            return
        menu = QMenu(self)
        play = menu.addAction("播放")
        add = menu.addAction("加入曲库")
        pages = menu.addAction("展开 / 收起分 P")
        action = menu.exec(self.results.viewport().mapToGlobal(position))
        if action == play:
            self.play_search_result(item)
        elif action == add:
            self.add_to_library(track)
        elif action == pages:
            self.toggle_search_result_pages(item)

    def _set_excel_library_page(self, excel: bool):
        target_view = (
            self.library_excel_view if excel else self.library_default_view
        )
        if self.library_tab.currentWidget() is target_view:
            return

        self.library_playlist_layout.removeWidget(self.library_playlist_header)
        self.library_playlist_layout.removeWidget(self.library_playlist_list)
        self.library_content_controls.removeWidget(self.library_filter)
        self.library_content_layout.removeWidget(self.library_items)
        self.library_excel_layout.removeWidget(self.library_playlist_header)
        self.library_excel_layout.removeWidget(self.library_filter)
        self.library_excel_layout.removeWidget(self.library_playlist_list)
        self.library_excel_layout.removeWidget(self.library_items)
        self.library_excel_layout.removeWidget(self.add_playlist_button)
        self.library_playlist_header_layout.removeWidget(self.add_playlist_button)

        if excel:
            for widget in (
                self.library_playlist_header,
                self.add_playlist_button,
                self.library_filter,
                self.library_playlist_list,
                self.library_items,
            ):
                widget.setParent(self.library_excel_view)
            self.library_excel_layout.addWidget(
                self.library_playlist_header, 1, 0
            )
            # Share the actual sheet column, avoiding independent nested-grid rounding.
            self.library_excel_layout.addWidget(self.add_playlist_button, 1, 1)
            self.library_excel_layout.addWidget(
                self.library_filter, 1, 2, 1, 8
            )
            self.library_excel_layout.addWidget(
                self.library_playlist_list, 2, 0, 1, 2
            )
            self.library_excel_layout.addWidget(
                self.library_items, 2, 2, 1, 8
            )
        else:
            self.add_playlist_button.setParent(self.library_playlist_header)
            self.library_playlist_header_layout.addWidget(
                self.add_playlist_button, 0, 1
            )
            self.library_playlist_header.setParent(
                self.library_playlist_sidebar
            )
            self.library_filter.setParent(self.library_default_content)
            self.library_playlist_list.setParent(
                self.library_playlist_sidebar
            )
            self.library_items.setParent(self.library_default_content)
            self.library_playlist_layout.insertWidget(
                0, self.library_playlist_header
            )
            self.library_playlist_layout.addWidget(
                self.library_playlist_list, 1
            )
            self.library_content_controls.addWidget(self.library_filter, 1)
            self.library_content_layout.insertWidget(
                1, self.library_items, 1
            )
        self.library_tab.setCurrentWidget(target_view)

    def _make_library_tab(self) -> QWidget:
        page = QStackedWidget()
        page.setObjectName("libraryPage")
        self.library_default_view = QWidget()
        self.library_default_view.setObjectName("libraryDefaultView")
        layout = QVBoxLayout(self.library_default_view)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)
        title = QLabel("曲库")
        title.setObjectName("pageTitle")
        subtitle = QLabel("收藏在这里，随时回到喜欢的音乐。")
        subtitle.setObjectName("pageSubtitle")
        heading = QHBoxLayout()
        heading.addWidget(title, 0, Qt.AlignmentFlag.AlignBottom)
        heading.addStretch(1)
        heading.addWidget(
            subtitle, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom
        )
        layout.addLayout(heading)

        playlist_sidebar = QWidget()
        playlist_sidebar.setMinimumWidth(155)
        playlist_sidebar.setMaximumWidth(230)
        playlist_layout = QVBoxLayout(playlist_sidebar)
        playlist_layout.setContentsMargins(0, 0, 8, 0)
        playlist_layout.setSpacing(8)
        self.library_playlist_header = QWidget()
        self.library_playlist_header.setObjectName(
            "libraryPlaylistHeader"
        )
        playlist_heading = QGridLayout(self.library_playlist_header)
        playlist_heading.setContentsMargins(0, 0, 0, 0)
        playlist_heading.setSpacing(0)
        playlist_title = QLabel("歌单")
        playlist_title.setObjectName("settingsSectionTitle")
        self.library_playlist_title = playlist_title
        self.add_playlist_button = QToolButton()
        self.add_playlist_button.setObjectName("playlistAddButton")
        self.add_playlist_button.setText("+")
        self.add_playlist_button.setFixedSize(34, 34)
        self.add_playlist_button.setToolTip("新建歌单")
        self.add_playlist_button.setAccessibleName("新建歌单")
        self.add_playlist_button.clicked.connect(self._create_library_playlist)
        playlist_heading.addWidget(playlist_title, 0, 0)
        playlist_heading.addWidget(self.add_playlist_button, 0, 1)
        playlist_heading.setColumnStretch(0, 1)
        playlist_heading.setColumnStretch(1, 0)
        self.library_playlist_header_layout = playlist_heading
        playlist_layout.addWidget(self.library_playlist_header)
        self.library_playlist_layout = playlist_layout
        self.library_playlist_sidebar = playlist_sidebar
        self.library_playlist_list = ExcelPlaylistListWidget()
        self.library_playlist_list.setObjectName("libraryPlaylistList")
        self.library_playlist_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.library_playlist_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.library_playlist_list.itemSelectionChanged.connect(
            self._on_library_playlist_selected
        )
        self.library_playlist_list.customContextMenuRequested.connect(
            self.show_library_playlist_context_menu
        )
        playlist_layout.addWidget(self.library_playlist_list, 1)

        library_content = QWidget()
        library_content_layout = QVBoxLayout(library_content)
        library_content_layout.setContentsMargins(0, 0, 0, 0)
        library_content_layout.setSpacing(8)
        controls = QHBoxLayout()
        self.library_filter = QLineEdit()
        self.library_filter.setObjectName("libraryFilter")
        self.library_filter.setPlaceholderText("筛选当前歌单")
        self._library_filter_timer = QTimer(self)
        self._library_filter_timer.setSingleShot(True)
        self._library_filter_timer.setInterval(180)
        self._library_filter_timer.timeout.connect(self.refresh_library)
        self.library_filter.textChanged.connect(lambda _text: self._library_filter_timer.start())
        controls.addWidget(self.library_filter, 1)
        library_content_layout.addLayout(controls)
        self.library_content_controls = controls
        self.library_content_layout = library_content_layout
        self.library_default_content = library_content
        self.library_items = ExcelLibraryListWidget()
        self.library_items.setObjectName("libraryItems")
        # Reserve the scrollbar column so filtering never widens collection details.
        self.library_items.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        # This is a sibling of the viewport so scrolling viewport children can
        # never move or cover the worksheet's permanent column boundary.
        self.library_column_border = QFrame(self.library_items)
        self.library_column_border.setObjectName("libraryColumnBorder")
        self.library_column_border.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.library_column_border.hide()
        self.library_items.verticalScrollBar().valueChanged.connect(self._sync_search_sheet_scroll)
        self.library_items.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.library_items.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.library_items.setMouseTracking(True)
        self.library_items.viewport().setMouseTracking(True)
        self.library_items.viewport().installEventFilter(self)
        self.library_items.itemDoubleClicked.connect(self.play_library_track)
        self.library_items.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library_items.customContextMenuRequested.connect(self.show_library_context_menu)
        library_content_layout.addWidget(self.library_items, 1)
        self.library_status = QLabel()
        self.library_status.setObjectName("mutedText")
        library_content_layout.addWidget(self.library_status)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("librarySplitter")
        splitter.setHandleWidth(7)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(playlist_sidebar)
        splitter.addWidget(library_content)
        splitter.setCollapsible(0, True)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([190, 700])
        layout.addWidget(splitter, 1)

        self.library_excel_view = QWidget()
        self.library_excel_view.setObjectName("excelLibraryView")
        self.library_excel_layout = ExcelLibraryGridLayout(self.library_excel_view)
        self.library_items.sheet_layout = self.library_excel_layout
        self.library_excel_layout.setContentsMargins(0, 0, 0, 0)
        self.library_excel_layout.setSpacing(0)
        title_cell = QLabel("曲库")
        title_cell.setObjectName("excelLibraryTitleCell")
        title_cell.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        title_cell.setFixedHeight(ExcelSheetStack.cell_height)
        self.library_excel_layout.addWidget(title_cell, 0, 0)
        for column in range(1, len(ExcelSheetStack.library_column_weights)):
            blank_cell = QLabel()
            blank_cell.setObjectName("excelLibraryBlankCell")
            blank_cell.setFixedHeight(ExcelSheetStack.cell_height)
            self.library_excel_layout.addWidget(blank_cell, 0, column)
        for column, weight in enumerate(
            ExcelSheetStack.library_column_weights
        ):
            self.library_excel_layout.setColumnStretch(column, weight)
        self.library_excel_layout.setRowStretch(2, 1)
        page.addWidget(self.library_default_view)
        page.addWidget(self.library_excel_view)
        self._refresh_library_playlists()
        return page

    def _refresh_library_playlists(self):
        playlist_list = self.library_playlist_list
        was_blocked = playlist_list.blockSignals(True)
        playlist_list.clear()
        all_songs_item = QListWidgetItem("全部歌曲")
        all_songs_item.setData(Qt.ItemDataRole.UserRole, None)
        playlist_list.addItem(all_songs_item)
        selected_item = all_songs_item
        for playlist_id, name in self.store.list_playlists():
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, playlist_id)
            playlist_list.addItem(item)
            if playlist_id == self.selected_library_playlist_id:
                selected_item = item
        if (
            self.selected_library_playlist_id is not None
            and selected_item is all_songs_item
        ):
            self.selected_library_playlist_id = None
        playlist_list.setCurrentItem(selected_item)
        playlist_list.blockSignals(was_blocked)
        self.selected_library_playlist_id = selected_item.data(
            Qt.ItemDataRole.UserRole
        )
        self._update_library_playlist_item_sizes()

    def _update_library_playlist_item_sizes(self):
        height = (
            ExcelSheetStack.cell_height if self.skin == "excel" else -1
        )
        for index in range(self.library_playlist_list.count()):
            item = self.library_playlist_list.item(index)
            item.setSizeHint(
                QSize(0, height) if height > 0 else QSize()
            )

    def _on_library_playlist_selected(self):
        item = self.library_playlist_list.currentItem()
        if item is None:
            return
        playlist_id = item.data(Qt.ItemDataRole.UserRole)
        if playlist_id == self.selected_library_playlist_id:
            return
        self.selected_library_playlist_id = playlist_id
        self._sync_playback_playlist()
        self.refresh_library()

    def show_library_playlist_context_menu(self, position):
        item = self.library_playlist_list.itemAt(position)
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        self.library_playlist_list.setCurrentItem(item)
        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        chosen = menu.exec(self.library_playlist_list.mapToGlobal(position))
        if chosen == rename_action:
            self._rename_library_playlist()
        elif chosen == delete_action:
            self._delete_library_playlist()

    def _create_library_playlist(self):
        name, accepted = QInputDialog.getText(self, "新建歌单", "歌单名称：")
        name = name.strip()
        if not accepted or not name:
            return
        try:
            playlist_id = self.store.create_playlist(name)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "创建失败", "已存在同名歌单。")
            return
        except sqlite3.Error as exc:
            logging.getLogger("echoplay.library").exception(
                "Could not create playlist %s", name
            )
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self.selected_library_playlist_id = playlist_id
        self._refresh_library_playlists()
        self._sync_playback_playlist()
        self.refresh_library()

    def _rename_library_playlist(self):
        playlist_id = self.selected_library_playlist_id
        item = self.library_playlist_list.currentItem()
        if playlist_id is None or item is None:
            return
        name, accepted = QInputDialog.getText(
            self, "重命名歌单", "歌单名称：", text=item.text()
        )
        name = name.strip()
        if not accepted or not name or name == item.text():
            return
        try:
            self.store.rename_playlist(playlist_id, name)
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "重命名失败", "已存在同名歌单。")
            return
        except sqlite3.Error as exc:
            logging.getLogger("echoplay.library").exception(
                "Could not rename playlist %d", playlist_id
            )
            QMessageBox.warning(self, "重命名失败", str(exc))
            return
        self._refresh_library_playlists()

    def _delete_library_playlist(self):
        playlist_id = self.selected_library_playlist_id
        item = self.library_playlist_list.currentItem()
        if playlist_id is None or item is None:
            return
        answer = QMessageBox.question(
            self,
            "删除歌单",
            f"确定删除歌单“{item.text()}”？歌单中的歌曲仍会保留在曲库。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete_playlist(playlist_id)
        except sqlite3.Error as exc:
            logging.getLogger("echoplay.library").exception(
                "Could not delete playlist %d", playlist_id
            )
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self.selected_library_playlist_id = None
        self._refresh_library_playlists()
        self._sync_playback_playlist()
        self.refresh_library()


    def _make_playback_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("playbackBar")
        bar.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(14, 8, 14, 8)
        main_row = QHBoxLayout()
        track_info = QVBoxLayout()
        self.playback_title = MarqueeLabel("尚未播放")
        self.playback_title.setStyleSheet("font-weight: 600;")
        self.playback_title.clicked.connect(
            self._navigate_to_current_track_in_library
        )
        self.playback_title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.playback_details = QLabel("选择曲库中的视频开始播放")
        self.playback_details.setObjectName("mutedText")
        self.playback_details.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        details_row = QHBoxLayout()
        details_row.addWidget(self.playback_details, 1)
        self.playback_status = QLabel("就绪")
        self.playback_status.setObjectName("mutedText")
        self.playback_status.setFixedWidth(120)
        self.playback_status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        details_row.addWidget(self.playback_status)
        track_info.addWidget(self.playback_title)
        track_info.addLayout(details_row)
        self.play_pause_button = QPushButton()
        self.play_pause_action = "play"
        self.play_pause_button.setObjectName("playbackControlButton")
        self.play_pause_button.setIconSize(QSize(22, 22))
        self.play_pause_button.setToolTip("继续")
        self.play_pause_button.setAccessibleName("继续")
        self.play_pause_button.setEnabled(False)
        self.play_pause_button.clicked.connect(self.toggle_play_pause)
        self.previous_button = QPushButton()
        self.previous_button.setObjectName("playbackControlButton")
        self.previous_button.setIconSize(QSize(22, 22))
        self.previous_button.setToolTip("上一首")
        self.previous_button.setAccessibleName("上一首")
        self.previous_button.setEnabled(False)
        self.previous_button.clicked.connect(self.play_previous)
        self.next_button = QPushButton()
        self.next_button.setObjectName("playbackControlButton")
        self.next_button.setIconSize(QSize(22, 22))
        self.next_button.setToolTip("下一首")
        self.next_button.setAccessibleName("下一首")
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self.play_next)
        self.volume_button = QToolButton()
        self.volume_button.setObjectName("volumeButton")
        self.volume_button.setIconSize(QSize(22, 22))
        self.volume_button.setFixedSize(32, 32)
        self.volume_button.setToolTip("音量")
        self.volume_button.setAccessibleName("音量")
        self.volume_button.clicked.connect(self.toggle_mute)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setObjectName("volumeSlider")
        self.volume_slider.setRange(0, 100)
        saved_volume = self.settings.value("audio/volume", 70, type=int)
        initial_volume = max(0, min(100, saved_volume))
        saved_nonzero_volume = self.settings.value(
            "audio/last_nonzero_volume", 70, type=int
        )
        self._last_nonzero_volume = (
            initial_volume
            if initial_volume
            else max(1, min(100, saved_nonzero_volume))
        )
        self.volume_slider.setValue(initial_volume)
        self.volume_slider.setFixedWidth(110)
        self.volume_slider.valueChanged.connect(self.set_volume)
        self.set_volume(self.volume_slider.value())
        self.playback_order = "列表循环"
        self.playback_order_options = (
            ("列表循环", "repeat_all"),
            ("单曲循环", "repeat_one"),
            ("随机播放", "shuffle"),
        )
        self.playback_order_icons = dict(self.playback_order_options)
        self.playback_order_buttons: dict[str, QToolButton] = {}
        self.playback_order_group = QButtonGroup(self)
        self.playback_order_group.setExclusive(True)
        for order_id, (mode, icon_name) in enumerate(
            self.playback_order_options
        ):
            button = QToolButton()
            button.setObjectName("playbackOrderButton")
            button.setCheckable(True)
            button.setIconSize(QSize(22, 22))
            button.setFixedSize(32, 32)
            button.setToolTip(mode)
            button.setAccessibleName(mode)
            self.playback_order_group.addButton(button, order_id)
            self.playback_order_buttons[mode] = button
        self.playback_order_group.idClicked.connect(
            self._set_playback_order_by_id
        )
        saved_order = self.settings.value("playback/order", "列表循环", type=str)
        self._set_playback_order(
            saved_order if saved_order in self.playback_order_icons else "列表循环"
        )
        main_row.addLayout(track_info, 1)
        main_row.addWidget(self.previous_button)
        main_row.addWidget(self.play_pause_button)
        main_row.addWidget(self.next_button)
        main_row.addWidget(self.volume_button)
        main_row.addWidget(self.volume_slider)
        for mode, _icon_name in self.playback_order_options:
            main_row.addWidget(self.playback_order_buttons[mode])
        layout.addLayout(main_row)

        progress_row = QHBoxLayout()
        self.elapsed_label = QLabel("00:00")
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setObjectName("playbackProgressSlider")
        self.progress_slider.setRange(0, 0)
        self.progress_slider.sliderPressed.connect(self._begin_seek)
        self.progress_slider.sliderReleased.connect(self._finish_seek)
        self.duration_label = QLabel("00:00")
        progress_row.addWidget(self.elapsed_label)
        progress_row.addWidget(self.progress_slider, 1)
        progress_row.addWidget(self.duration_label)
        layout.addLayout(progress_row)
        return bar

    def play_library_track(self, item: QListWidgetItem):
        track = item.data(Qt.ItemDataRole.UserRole)
        title_label = self.library_title_labels.get(id(item))
        self._request_play_track(
            track,
            require_library=True,
            display_title=(
                title_label.text()
                if title_label is not None
                else track.title if track is not None else None
            ),
        )

    def _request_play_track(
        self,
        track: Track | None,
        *,
        require_library: bool = False,
        display_title: str | None = None,
        record_history: bool = False,
    ):
        if not track:
            return
        if self.pending_play_id == track.playback_id:
            self._record_current_play_in_history = record_history
            return
        if require_library:
            library_track = next(
                (
                    stored_track
                    for stored_track in self.store.list_tracks()
                    if stored_track.playlist_id == track.playlist_id
                ),
                None,
            )
            if library_track is None:
                self._notify_library_change(
                    "该分P已不在曲库中，无法播放。"
                )
                return
            track = library_track
        logging.getLogger("echoplay.playback").info(
            "Requested playback for track=%s", track.bvid
        )
        cached_audio = None
        if self._next_audio_prefetch is not None:
            cached_audio = self._next_audio_prefetch.take(track)
            self._next_audio_prefetch.cancel()
            self._next_audio_prefetch = None
        self.stop_playback(update_status=False)
        self.pending_play_id = track.playback_id
        playlist_tracks = self._list_current_library_tracks(
            self.library_filter.text() if hasattr(self, "library_filter") else ""
        )
        self.playlist = self._playback_tracks_in_library_order(playlist_tracks)
        aggregate_pages = [
            page
            for page in self.store.list_tracks()
            if page.bvid == track.bvid and not page.is_standalone
        ]
        if (
            not track.is_standalone
            and len(aggregate_pages) == 1
            and (track.part_title or self.store.list_removed_pages(track.bvid))
        ):
            track = replace(track, title=self._single_page_title(track))
        elif require_library and not track.is_standalone and len(aggregate_pages) > 1:
            track = replace(
                track, title=track.part_title or f"P{track.page_number}"
            )
        if display_title is not None:
            track = replace(track, title=display_title)
        self.current_track = track
        self._record_current_play_in_history = record_history
        self._history_recorded_play_id = None
        self._update_track_navigation()
        self.playback_title.setText(track.title)
        self.playback_details.setText("正在获取 B 站音轨")
        self._set_playback_status("正在解析…")
        if cached_audio is not None:
            stream, data = cached_audio
            self._audio_resolved(track, stream, cached_data=data)
            return
        worker = Worker(resolve_audio_stream, track, dict(self.bilibili_cookies), self.stream_quality)
        worker.signals.succeeded.connect(
            lambda stream, item_track=track: self._audio_stream_ready.emit(item_track, stream)
        )
        worker.signals.failed.connect(
            lambda message, play_id=track.playback_id:
                self._audio_stream_failed.emit(play_id, message)
        )
        self._start_worker(worker)

    def _start_worker(self, worker: Worker):
        self._workers.add(worker)
        worker.signals.finished.connect(
            self._worker_finished, Qt.ConnectionType.QueuedConnection
        )
        pool = self.pool
        if worker.function is search_videos:
            pool = self.search_pool
            worker.result_epoch = self._result_epoch
        elif worker.function is fetch_cover:
            pool = self.cover_pool
            worker.result_epoch = self._result_epoch
        elif worker.function is fetch_video_pages:
            pool = self.metadata_pool
        worker.task_pool = pool
        pool.start(worker)

    def _worker_finished(self, worker: Worker):
        self._workers.discard(worker)
        job = getattr(worker, "prefetch_job", None)
        if job is not None and job is self._next_audio_prefetch:
            self._prepare_preloaded_audio(job)
        buffer = getattr(worker, "streaming_audio_buffer", None)
        if buffer is not None:
            buffer.producer_done = True
            self._try_dispose_audio_buffer(buffer)

    def _on_audio_stream_ready(self, track: Track, stream: AudioStream):
        logging.getLogger("echoplay.playback").info(
            "Audio resolution delivered to UI thread: track=%s", track.bvid
        )
        self._audio_resolved(track, stream)

    def _on_audio_stream_failed(self, bvid: str, message: str):
        if self.pending_play_id == bvid or self.active_play_id == bvid:
            self._playback_failed(message)

    def _audio_resolved(self, track: Track, stream: AudioStream, *, cached_data=None):
        logging.getLogger("echoplay.playback").info(
            "Audio resolution callback: track=%s pending=%s",
            track.playback_id, self.pending_play_id,
        )
        if self.pending_play_id != track.playback_id:
            logging.getLogger("echoplay.playback").info(
                "Ignoring stale audio resolution for track=%s", track.bvid
            )
            return
        if (
            self.current_track is not None
            and self.current_track.playback_id == track.playback_id
        ):
            track = replace(track, title=self.current_track.title)
        self.current_stream_description = stream.description
        self.current_track_title = track.title
        self.playback_title.setText(self.current_track_title)
        self.playback_details.setText(f"Qt Multimedia · {stream.description}")
        self._set_playback_status("正在从网络接收音频…")
        self.play_pause_button.setEnabled(False)
        preloaded = self._preloaded_audio
        if (cached_data is not None and preloaded is not None
                and preloaded[0] == track.playback_id
                and preloaded[2].mediaStatus() != QMediaPlayer.MediaStatus.InvalidMedia):
            _, buffer, player = preloaded
            self._preloaded_audio = None
            buffer.preloaded_player = player
        else:
            self._clear_preloaded_audio()
            buffer = (StreamingAudioDevice(self, initial_data=cached_data)
                      if cached_data is not None else RangeAudioDevice(stream, self))
        if not buffer.isOpen() and not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            buffer.deleteLater()
            self._playback_failed("无法创建音频流缓冲区。")
            return
        self.audio_buffer = buffer
        if cached_data is not None:
            buffer.finish()
            buffer.producer_done = True
            self._on_audio_device_ready(track, stream, buffer)
            self._schedule_next_audio_prefetch()
            return
        last_progress_time = 0.0

        def report_progress(received, total):
            nonlocal last_progress_time
            now = monotonic()
            if now - last_progress_time >= 0.2 or received == total:
                self._audio_download_progress.emit(track.playback_id, received, total)
                last_progress_time = now

        def download():
            received = buffer.download(
                lambda: self._audio_device_ready.emit(track, stream, buffer),
                report_progress,
            )
            self._audio_download_complete.emit(track.playback_id, received, buffer)
            return received

        logging.getLogger("echoplay.playback").info(
            "Streaming audio into a seekable memory buffer for track=%s quality=%s",
            track.bvid, stream.description,
        )
        worker = Worker(download)
        worker.streaming_audio_buffer = buffer
        worker.signals.failed.connect(
            lambda message, play_id=track.playback_id, item_buffer=buffer:
                self._audio_download_failed.emit(play_id, message, item_buffer)
        )
        self._start_worker(worker)

    def _on_audio_download_progress(self, bvid: str, received: int, total: int):
        if self.pending_play_id != bvid:
            return
        received_mb = received / (1024 * 1024)
        if total:
            self._set_playback_status(
                f"正在接收音频至内存… {received_mb:.1f}/{total / (1024 * 1024):.1f} MB"
            )
        else:
            self._set_playback_status(f"正在接收音频至内存… {received_mb:.1f} MB")

    def _on_audio_device_ready(
        self, track: Track, stream: AudioStream, buffer: StreamingAudioDevice | RangeAudioDevice
    ):
        if self.pending_play_id != track.playback_id:
            return
        if self.audio_buffer is not buffer:
            return
        self.pending_play_id = None
        self.active_play_id = track.playback_id
        buffer.player_released = False
        source_hint = QUrl(f"memory://audio.{stream.format_hint}")
        preloaded_player = getattr(buffer, "preloaded_player", None)
        if preloaded_player is not None:
            previous_player = self.media_player
            self._connect_playback_signals(previous_player, False)
            previous_player.setAudioOutput(None)
            self.media_player = preloaded_player
            self.media_player.setAudioOutput(self.audio_output)
            self._connect_playback_signals(self.media_player, True)
            previous_player.deleteLater()
            buffer.preloaded_player = None
            self.progress_slider.setEnabled(self.media_player.isSeekable())
            self._on_duration_changed(self.media_player.duration())
        else:
            self.media_player.setSourceDevice(buffer, source_hint)
        self._set_playback_status("正在解码并播放…")
        self._set_play_pause_action("pause")
        self._update_track_navigation()
        self._playback_start_timer.start()
        logging.getLogger("echoplay.playback").info(
            "Starting playback from streamed audio: track=%s buffered_bytes=%d",
            track.bvid, buffer.bytesAvailable(),
        )
        self.media_player.play()
        self._schedule_next_audio_prefetch()
        logging.getLogger("echoplay.playback").info(
            "Media source submitted: format=%s status=%s state=%s",
            stream.format_hint,
            self.media_player.mediaStatus().name,
            self.media_player.playbackState().name,
        )

    def _on_audio_outputs_changed(self):
        device = QMediaDevices.defaultAudioOutput()
        if self.audio_output.device() != device:
            self.audio_output.setDevice(device)

    def _on_audio_download_complete(
        self, bvid: str, received: int, buffer: StreamingAudioDevice | RangeAudioDevice
    ):
        if self.audio_buffer is not buffer:
            return
        buffer.producer_done = True
        self._schedule_next_audio_prefetch()
        logging.getLogger("echoplay.playback").info(
            "Audio stream received: track=%s bytes=%d", bvid, received
        )
        if (
            self.active_play_id == bvid
            and self._pending_seek_position is not None
        ):
            position = self._pending_seek_position
            resume_playback = self._resume_after_download
            self._pending_seek_position = None
            self._resume_after_download = False
            self.media_player.setPosition(position)
            if resume_playback:
                self.media_player.play()
                self._set_playback_status("正在播放")
                self._set_play_pause_action("pause")
            else:
                self._set_playback_status("已暂停")
                self._set_play_pause_action("play")
            logging.getLogger("echoplay.playback").info(
                "Applied deferred seek at %d ms after audio download: track=%s resume=%s",
                position,
                bvid,
                resume_playback,
            )
        else:
            self._pending_seek_position = None
            self._resume_after_download = False

    def _connect_playback_signals(self, player, connect):
        bindings = (
            (player.positionChanged, self._on_position_changed),
            (player.durationChanged, self._on_duration_changed),
            (player.playbackStateChanged, self._on_playback_state_changed),
            (player.mediaStatusChanged, self._on_media_status_changed),
            (player.bufferProgressChanged, self._on_media_buffer_progress),
            (player.errorOccurred, self._on_media_error),
            (player.seekableChanged, self.progress_slider.setEnabled),
        )
        for signal, callback in bindings:
            if connect:
                signal.connect(callback)
            else:
                signal.disconnect(callback)

    def _clear_preloaded_audio(self):
        if self._preloaded_audio is None:
            return
        _, buffer, player = self._preloaded_audio
        self._preloaded_audio = None
        player.setSource(QUrl())
        player.deleteLater()
        buffer.close()
        buffer.deleteLater()

    def _prepare_preloaded_audio(self, job):
        cached = job.peek()
        if cached is None:
            return
        stream, data = cached
        self._clear_preloaded_audio()
        buffer = StreamingAudioDevice(self, initial_data=data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        buffer.finish()
        buffer.producer_done = True
        player = QMediaPlayer(self)
        player.setSourceDevice(buffer, QUrl(f"memory://audio.{stream.format_hint}"))
        self._preloaded_audio = (job.track.playback_id, buffer, player)

    def _schedule_next_audio_prefetch(self):
        if not self.playlist or self.current_track is None:
            return
        if self._selected_playback_order() != "\u5217\u8868\u5faa\u73af":
            return
        index = next((i for i, track in enumerate(self.playlist)
                      if track.playlist_id == self.current_track.playlist_id), -1)
        if index < 0 or len(self.playlist) < 2:
            return
        target = self.playlist[(index + 1) % len(self.playlist)]
        if (self._next_audio_prefetch is not None
                and self._next_audio_prefetch.track.playback_id == target.playback_id):
            return
        if self._next_audio_prefetch is not None:
            self._next_audio_prefetch.cancel()
        self._clear_preloaded_audio()
        self._next_audio_prefetch = AudioPrefetch(target, self.bilibili_cookies, self.stream_quality)
        worker = Worker(self._next_audio_prefetch.run)
        worker.prefetch_job = self._next_audio_prefetch
        self._start_worker(worker)

    def _on_audio_download_failed(
        self, bvid: str, message: str, buffer: StreamingAudioDevice | RangeAudioDevice
    ):
        if self.audio_buffer is buffer and (
            self.pending_play_id == bvid or self.active_play_id == bvid
        ):
            self._playback_failed(message)

    def _on_playback_start_timeout(self):
        if self.active_play_id is not None and self.media_player.playbackState() != \
                QMediaPlayer.PlaybackState.PlayingState:
            media_status = self.media_player.mediaStatus().name
            error = self.media_player.errorString()
            logging.getLogger("echoplay.playback").error(
                "Playback start timed out: track=%s status=%s error=%s",
                self.active_play_id, media_status, error,
            )
            details = f"（媒体状态：{media_status}）"
            if error:
                details += f"\n{error}"
            self._playback_failed(f"音频已接收，但无法开始播放{details}")

    def _set_playback_status(self, text: str):
        self.playback_status.setText(
            QFontMetrics(self.playback_status.font()).elidedText(
                text, Qt.TextElideMode.ElideRight, self.playback_status.width()
            )
        )

    def _notify_library_change(self, message: str):
        self.library_notification.setText(message)
        self.library_notification.adjustSize()
        self._position_library_notification()
        self.library_notification.raise_()
        self.library_notification.show()
        self.library_notification_timer.start()

    def _position_library_notification(self):
        if not hasattr(self, "library_notification"):
            return
        x = (self.width() - self.library_notification.width()) // 2
        self.library_notification.move(max(12, x), 18)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_library_notification()
        self._sync_search_sheet_scroll()
        if self.skin == "excel":
            self._update_excel_ribbon_display_size()

    def _update_excel_ribbon_display_size(self):
        margins = self.root_layout.contentsMargins()
        # Window state and client geometry can disagree during restoration.
        # Always use the screen reference so resize events cannot shrink the image.
        width = self.screen().availableGeometry().width()
        self.excel_ribbon.set_display_width(width - margins.left() - margins.right())

    def _retire_audio_buffer(self):
        buffer = self.audio_buffer
        self.audio_buffer = None
        if buffer is not None:
            buffer.cancel()
            self._retired_audio_buffers.append(buffer)
            self._try_dispose_audio_buffer(buffer)

    def _dispose_audio_buffer(self, buffer: StreamingAudioDevice | RangeAudioDevice):
        if buffer not in self._retired_audio_buffers:
            return
        self._retired_audio_buffers.remove(buffer)
        buffer.clear_cached_data()
        buffer.close()
        buffer.deleteLater()

    def _try_dispose_audio_buffer(self, buffer: StreamingAudioDevice | RangeAudioDevice):
        if buffer.producer_done and buffer.player_released:
            self._dispose_audio_buffer(buffer)

    def stop_playback(self, update_status: bool = True):
        if self._next_audio_prefetch is not None:
            self._next_audio_prefetch.cancel()
            self._next_audio_prefetch = None
        self._playback_start_timer.stop()
        self._pending_seek_position = None
        self._resume_after_download = False
        self.pending_play_id = None
        self.active_play_id = None
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        self._retire_audio_buffer()
        self._set_play_pause_action("play")
        self.play_pause_button.setEnabled(False)
        self.progress_slider.setRange(0, 0)
        self.progress_slider.setValue(0)
        self.progress_slider.setEnabled(False)
        self.elapsed_label.setText("00:00")
        self.duration_label.setText("00:00")
        self._update_track_navigation()
        self._update_library_playing_indicator()
        if update_status:
            self.playback_title.setText("尚未播放")
            self.playback_details.setText("选择曲库中的视频开始播放")
            self._set_playback_status("已停止")

    def toggle_play_pause(self):
        if self._pending_seek_position is not None:
            self._resume_after_download = not self._resume_after_download
            self._set_play_pause_action(
                "pause" if self._resume_after_download else "play"
            )
            self._set_playback_status(
                "正在缓冲跳转位置…"
                if self._resume_after_download
                else "正在缓冲跳转位置（暂停）…"
            )
            return
        state = self.media_player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        elif self.active_play_id is not None:
            self.media_player.play()

    @staticmethod
    def _format_time(milliseconds) -> str:
        seconds = max(0, int(milliseconds or 0) // 1000)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    def _on_position_changed(self, position: int):
        if not self._seeking and self._pending_seek_position is None:
            self.progress_slider.setValue(max(0, position // 1000))
        displayed_position = (
            self._pending_seek_position
            if self._pending_seek_position is not None
            else position
        )
        elapsed = self._format_time(displayed_position)
        if self.elapsed_label.text() != elapsed:
            self.elapsed_label.setText(elapsed)

    def _on_duration_changed(self, duration: int):
        self.progress_slider.setRange(0, max(0, duration // 1000))
        self.duration_label.setText(self._format_time(duration))
        self.progress_slider.setEnabled(self.media_player.isSeekable())

    def _begin_seek(self):
        self._seeking = True

    def _finish_seek(self):
        self._seeking = False
        if not self.media_player.isSeekable():
            return
        position = self.progress_slider.value() * 1000
        resume_playback = (
            self._resume_after_download
            if self._pending_seek_position is not None
            else self.media_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )
        # The stream device can seek before the download completes. Its reader
        # waits only for the requested bytes, so cached positions resume at once.
        self._pending_seek_position = None
        self._resume_after_download = False
        self.media_player.setPosition(position)
        self.elapsed_label.setText(self._format_time(position))
        if resume_playback:
            self.media_player.play()

    def set_volume(self, volume: int):
        volume = max(0, min(100, volume))
        if volume > 0:
            self._last_nonzero_volume = volume
            self.settings.setValue("audio/last_nonzero_volume", volume)
        self.audio_output.setVolume(volume / 100.0)
        self.settings.setValue("audio/volume", volume)
        self._refresh_volume_icon()

    def _on_playback_state_changed(self, state):
        logging.getLogger("echoplay.playback").info(
            "Playback state changed: track=%s state=%s media_status=%s",
            self.active_play_id, state.name, self.media_player.mediaStatus().name,
        )
        if state == QMediaPlayer.PlaybackState.PlayingState:
            if (self._record_current_play_in_history and self.current_track is not None
                    and self.active_play_id == self.current_track.playback_id
                    and self._history_recorded_play_id != self.active_play_id):
                try:
                    self.store.record_playback(self.current_track)
                    self._history_recorded_play_id = self.active_play_id
                except sqlite3.Error:
                    logging.getLogger("echoplay.playback").exception("Could not save playback history")
            self._playback_start_timer.stop()
            self._set_play_pause_action("pause")
            self._set_playback_status("正在播放")
            self.play_pause_button.setEnabled(True)
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self._set_play_pause_action("play")
            self._set_playback_status("已暂停")
            self.play_pause_button.setEnabled(True)
        else:
            self._set_play_pause_action("play")
            self.play_pause_button.setEnabled(self.active_play_id is not None)
        self._update_library_playing_indicator()

    def _update_library_playing_indicator(self):
        playback_state = self.media_player.playbackState()
        is_active = (
            self.active_play_id is not None
            and self.current_track is not None
            and playback_state in (
                QMediaPlayer.PlaybackState.PlayingState,
                QMediaPlayer.PlaybackState.PausedState,
            )
        )
        for item_id, indicator in self.library_play_indicators.items():
            item = self.library_row_items.get(item_id)
            track = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            is_current_track = (
                is_active
                and track is not None
                and (
                    track.playlist_id == self.current_track.playlist_id
                    or (
                        not track.is_standalone
                        and not self.current_track.is_standalone
                        and track.bvid == self.current_track.bvid
                    )
                )
            )
            indicator.setStyleSheet(
                f"background-color: {self.palette().color(QPalette.ColorRole.Highlight).name()};"
                if is_current_track
                else "background-color: transparent;"
            )
            page_list = self.library_page_lists.get(item_id)
            if page_list is None:
                continue
            for page_index in range(page_list.count()):
                page_item = page_list.item(page_index)
                page_track = page_item.data(Qt.ItemDataRole.UserRole)
                is_current_page = (
                    is_active
                    and page_track is not None
                    and not self.current_track.is_standalone
                    and page_track.track_id == self.current_track.track_id
                )
                page_item.setIcon(
                    self.playing_page_icon if is_current_page else self.empty_page_icon
                )
        for item_id, indicator in self.search_result_play_indicators.items():
            item = self.search_result_row_items.get(item_id)
            track = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            is_current_track = (
                is_active
                and track is not None
                and (
                    track.playlist_id == self.current_track.playlist_id
                    or (
                        not track.is_standalone
                        and not self.current_track.is_standalone
                        and track.bvid == self.current_track.bvid
                    )
                )
            )
            indicator.setStyleSheet(
                f"background-color: {self.palette().color(QPalette.ColorRole.Highlight).name()};"
                if is_current_track
                else "background-color: transparent;"
            )
        for page_list in self.search_result_page_lists.values():
            for page_index in range(page_list.count()):
                page_item = page_list.item(page_index)
                page_track = page_item.data(Qt.ItemDataRole.UserRole)
                is_current_page = (
                    is_active
                    and page_track is not None
                    and not self.current_track.is_standalone
                    and page_track.track_id == self.current_track.track_id
                )
                page_item.setIcon(
                    self.playing_page_icon if is_current_page else self.empty_page_icon
                )

    def _on_media_status_changed(self, status):
        logging.getLogger("echoplay.playback").info(
            "Media status changed: track=%s status=%s state=%s error=%s",
            self.active_play_id, status.name,
            self.media_player.playbackState().name, self.media_player.errorString(),
        )
        if status == QMediaPlayer.MediaStatus.LoadingMedia:
            self._set_playback_status("正在识别并解码音频…")
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._set_playback_status("正在播放")
            else:
                self._set_playback_status("音频已就绪")
        elif status == QMediaPlayer.MediaStatus.BufferingMedia:
            self._set_playback_status("正在缓冲…")
        elif status == QMediaPlayer.MediaStatus.BufferedMedia:
            if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._set_playback_status("正在播放")
            elif self.media_player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
                self._set_playback_status("已暂停")
            else:
                self._set_playback_status("音频已就绪")
        elif status == QMediaPlayer.MediaStatus.StalledMedia:
            self._set_playback_status("网络音频暂时停滞，正在等待数据…")
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._playback_failed(
                self.media_player.errorString() or "Qt Multimedia 无法识别该音频格式。"
            )
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            buffer = self.audio_buffer
            if (
                self.active_play_id is not None
                and buffer is not None
                and not buffer.producer_done
            ):
                if not self._resume_after_download:
                    if self._pending_seek_position is None:
                        self._pending_seek_position = self.media_player.position()
                        self._resume_after_download = True
                    logging.getLogger("echoplay.playback").warning(
                        "Ignoring premature end-of-media while audio is still downloading: "
                        "track=%s position=%d received_bytes=%d",
                        self.active_play_id,
                        self._pending_seek_position,
                        buffer.total_received,
                    )
                self._set_playback_status(
                    "正在缓冲跳转位置…"
                    if self._resume_after_download
                    else "正在缓冲跳转位置（暂停）…"
                )
                return
            if self.audio_buffer is not None:
                self.audio_buffer.player_released = True
                self._try_dispose_audio_buffer(self.audio_buffer)
            self._next_playlist_track(1, automatic=True)
        elif (
            status == QMediaPlayer.MediaStatus.NoMedia
            and self.media_player.mediaStatus() == QMediaPlayer.MediaStatus.NoMedia
            and self._retired_audio_buffers
        ):
            for buffer in tuple(self._retired_audio_buffers):
                buffer.player_released = True
                self._try_dispose_audio_buffer(buffer)

    def _on_media_buffer_progress(self, progress: float):
        if self.active_play_id is not None and \
                self.media_player.mediaStatus() == QMediaPlayer.MediaStatus.BufferingMedia:
            self._set_playback_status(f"正在缓冲音频… {progress:.0%}")

    def _on_media_error(self, _error, error_string: str):
        if self.active_play_id is not None or self.pending_play_id is not None:
            self._playback_failed(error_string or "Qt Multimedia 无法解码该音轨。")

    def _update_track_navigation(self):
        has_tracks = bool(self.playlist) and self.pending_play_id is None
        self.previous_button.setEnabled(has_tracks)
        self.next_button.setEnabled(has_tracks)

    def _next_playlist_track(self, direction: int = 1, *, automatic: bool = False):
        if not self.playlist:
            return
        playback_order = self._selected_playback_order()
        if automatic and playback_order == "单曲循环":
            self._play_track(self.current_track)
            return
        if playback_order == "随机播放" and len(self.playlist) > 1:
            import random
            candidates = [
                track for track in self.playlist
                if track.playlist_id
                != getattr(self.current_track, "playlist_id", None)
            ]
            target = random.choice(candidates)
        else:
            index = next((i for i, track in enumerate(self.playlist)
                          if track.playlist_id
                          == getattr(self.current_track, "playlist_id", None)), -1)
            new_index = index + direction
            if new_index < 0:
                new_index = len(self.playlist) - 1
            if new_index >= len(self.playlist):
                if automatic and playback_order != "列表循环":
                    self._set_playback_status("播放已结束")
                    self.play_pause_button.setEnabled(False)
                    return
                new_index = 0
            target = self.playlist[new_index]
        self._play_track(target)

    def _play_track(self, track: Track | None):
        if not track:
            return
        for index in range(self.library_items.count()):
            item = self.library_items.item(index)
            candidate = item.data(Qt.ItemDataRole.UserRole)
            if (
                candidate
                and candidate.playlist_id == track.playlist_id
            ):
                self._request_play_track(track, require_library=True)
                return
        self._request_play_track(track, require_library=True)

    def _navigate_to_current_track_in_library(self):
        track = self.current_track
        if track is None:
            return
        stored_track = next(
            (
                candidate
                for candidate in self.store.list_tracks()
                if candidate.playlist_id == track.playlist_id
            ),
            None,
        )
        if stored_track is None:
            return
        if self.library_filter.text():
            self.library_filter.clear()
        target_item = None
        for item in self.library_row_items.values():
            row_track = item.data(Qt.ItemDataRole.UserRole)
            matches = (
                row_track.playlist_id == stored_track.playlist_id
                if stored_track.is_standalone
                else (
                    not row_track.is_standalone
                    and row_track.bvid == stored_track.bvid
                )
            )
            if matches:
                target_item = item
                break
        if target_item is None:
            return
        self.library_nav_button.setChecked(True)
        self.page_stack.setCurrentWidget(self.library_tab)
        self.library_items.scrollToItem(
            target_item, QAbstractItemView.ScrollHint.PositionAtCenter
        )
        if stored_track.is_standalone:
            return
        item_id = id(target_item)
        pages = self.library_tracks_by_item.get(item_id, [])
        if len(pages) <= 1:
            return
        page_panel = self.library_page_panels.get(item_id)
        page_list = self.library_page_lists.get(item_id)
        if page_panel is None or page_list is None:
            return
        if page_list.count() != len(pages):
            self._populate_library_pages(target_item, pages)
        if page_panel.isHidden():
            page_panel.show()
            self._update_library_item_size(target_item)
        page_item = next(
            (
                page_list.item(index)
                for index in range(page_list.count())
                if (
                    isinstance(
                        page_list.item(index).data(Qt.ItemDataRole.UserRole),
                        Track,
                    )
                    and page_list.item(index).data(
                        Qt.ItemDataRole.UserRole
                    ).playlist_id
                    == stored_track.playlist_id
                )
            ),
            None,
        )
        if page_item is not None:
            page_list.scrollToItem(
                page_item, QAbstractItemView.ScrollHint.PositionAtCenter
            )

    def play_previous(self):
        self._next_playlist_track(-1)

    def play_next(self):
        self._next_playlist_track(1)

    def _playback_failed(self, message: str):
        self._playback_start_timer.stop()
        self._pending_seek_position = None
        self._resume_after_download = False
        self.pending_play_id = None
        self.active_play_id = None
        self.media_player.stop()
        self.media_player.setSource(QUrl())
        self._retire_audio_buffer()
        self.play_pause_button.setEnabled(False)
        self.progress_slider.setEnabled(False)
        self._update_track_navigation()
        self._update_library_playing_indicator()
        logging.getLogger("echoplay.playback").error(
            "Playback failed for track=%s: %s",
            getattr(self.current_track, "bvid", None), message,
        )
        self._set_playback_status(f"播放失败：{message}")
        QMessageBox.warning(self, "播放失败", message)
    def _update_account_status(self):
        self._sync_stream_quality_access()
        logged_in = bool(self.bilibili_cookies.get("SESSDATA"))
        account_text = (
            self.account_nickname or "正在获取 B 站账号昵称…"
            if logged_in
            else "访客模式 · 搜索无需登录"
        )
        self.settings_account_label.setText(account_text)
        self.account_avatar.setVisible(logged_in)
        if logged_in and not self._account_avatar_ready:
            self.account_avatar.clear()
            self.account_avatar.setText(
                (self.account_nickname or "…")[:1].upper()
            )
        elif not logged_in:
            self._account_avatar_ready = False
            self.account_avatar.clear()
        self.login_button.setText("更换账号" if logged_in else "登录 B 站")
        self.logout_button.setVisible(logged_in)

    def _fetch_account_profile(self):
        cookies = dict(self.bilibili_cookies)
        worker = Worker(fetch_account_profile, cookies)
        worker.signals.succeeded.connect(
            lambda profile, expected_cookies=cookies: self._account_profile_loaded(
                profile, expected_cookies
            )
        )
        worker.signals.failed.connect(
            lambda message, expected_cookies=cookies: self._account_profile_failed(
                message, expected_cookies
            )
        )
        self._start_worker(worker)

    def _account_profile_loaded(
        self, profile: tuple[str, str, bool], expected_cookies: dict[str, str]
    ):
        if self.bilibili_cookies != expected_cookies:
            return
        nickname, avatar_url, self.account_is_vip = profile
        self.account_nickname = nickname
        self._update_account_status()
        if not avatar_url:
            logging.getLogger("echoplay.auth").warning(
                "Bilibili profile did not include an avatar URL"
            )
            return
        worker = Worker(fetch_account_avatar, avatar_url)
        worker.signals.succeeded.connect(
            lambda image_data, expected_cookies=expected_cookies:
                self._account_avatar_loaded(image_data, expected_cookies)
        )
        worker.signals.failed.connect(
            lambda message, expected_cookies=expected_cookies:
                self._account_avatar_failed(message, expected_cookies)
        )
        self._start_worker(worker)

    def _account_avatar_loaded(
        self, image_data: bytes, expected_cookies: dict[str, str]
    ):
        if self.bilibili_cookies != expected_cookies:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(image_data):
            logging.getLogger("echoplay.auth").warning(
                "Could not decode Bilibili account avatar"
            )
            return
        size = self.account_avatar.size()
        scaled = pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        cropped = scaled.copy(
            (scaled.width() - size.width()) // 2,
            (scaled.height() - size.height()) // 2,
            size.width(),
            size.height(),
        )
        rounded = QPixmap(size)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, size.width(), size.height())
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, cropped)
        painter.end()
        self.account_avatar.setText("")
        self.account_avatar.setPixmap(rounded)
        self._account_avatar_ready = True

    def _account_avatar_failed(
        self, message: str, expected_cookies: dict[str, str]
    ):
        if self.bilibili_cookies != expected_cookies:
            return
        logging.getLogger("echoplay.auth").warning(
            "Could not load Bilibili account avatar: %s", message
        )

    def _account_profile_failed(self, message: str, expected_cookies: dict[str, str]):
        if self.bilibili_cookies != expected_cookies:
            return
        clear_session()
        self.bilibili_cookies = {}
        self.account_nickname = None
        self.account_is_vip = False
        self._account_avatar_ready = False
        self._update_account_status()
        logging.getLogger("echoplay.auth").warning(
            "Could not fetch Bilibili account profile; treating session as logged out: %s",
            message,
        )

    def open_login_dialog(self):
        from echoplay.ui.login_dialog import BilibiliLoginDialog, prepare_webengine_runtime

        runtime_error = prepare_webengine_runtime()
        if runtime_error:
            logging.getLogger("echoplay.auth").error(
                "Qt WebEngine runtime unavailable: %s", runtime_error
            )
            QMessageBox.critical(self, "无法打开登录页面", runtime_error)
            return

        dialog = BilibiliLoginDialog(self)
        self.login_dialog = dialog
        dialog.login_succeeded.connect(self._save_bilibili_login)
        dialog.exec()
        self.login_dialog = None

    def _save_bilibili_login(self, cookies: dict[str, str]):
        try:
            save_session(cookies)
        except Exception as exc:
            QMessageBox.warning(self, "登录状态保存失败", str(exc))
            if self.login_dialog:
                self.login_dialog.status.setText(f"登录成功，但保存失败：{exc}")
            return
        self.bilibili_cookies = cookies
        self.account_nickname = None
        self.account_is_vip = False
        self._account_avatar_ready = False
        self._update_account_status()
        self._fetch_account_profile()
        if self.login_dialog:
            self.login_dialog.accept()

    def logout_bilibili(self):
        clear_session()
        self.bilibili_cookies = {}
        self.account_nickname = None
        self.account_is_vip = False
        self._account_avatar_ready = False
        self.stop_playback(update_status=False)
        self._update_account_status()

    def closeEvent(self, event):
        self._taskbar_icon_refresh_timer.stop()
        self.library_click_timer.stop()
        self.pending_library_click = None
        self._clear_preloaded_audio()
        self.stop_playback(update_status=False)
        if self._taskbar_icon_handle:
            _set_taskbar_large_icon(int(self.winId()), 0)
            self._destroy_windows_icon(self._taskbar_icon_handle)
            self._taskbar_icon_handle = None
        super().closeEvent(event)

    def _show_recent_searches(self):
        if not self.search_input.hasFocus() or not self.search_input.isVisible():
            return
        if self.search_history_popup.isVisible():
            return
        keywords = self.store.list_search_history()
        if self.search_history_model.stringList() != keywords:
            self.search_history_model.setStringList(keywords)
        self.search_history_popup.show_below_input()

    def _delete_recent_keyword(self, keyword: str):
        self.store.remove_search_keyword(keyword)
        self.search_history_model.setStringList(self.store.list_search_history())
        self.search_history_popup.refresh_after_deletion()
        self.search_input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _search_recent_keyword(self, keyword: str):
        self.search_input.setText(keyword)
        self.search_input.setFocus(Qt.FocusReason.OtherFocusReason)
        self.search()

    def show_playback_history(self):
        self.search_history_popup.hide()
        self.search_generation += 1
        self.search_keyword = None
        self.search_loading = False
        self.search_has_more = False
        self.search_button.setEnabled(True)
        self._clear_search_results()
        tracks = self.store.list_playback_history()
        self._append_search_marker("— 历史记录 —")
        self._append_search_results(tracks, 0)
        self.search_status.setText(
            f"最近播放的 {len(tracks)} 条记录（最多 50 条）" if tracks else "暂无播放历史，播放后会自动记录。")

    def search(self):
        self.search_history_popup.hide()
        keyword = self.search_input.text().strip()
        if not keyword:
            self.search_status.setText("请先输入关键词或 B 站视频链接。")
            return
        self.search_button.setEnabled(False)
        self.search_generation += 1
        generation = self.search_generation
        self.search_keyword = None
        self.search_loading = True
        self.search_has_more = False
        self.next_search_page = 1
        self._clear_search_results()
        if keyword.lower().startswith(("http://", "https://")):
            self.search_status.setText("正在读取视频信息并加入曲库…")
            worker = Worker(fetch_video_from_url, keyword)
            worker.signals.succeeded.connect(self._video_url_added)
            worker.signals.failed.connect(self._video_url_failed)
            self._start_worker(worker)
            return
        self.store.record_search(keyword)
        self.search_history_model.setStringList(self.store.list_search_history())
        self.search_keyword = keyword
        self.search_has_more = True
        self.search_status.setText("正在搜索 B 站公开视频…")
        worker = Worker(
            search_videos, keyword, 30, dict(self.bilibili_cookies), 1
        )
        worker.signals.succeeded.connect(
            lambda tracks, current_generation=generation:
                self._search_page_succeeded(tracks, 1, current_generation)
        )
        worker.signals.failed.connect(
            lambda message, current_generation=generation:
                self._search_page_failed(message, 1, current_generation)
        )
        self._start_worker(worker)

    def _clear_search_results(self):
        self._result_epoch += 1
        # Drop old queued work rather than downloading covers no longer shown.
        for worker in list(self._workers):
            if hasattr(worker, "result_epoch"):
                pool = getattr(worker, "task_pool", None)
                if pool is not None and pool.tryTake(worker):
                    self._workers.discard(worker)
        self._cover_requests.clear()
        self.search_result_pages_active.clear()
        self.results.clear()
        self.search_status.show()
        self._sync_search_sheet_scroll()
        self.cover_labels.clear()
        self.search_result_row_items.clear()
        self.search_result_play_indicators.clear()
        self.search_result_collection_badges.clear()
        self.search_result_containers.clear()
        self.search_result_page_panels.clear()
        self.search_result_page_lists.clear()
        self.search_result_pages.clear()
        self.search_result_pages_pending.clear()
        self.search_result_pages_queue.clear()
        self.search_result_bvids.clear()
        self.search_result_click_timer.stop()
        self.pending_search_result_click = None

    def _video_url_added(self, track: Track):
        self.add_to_library(track)
        self.search_button.setEnabled(True)
        self.search_status.setText(f"已加入曲库：{track.title}")

    def _video_url_failed(self, message: str):
        logging.getLogger("echoplay.search").warning(
            "Could not validate or add video URL: %s", message
        )
        self.search_button.setEnabled(True)
        self.search_status.setText("网址错误请检查")
        QMessageBox.warning(self, "网址错误", "网址错误请检查")

    def _search_page_succeeded(
        self, tracks: list[Track], page: int, generation: int
    ):
        if generation != self.search_generation:
            return
        self.search_button.setEnabled(True)
        added_tracks = [
            track for track in tracks if track.bvid not in self.search_result_bvids
        ]
        self._append_search_results(added_tracks, page)
        self.search_result_bvids.update(track.bvid for track in added_tracks)
        self.next_search_page = page + 1
        self.search_has_more = len(tracks) >= 30 and bool(added_tracks)
        self.search_loading = False
        logging.getLogger("echoplay.search").info(
            "Search page %d returned %d result(s), added %d",
            page, len(tracks), len(added_tracks),
        )
        if page == 1:
            self.search_status.setText(
                f"找到 {len(added_tracks)} 条结果。"
                if not self.search_has_more
                else f"已找到 {len(added_tracks)} 条结果，滚动到底部加载更多。"
            )
        elif self.search_has_more:
            self.search_status.setText(
                f"已加载到第 {page} 页，共显示 {len(self.search_result_bvids)} 条；滚动到底部加载更多。"
            )
        else:
            self.search_status.setText(
                f"已加载全部结果，共 {len(self.search_result_bvids)} 条。"
            )

    def _search_page_failed(self, message: str, page: int, generation: int):
        if generation != self.search_generation:
            return
        self.search_status.show()
        self._sync_search_sheet_scroll()
        self.search_loading = False
        self.search_button.setEnabled(True)
        logging.getLogger("echoplay.search").error(
            "Search page %d failed: %s", page, message
        )
        if page == 1:
            self.search_has_more = False
            self.search_status.setText(f"搜索失败：{message}")
            QMessageBox.warning(self, "搜索失败", message)
        else:
            self.search_status.setText(
                f"第 {page} 页加载失败，滚动到底部可重试：{message}"
            )

    def _maybe_load_next_search_page(self, value: int):
        if (
            self.search_keyword is None
            or not self.search_has_more
            or self.search_loading
        ):
            return
        scrollbar = self.results.verticalScrollBar()
        if scrollbar.maximum() == 0 or value < scrollbar.maximum():
            return
        keyword = self.search_keyword
        page = self.next_search_page
        generation = self.search_generation
        self.search_loading = True
        self.search_status.setText(f"正在加载第 {page} 页搜索结果…")
        worker = Worker(
            search_videos,
            keyword,
            30,
            dict(self.bilibili_cookies),
            page,
        )
        worker.signals.succeeded.connect(
            lambda tracks, requested_page=page, current_generation=generation:
                self._search_page_succeeded(
                    tracks, requested_page, current_generation
                )
        )
        worker.signals.failed.connect(
            lambda message, requested_page=page, current_generation=generation:
                self._search_page_failed(
                    message, requested_page, current_generation
                )
        )
        self._start_worker(worker)

    def _append_search_marker(self, text: str):
        marker = QListWidgetItem(text)
        marker.setFlags(Qt.ItemFlag.NoItemFlags)
        marker.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        marker.setSizeHint(QSize(0, ExcelSheetStack.cell_height))
        self.results.addItem(marker)

    def _append_search_results(self, tracks: list[Track], page: int):
        if tracks and page:
            self._append_search_marker(f"— 第 {page} 页 —")
        saved_ids = self.store.saved_track_ids()
        for track in tracks:
            item = QListWidgetItem()
            item.setSizeHint(
                QSize(
                    0,
                    2 * ExcelSheetStack.cell_height
                    if self.skin == "excel"
                    else 80,
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, track)
            self.results.addItem(item)

            container = SearchResultContainer()
            container.setObjectName("searchResultContainer")
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(0 if self.skin == "excel" else 6)
            row = SearchResultRow(container)
            row.setObjectName("searchResultRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 6, 8, 6)
            indicator = QFrame()
            indicator.setFixedSize(3, 42)
            indicator.setStyleSheet("background-color: transparent;")
            self.search_result_play_indicators[id(item)] = indicator
            cover = QLabel("加载封面")
            cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            cover.setFixedSize(104, 58)
            cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cover.setObjectName("coverPlaceholder")
            self.cover_labels.setdefault(track.bvid, []).append(cover)
            info_layout = QVBoxLayout()
            title_row = QHBoxLayout()
            title = QLabel(track.title)
            title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            title.setWordWrap(True)
            title.setStyleSheet("font-weight: 600;")
            collection_badge = self._make_collection_badge(row)
            self.search_result_collection_badges[id(item)] = collection_badge
            title_row.addWidget(title, 1)
            title_row.addWidget(
                collection_badge, 0, Qt.AlignmentFlag.AlignVCenter
            )
            info_layout.addLayout(title_row)
            metadata = QLabel(f"{track.author}  ·  {track.duration}")
            metadata.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            metadata.setObjectName("mutedText")
            info_layout.addWidget(metadata)
            add_button = AddToLibraryButton("+")
            add_button.set_in_library(track.track_id in saved_ids)
            add_button.clicked.connect(lambda checked=False, item=track: self.add_to_library(item))
            row_layout.addWidget(indicator)
            row_layout.addWidget(cover)
            row_layout.addLayout(info_layout, 1)
            row_layout.addWidget(add_button)
            row.setProperty("searchResultItem", id(item))
            self.search_result_row_items[id(item)] = item
            page_panel = SearchResultDetails(container)
            page_panel.setVisible(False)
            page_layout = QVBoxLayout(page_panel)
            page_layout.setContentsMargins(144, 0, 8, 8)
            page_list = SmoothScrollListWidget()
            page_list.setObjectName("searchResultPages")
            page_list.verticalScrollBar().valueChanged.connect(
                lambda _value, panel=page_panel: panel.update()
            )
            page_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            page_list.setIconSize(QSize(16, 16))
            page_list.setVerticalScrollMode(
                QAbstractItemView.ScrollMode.ScrollPerPixel
            )
            page_list.setMaximumHeight(240)
            page_list.setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu
            )
            page_list.customContextMenuRequested.connect(
                lambda position, page_list=page_list:
                    self.show_search_page_context_menu(page_list, position)
            )
            page_list.itemDoubleClicked.connect(self._play_search_page)
            page_layout.addWidget(page_list)
            container_layout.addWidget(row)
            container_layout.addWidget(page_panel)
            row.bind_controls(self.excel_search_view, cover, title, metadata,
                              collection_badge, indicator, add_button, page_panel)
            row.set_excel(self.skin == "excel")
            container.set_excel(self.skin == "excel", self._theme_colors()["border"])
            self.search_result_containers[id(item)] = container
            self.search_result_page_panels[id(item)] = page_panel
            self.search_result_page_lists[id(item)] = page_list
            row.installEventFilter(self)
            row.setMouseTracking(True)
            for child in row.findChildren(QWidget):
                child.installEventFilter(self)
                child.setMouseTracking(True)
            self.results.setItemWidget(item, container)
            self._update_search_result_item_size(item)

            if track.cover_url:
                image_data = cached_cover(track.cover_url)
                if image_data is not None:
                    self._cover_loaded((track.bvid, image_data))
                elif track.bvid not in self._cover_requests:
                    self._cover_requests.add(track.bvid)
                    worker = Worker(fetch_cover, track)
                    worker.signals.succeeded.connect(
                        lambda result, epoch=self._result_epoch: self._cover_loaded(result, epoch))
                    self._start_worker(worker)
            else:
                cover.setText("\u6682\u65e0\u5c01\u9762")
            self._prefetch_search_result_pages(item)
        self.search_status.setVisible(self.skin != "excel" or not self.search_result_row_items)
        self.results.viewport().update()
        self._sync_search_sheet_scroll()
        self._update_library_playing_indicator()

    @staticmethod
    def _make_collection_badge(parent: QWidget) -> QLabel:
        badge = QLabel("合集", parent)
        badge.hide()
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setObjectName("collectionBadge")
        return badge

    def _cover_loaded(self, result: tuple[str, bytes], epoch=None):
        if epoch is not None and epoch != self._result_epoch:
            return
        bvid, image_data = result
        covers = self.cover_labels.get(bvid, [])
        pixmap = QPixmap()
        valid = bool(image_data) and pixmap.loadFromData(image_data)
        for cover in covers:
            if valid:
                cover.setText("")
                cover.source_pixmap = pixmap
                cover.setPixmap(pixmap.scaled(
                    cover.size(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
            else:
                cover.setText("\u5c01\u9762\u52a0\u8f7d\u5931\u8d25")

    def play_search_result(self, item: QListWidgetItem):
        self._request_play_track(item.data(Qt.ItemDataRole.UserRole), record_history=True)

    def _prefetch_search_result_pages(self, item: QListWidgetItem):
        item_id = id(item)
        if (
            item_id in self.search_result_pages_pending
            or item_id in self.search_result_pages
        ):
            return
        self.search_result_pages_pending.add(item_id)
        self.search_result_pages_queue.append(item)
        self._start_next_search_page_prefetch()

    def _start_next_search_page_prefetch(self):
        while (
            self.search_result_pages_queue
            and len(self.search_result_pages_active) < 4
        ):
            item = self.search_result_pages_queue.pop(0)
            item_id = id(item)
            if item_id not in self.search_result_page_panels:
                self.search_result_pages_pending.discard(item_id)
                continue
            self.search_result_pages_active.add(item_id)
            worker = Worker(
                fetch_video_pages, item.data(Qt.ItemDataRole.UserRole)
            )
            worker.result_epoch = self._result_epoch
            worker.signals.succeeded.connect(
                lambda pages, result_item=item:
                    self._search_result_pages_prefetched(result_item, pages)
            )
            worker.signals.failed.connect(
                lambda message, result_item=item:
                    self._search_result_pages_prefetch_failed(
                        result_item, message
                    )
            )
            self._start_worker(worker)

    def _search_result_pages_prefetched(
        self, item: QListWidgetItem, pages: list[Track]
    ):
        item_id = id(item)
        self.search_result_pages_active.discard(item_id)
        self.search_result_pages_pending.discard(item_id)
        if item_id not in self.search_result_page_panels:
            self._start_next_search_page_prefetch()
            return
        self.search_result_pages[item_id] = pages
        collection_badge = self.search_result_collection_badges.get(item_id)
        if collection_badge is not None:
            collection_badge.setProperty("hasPages", len(pages) > 1)
            collection_badge.setVisible(len(pages) > 1)
        self._update_library_playing_indicator()
        self._start_next_search_page_prefetch()

    def _search_result_pages_prefetch_failed(
        self, item: QListWidgetItem, message: str
    ):
        item_id = id(item)
        self.search_result_pages_active.discard(item_id)
        self.search_result_pages_pending.discard(item_id)
        if item_id not in self.search_result_page_panels:
            self._start_next_search_page_prefetch()
            return
        track = item.data(Qt.ItemDataRole.UserRole)
        logging.getLogger("echoplay.search").warning(
            "Could not prefetch pages for search result %s: %s",
            track.bvid,
            message,
        )
        self._start_next_search_page_prefetch()

    def toggle_search_result_pages(self, item: QListWidgetItem):
        item_id = id(item)
        page_panel = self.search_result_page_panels.get(item_id)
        if page_panel is None:
            return
        pages = self.search_result_pages.get(item_id)
        if pages is not None:
            if len(pages) <= 1:
                return
            if not page_panel.isHidden():
                page_panel.hide()
                self._update_search_result_item_size(item)
                return
            page_panel.show()
            self._populate_search_result_pages(item, pages)
            self._update_search_result_item_size(item)
            return
        if item_id in self.search_result_pages_pending:
            return
        self.search_result_pages_pending.add(item_id)
        page_list = self.search_result_page_lists[item_id]
        page_list.clear()
        loading = QListWidgetItem("正在加载分 P…")
        loading.setFlags(Qt.ItemFlag.NoItemFlags)
        page_list.addItem(loading)
        page_list.setFixedHeight(48)
        worker = Worker(fetch_video_pages, item.data(Qt.ItemDataRole.UserRole))
        worker.signals.succeeded.connect(
            lambda pages, result_item=item: self._search_result_pages_loaded(
                result_item, pages
            )
        )
        worker.signals.failed.connect(
            lambda message, result_item=item: self._search_result_pages_failed(
                result_item, message
            )
        )
        self._start_worker(worker)

    @staticmethod
    def _expanded_collection_height(container):
        """Measure visible details without relying on a hidden panel's cached hint."""
        layout = container.layout()
        if layout is None or layout.count() < 2:
            return 0
        row = layout.itemAt(0).widget()
        panel = layout.itemAt(1).widget()
        details = panel.findChild(QListWidget) if panel is not None else None
        if row is None or panel is None or panel.isHidden() or details is None:
            return 0
        margins = layout.contentsMargins()
        row_width = max(1, container.width() - margins.left() - margins.right())
        # A wrapped title's unconstrained hint can be far taller than the row
        # at its actual width. Counting it leaves empty space around details.
        row_height = (row.heightForWidth(row_width) if row.hasHeightForWidth()
                      else row.sizeHint().height())
        row_height = max(row.minimumSizeHint().height(), row_height)
        panel_margins = panel.layout().contentsMargins()
        return (row_height + details.height()
                + panel_margins.top() + panel_margins.bottom()
                + layout.spacing() + margins.top() + margins.bottom())

    def _update_search_result_item_size(self, item: QListWidgetItem):
        container = self.search_result_containers.get(id(item))
        if container is None:
            return
        layout = container.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        minimum_height = (
            2 * ExcelSheetStack.cell_height if self.skin == "excel" else 80
        )
        # QListWidget owns the row width. adjustSize() would shrink it to the
        # content's preferred width, even when the height hint stays unchanged.
        content_height = (
            layout.totalHeightForWidth(container.width())
            if layout is not None and layout.hasHeightForWidth()
            else container.sizeHint().height()
        )
        height = max(minimum_height, content_height)
        if self.skin != "excel":
            height = max(height, self._expanded_collection_height(container))
        if self.skin == "excel":
            height = 2 * ExcelSheetStack.cell_height
            panel = self.search_result_page_panels.get(id(item))
            details = self.search_result_page_lists.get(id(item))
            if panel is not None and not panel.isHidden() and details is not None:
                height += details.height()
        size = QSize(0, height)
        if item.sizeHint() != size:
            item.setSizeHint(size)

    def _search_result_pages_loaded(self, item: QListWidgetItem, pages: list[Track]):
        item_id = id(item)
        self.search_result_pages_pending.discard(item_id)
        if item_id not in self.search_result_page_panels:
            return
        self.search_result_pages[item_id] = pages
        collection_badge = self.search_result_collection_badges.get(item_id)
        if collection_badge is not None:
            collection_badge.setProperty("hasPages", len(pages) > 1)
            collection_badge.setVisible(len(pages) > 1)
        if len(pages) > 1:
            self.search_result_page_panels[item_id].show()
            self._populate_search_result_pages(item, pages)

    def _populate_search_result_pages(self, item: QListWidgetItem, pages: list[Track]):
        item_id = id(item)
        panel = self.search_result_page_panels.get(item_id)
        page_list = self.search_result_page_lists.get(item_id)
        if panel is None or page_list is None:
            return
        page_list.clear()
        for page in pages:
            page_title = page.part_title or f"P{page.page_number}"
            page_item = QListWidgetItem(page_title)
            page_item.setData(Qt.ItemDataRole.UserRole, page)
            if self.skin == "excel":
                page_item.setSizeHint(QSize(0, ExcelSheetStack.cell_height))
            page_list.addItem(page_item)
        self._size_collection_page_list(page_list)
        self._update_library_playing_indicator()
        if not panel.isHidden():
            self._update_search_result_item_size(item)

    def _size_collection_page_list(self, page_list):
        page_list.ensurePolished()
        page_list.viewport().ensurePolished()
        page_list.doItemsLayout()
        excel = self.skin == "excel"
        row_height = 28 if excel else 32
        for index in range(page_list.count()):
            page_list.item(index).setSizeHint(QSize(0, row_height))
        count = max(page_list.count(), int(page_list.property("collectionPageCount") or 0))
        page_list.setFixedHeight(
            min(224, max(28, 28 * count)) if excel
            else min(240, max(48, (row_height + 1) * count + 4))
        )

    def _play_search_page(self, item: QListWidgetItem):
        self._request_play_track(item.data(Qt.ItemDataRole.UserRole), record_history=True)

    def show_search_page_context_menu(self, page_list: QListWidget, position):
        page_item = page_list.itemAt(position)
        if page_item is None:
            return
        page = page_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(page, Track):
            return
        parent_item = next(
            (
                self.search_result_row_items[item_id]
                for item_id, candidate_list in self.search_result_page_lists.items()
                if candidate_list is page_list
                and item_id in self.search_result_row_items
            ),
            None,
        )
        if parent_item is None:
            return
        parent_track = parent_item.data(Qt.ItemDataRole.UserRole)
        part_title = page.part_title or f"P{page.page_number}"
        menu = QMenu(self)
        add_action = menu.addAction("单独添加该分P到曲库")
        chosen = menu.exec(page_list.mapToGlobal(position))
        if chosen == add_action:
            library_track = Track(
                bvid=page.bvid,
                title=f"{parent_track.title}-{part_title}",
                author=page.author,
                duration=page.duration,
                cover_url=page.cover_url,
                page_url=page.page_url,
                page_number=page.page_number,
                part_title=page.part_title,
                is_standalone=True,
            )
            self.add_to_library(library_track)
            self.search_status.setText(f"已加入曲库：{library_track.title}")

    def _search_result_pages_failed(self, item: QListWidgetItem, message: str):
        item_id = id(item)
        self.search_result_pages_pending.discard(item_id)
        panel = self.search_result_page_panels.get(item_id)
        if panel is None:
            return
        logging.getLogger("echoplay.search").error(
            "Could not load video pages for %s: %s",
            item.data(Qt.ItemDataRole.UserRole).bvid,
            message,
        )
        page_list = self.search_result_page_lists.get(item_id)
        if page_list is not None:
            page_list.clear()
            error_item = QListWidgetItem(f"加载分 P 失败：{message}")
            error_item.setFlags(Qt.ItemFlag.NoItemFlags)
            page_list.addItem(error_item)
            page_list.setFixedHeight(48)
            panel.show()
        self._update_search_result_item_size(item)

    def add_to_library(self, track: Track):
        should_fetch_collection_pages = (
            not track.is_standalone and track.page_number == 1
        )
        selected_playlist_id = self.selected_library_playlist_id
        try:
            if not track.is_standalone and track.page_number == 1:
                self.store.clear_removed_pages(track.bvid)
            self.store.add(track)
        except sqlite3.Error:
            logging.getLogger("echoplay.library").exception(
                "Could not add track %s to the library", track.bvid
            )
            self._notify_library_change(f"添加失败：{track.title}")
            return
        added_to_playlist = None
        if selected_playlist_id is not None:
            try:
                added_to_playlist = self.store.add_track_to_playlist(
                    selected_playlist_id, track
                )
            except sqlite3.Error:
                logging.getLogger("echoplay.library").exception(
                    "Could not add track %s to playlist %d",
                    track.bvid,
                    selected_playlist_id,
                )
                self.refresh_library()
                self._notify_library_change(
                    f"已添加到曲库，但加入当前歌单失败：{track.title}"
                )
                return
        if not track.is_standalone and track.page_number == 1:
            self.library_pages_by_bvid.pop(track.bvid, None)
            self.library_pages_pending.discard(track.bvid)
        self.refresh_library()
        if selected_playlist_id is None:
            message = f"已添加到曲库：{track.title}"
        elif added_to_playlist:
            message = f"已添加到曲库和当前歌单：{track.title}"
        else:
            message = f"已添加到曲库（已在当前歌单中）：{track.title}"
        self._notify_library_change(message)
        if should_fetch_collection_pages:
            self._prefetch_library_collection_pages(track)

    def _prefetch_library_collection_pages(self, track: Track):
        if track.bvid in self.library_pages_pending:
            return
        self.library_pages_pending.add(track.bvid)
        worker = Worker(fetch_video_pages, track)
        worker.signals.succeeded.connect(
            lambda pages, bvid=track.bvid:
                self._library_collection_pages_prefetched(bvid, pages)
        )
        worker.signals.failed.connect(
            lambda message, bvid=track.bvid:
                self._library_collection_pages_prefetch_failed(bvid, message)
        )
        self._start_worker(worker)

    def _library_collection_pages_prefetched(
        self, bvid: str, fetched_pages: list[Track]
    ):
        self.library_pages_pending.discard(bvid)
        if not any(
            track.bvid == bvid and not track.is_standalone
            for track in self.store.list_tracks()
        ):
            return
        pages = self._merge_library_pages(bvid, fetched_pages)
        self._sync_search_library_buttons()
        self.library_pages_by_bvid[bvid] = pages
        for item_id, item in self.library_row_items.items():
            parent_track = item.data(Qt.ItemDataRole.UserRole)
            if parent_track.bvid != bvid or parent_track.is_standalone:
                continue
            self.library_tracks_by_item[item_id] = pages
            badge = self.library_collection_badges.get(item_id)
            if badge is not None:
                badge.setVisible(len(pages) > 1)
            if len(pages) > 1:
                parent_track = replace(
                    pages[0],
                    title=self._aggregate_video_title(pages[0]),
                )
                self._set_library_parent_track(item, parent_track)
            break
        self._update_track_navigation()

    def _library_collection_pages_prefetch_failed(
        self, bvid: str, message: str
    ):
        self.library_pages_pending.discard(bvid)
        logging.getLogger("echoplay.library").warning(
            "Could not fetch collection pages for %s after adding it: %s",
            bvid,
            message,
        )

    def _sync_search_library_buttons(self):
        if not self.search_result_row_items:
            return
        saved_ids = self.store.saved_track_ids()
        for item_id, item in self.search_result_row_items.items():
            track = item.data(Qt.ItemDataRole.UserRole)
            container = self.search_result_containers.get(item_id)
            button = container.findChild(AddToLibraryButton) if container is not None else None
            if button is not None:
                button.set_in_library(track.track_id in saved_ids)

    def refresh_library(self):
        self._sync_search_library_buttons()
        self.library_click_timer.stop()
        self.pending_library_click = None
        if not hasattr(self, "library_items"):
            return
        self._library_filter_timer.stop()
        self._set_hovered_library_row(None)
        self.library_items.clear()
        self.library_row_items.clear()
        self.library_page_panels.clear()
        self.library_page_lists.clear()
        self.library_tracks_by_item.clear()
        self.library_title_labels.clear()
        self.library_author_labels.clear()
        self.library_track_inner_layouts.clear()
        self.library_track_inner_layout_defaults.clear()
        self.library_row_layout_spacings.clear()
        self.library_play_indicators.clear()
        self.library_collection_badges.clear()
        query = self.library_filter.text().strip().casefold()
        # Group before filtering so a match in just one part retains the
        # collection's original title and its full saved-page identity.
        tracks = self._list_current_library_tracks()
        library_rows = group_library_tracks(tracks)
        if query:
            library_rows = [row for row in library_rows if any(
                query in value.casefold()
                for page in row[1]
                for value in (page.title, page.author, page.video_title, page.part_title)
            )]
        for row_index, (parent_track, video_tracks, is_standalone) in enumerate(library_rows):
            bvid = parent_track.bvid
            if not is_standalone:
                video_tracks.sort(key=lambda track: track.page_number)
                parent_track = next(
                    (track for track in video_tracks if track.page_number == 1),
                    video_tracks[0],
                )
                if len(video_tracks) > 1:
                    parent_track = replace(
                        parent_track,
                        title=self._aggregate_video_title(parent_track),
                    )
                elif (
                    parent_track.part_title
                    or self.store.list_removed_pages(bvid)
                ):
                    parent_track = replace(
                        parent_track,
                        title=self._single_page_title(parent_track),
                    )
                elif parent_track.video_title:
                    parent_track = replace(
                        parent_track, title=parent_track.video_title
                    )
                library_rows[row_index] = (parent_track, video_tracks, False)
        library_rows = self._sort_library_items(
            library_rows, lambda row: row[0].title
        )
        for row_index, (parent_track, video_tracks, is_standalone) in enumerate(library_rows):
            bvid = parent_track.bvid
            item = QListWidgetItem()
            item.setSizeHint(
                QSize(
                    0,
                    56 if self.skin == "excel" else 64,
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, parent_track)
            self.library_items.addItem(item)

            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            row = QFrame()
            row.setObjectName("libraryTrackRow")
            row_layout = QHBoxLayout(row)
            self.library_row_layout_spacings[id(item)] = row_layout.spacing()
            row_layout.setContentsMargins(
                0 if self.skin == "excel" else 10,
                0 if self.skin == "excel" else 4,
                0 if self.skin == "excel" else 8,
                0 if self.skin == "excel" else 4,
            )
            if self.skin == "excel":
                row_layout.setSpacing(0)
            indicator = QFrame()
            indicator.setFixedSize(3, 48)
            indicator.setStyleSheet("background-color: transparent;")
            self.library_play_indicators[id(item)] = indicator
            row_layout.addWidget(indicator)
            info = QVBoxLayout()
            title = QLabel(parent_track.title)
            title.setObjectName("libraryTrackTitle")
            self.library_title_labels[id(item)] = title
            title.setStyleSheet("font-weight: 600;")
            title.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            title_row = QHBoxLayout()
            title_row.addWidget(title, 1)
            collection_badge = self._make_collection_badge(row)
            self.library_collection_badges[id(item)] = collection_badge
            title_row.addWidget(collection_badge, 0, Qt.AlignmentFlag.AlignVCenter)
            collection_badge.setVisible(
                not is_standalone and len(video_tracks) > 1
            )
            self.library_track_inner_layouts[id(item)] = (
                info,
                title_row,
            )
            self.library_track_inner_layout_defaults[id(item)] = tuple(
                (
                    margins.left(),
                    margins.top(),
                    margins.right(),
                    margins.bottom(),
                    layout.spacing(),
                )
                for layout in (info, title_row)
                for margins in (layout.contentsMargins(),)
            )
            if self.skin == "excel":
                info.setContentsMargins(0, 0, 0, 0)
                info.setSpacing(0)
                title_row.setContentsMargins(0, 0, 0, 0)
                title_row.setSpacing(0)
                title.setFixedHeight(ExcelSheetStack.cell_height - 1)
            info.addLayout(title_row)
            author = QLabel(f"{parent_track.author}  ·  {parent_track.duration}")
            author.setObjectName("mutedText")
            author.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            if self.skin == "excel":
                author.setFixedHeight(ExcelSheetStack.cell_height - 1)
            self.library_author_labels[id(item)] = author
            info.addWidget(author)
            info.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            row_layout.addLayout(info, 1)
            row.setProperty("libraryItem", id(item))
            self.library_row_items[id(item)] = item
            page_panel = SearchResultDetails(container)
            page_panel.setVisible(False)
            page_layout = QVBoxLayout(page_panel)
            page_layout.setContentsMargins(144, 0, 8, 8)
            page_list = ExcelCollectionPageListWidget()
            page_list.sheet_reference = self.library_excel_view
            page_list.setObjectName("libraryResultPages")
            page_list.verticalScrollBar().valueChanged.connect(
                lambda _value, panel=page_panel: panel.update()
            )
            page_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            page_list.setIconSize(QSize(16, 16))
            page_list.setVerticalScrollMode(
                QAbstractItemView.ScrollMode.ScrollPerPixel
            )
            page_list.setMaximumHeight(240)
            page_list.setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu
            )
            page_list.customContextMenuRequested.connect(
                lambda position, page_list=page_list:
                    self.show_library_page_context_menu(page_list, position)
            )
            page_list.itemDoubleClicked.connect(self._play_library_page)
            page_layout.addWidget(page_list)
            container_layout.addWidget(row)
            container_layout.addWidget(page_panel)
            self.library_items.setItemWidget(item, container)
            self._update_library_item_size(item)
            self.library_page_panels[id(item)] = page_panel
            self.library_page_lists[id(item)] = page_list
            self.library_tracks_by_item[id(item)] = video_tracks
            row.installEventFilter(self)
            for child in row.findChildren(QWidget):
                child.installEventFilter(self)
            if not is_standalone and bvid in self.library_pages_by_bvid:
                cached_pages = self._merge_library_pages(
                    bvid, self.library_pages_by_bvid[bvid]
                )
                self.library_pages_by_bvid[bvid] = cached_pages
                if len(cached_pages) > 1:
                    self._populate_library_pages(item, cached_pages)
            if (query and not is_standalone and len(video_tracks) > 1
                    and any(query in (page.part_title or page.title).casefold()
                            for page in video_tracks)):
                page_panel.show()
                self._populate_library_pages(item, video_tracks)
                self._update_library_item_size(item)
        self._update_library_count()
        self._update_library_playing_indicator()

    def _update_library_count(self):
        count = self.store.count_tracks(self.selected_library_playlist_id)
        if self.selected_library_playlist_id is None:
            self.library_status.setText(f"曲库音频总数：{count}")
        else:
            self.library_status.setText(f"当前歌单音频数：{count}")

    def _sync_playback_playlist(self):
        query = self.library_filter.text() if hasattr(self, "library_filter") else ""
        self.playlist = self._playback_tracks_in_library_order(
            self._list_current_library_tracks(query)
        )
        self._update_track_navigation()

    def _list_current_library_tracks(self, query: str = "") -> list[Track]:
        if self.selected_library_playlist_id is None:
            tracks = self.store.list_tracks(query)
        else:
            tracks = self.store.list_playlist_tracks(
                self.selected_library_playlist_id, query
            )
        return self._sort_library_items(tracks, lambda track: track.title)

    def _playback_tracks_in_library_order(
        self, tracks: list[Track]
    ) -> list[Track]:
        library_rows = group_library_tracks(tracks)

        for row_index, (parent_track, video_tracks, is_standalone) in enumerate(
            library_rows
        ):
            if is_standalone:
                continue
            video_tracks.sort(key=lambda track: track.page_number)
            parent_track = next(
                (track for track in video_tracks if track.page_number == 1),
                video_tracks[0],
            )
            if len(video_tracks) > 1:
                parent_track = replace(
                    parent_track,
                    title=self._aggregate_video_title(parent_track),
                )
            elif (
                parent_track.part_title
                or self.store.list_removed_pages(parent_track.bvid)
            ):
                parent_track = replace(
                    parent_track,
                    title=self._single_page_title(parent_track),
                )
            elif parent_track.video_title:
                parent_track = replace(
                    parent_track, title=parent_track.video_title
                )
            library_rows[row_index] = (
                parent_track,
                video_tracks,
                False,
            )

        library_rows = self._sort_library_items(
            library_rows, lambda row: row[0].title
        )
        return [
            track
            for _parent_track, video_tracks, _is_standalone in library_rows
            for track in video_tracks
        ]

    @staticmethod
    def _library_title_sort_group(title: str) -> int:
        initial = next(
            (character for character in title if character.isalnum()), ""
        )
        if initial.isascii() and initial.isalpha():
            return 0
        if initial and not initial.isascii() and initial.isalpha():
            return 1
        return 2

    @classmethod
    def _sort_library_items(cls, items, get_title):
        collator = QCollator(QLocale("zh_CN"))
        collator.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        def compare(left, right):
            left_title = get_title(left)
            right_title = get_title(right)
            left_group = cls._library_title_sort_group(left_title)
            right_group = cls._library_title_sort_group(right_title)
            if left_group != right_group:
                return left_group - right_group
            return collator.compare(left_title, right_title)

        return sorted(items, key=cmp_to_key(compare))

    def _set_hovered_library_row(self, row: QFrame | None):
        if self.hovered_library_row is row:
            return
        if self.hovered_library_row is not None:
            self.hovered_library_row.setStyleSheet("")
        self.hovered_library_row = row
        if row is not None:
            hover_color = (
                self._theme_colors()["hover"]
                if self.skin == "excel"
                else "rgba(128, 128, 128, 32)"
            )
            row.setStyleSheet(
                "QFrame#libraryTrackRow { "
                f"background-color: {hover_color}; }}"
            )

    def _library_row_at_global_position(self, position) -> QFrame | None:
        viewport_position = self.library_items.viewport().mapFromGlobal(position)
        item = self.library_items.itemAt(viewport_position)
        if item is None:
            return None
        container = self.library_items.itemWidget(item)
        return (
            container.findChild(QFrame, "libraryTrackRow")
            if container is not None
            else None
        )

    @staticmethod
    def _library_row_for_widget(widget) -> QFrame | None:
        while widget is not None:
            if isinstance(widget, QFrame) and widget.objectName() == "libraryTrackRow":
                return widget
            widget = widget.parentWidget()
        return None

    def toggle_library_pages(self, item: QListWidgetItem):
        track = item.data(Qt.ItemDataRole.UserRole)
        if track.is_standalone:
            return
        item_id = id(item)
        panel = self.library_page_panels.get(item_id)
        if panel is None:
            return
        stored_pages = self.library_tracks_by_item.get(item_id, [])
        removed_pages = self.store.list_removed_pages(track.bvid)
        if len(stored_pages) <= 1 and removed_pages:
            return
        cached_pages = self.library_pages_by_bvid.get(track.bvid)
        if cached_pages is None and len(stored_pages) > 1:
            cached_pages = stored_pages
            self.library_pages_by_bvid[track.bvid] = cached_pages
        if cached_pages is not None:
            if len(cached_pages) <= 1:
                return
            if not panel.isHidden():
                panel.hide()
                self._update_library_item_size(item)
                return
            cached_pages = self._merge_library_pages(track.bvid, cached_pages)
            self.library_pages_by_bvid[track.bvid] = cached_pages
            panel.show()
            self._populate_library_pages(item, cached_pages)
            self._update_library_item_size(item)
            return
        if track.bvid in self.library_pages_pending:
            return
        self.library_pages_pending.add(track.bvid)
        worker = Worker(fetch_video_pages, track)
        worker.signals.succeeded.connect(
            lambda pages, video_bvid=track.bvid, parent_item=item:
                self._library_pages_loaded(video_bvid, parent_item, pages)
        )
        worker.signals.failed.connect(
            lambda message, video_bvid=track.bvid, parent_item=item:
                self._library_pages_failed(video_bvid, parent_item, message)
        )
        self._start_worker(worker)

    def _library_pages_loaded(
        self, bvid: str, item: QListWidgetItem, pages: list[Track]
    ):
        self.library_pages_pending.discard(bvid)
        if id(item) not in self.library_page_lists:
            return
        pages = self._merge_library_pages(bvid, pages)
        self.library_pages_by_bvid[bvid] = pages
        self.library_tracks_by_item[id(item)] = pages
        if len(pages) <= 1:
            if pages:
                self._set_library_parent_track(
                    item, replace(pages[0], title=self._single_page_title(pages[0]))
                )
            panel = self.library_page_panels.get(id(item))
            if panel is not None:
                panel.hide()
            self._update_library_item_size(item)
            return
        self._set_library_parent_track(
            item, replace(pages[0], title=self._aggregate_video_title(pages[0]))
        )
        self.library_page_panels[id(item)].show()
        self._populate_library_pages(item, pages)
        self._update_library_item_size(item)

    def _merge_library_pages(self, bvid: str, fetched_pages: list[Track]) -> list[Track]:
        stored_pages = {
            track.track_id: track
            for track in self.store.list_tracks()
            if track.bvid == bvid and not track.is_standalone
        }
        removed_pages = self.store.list_removed_pages(bvid)
        merged_pages = []
        for page in fetched_pages:
            if page.page_number in removed_pages:
                continue
            stored = stored_pages.get(page.track_id)
            if stored is None:
                self.store.add(page)
                merged_pages.append(page)
                continue
            self.store.set_part_title(bvid, page.part_title, page.page_number)
            self.store.set_video_title(bvid, page.video_title, page.page_number)
            merged_pages.append(
                Track(
                    stored.bvid,
                    (
                        stored.part_title
                        if stored.is_part_title_renamed
                        else stored.title
                    ),
                    stored.author,
                    stored.duration,
                    stored.cover_url,
                    stored.page_url,
                    stored.page_number,
                    (
                        stored.part_title
                        if stored.is_part_title_renamed
                        else page.part_title
                    ),
                    stored.is_standalone,
                    stored.video_title or page.video_title,
                    stored.is_video_title_renamed,
                    stored.is_part_title_renamed,
                )
            )
        return merged_pages

    @staticmethod
    def _aggregate_video_title(track: Track) -> str:
        if track.video_title:
            return track.video_title
        marker = f" - P{track.page_number}"
        return track.title.split(marker, 1)[0] if marker in track.title else track.title

    @classmethod
    def _single_page_title(cls, track: Track) -> str:
        if track.is_video_title_renamed:
            return track.video_title or track.title
        part_title = track.part_title or f"P{track.page_number}"
        return f"{cls._aggregate_video_title(track)}-{part_title}"

    def _set_library_parent_track(
        self, item: QListWidgetItem, track: Track
    ) -> None:
        item.setData(Qt.ItemDataRole.UserRole, track)
        title_label = self.library_title_labels.get(id(item))
        if title_label is not None:
            title_label.setText(track.title)

    def _library_pages_failed(
        self, bvid: str, item: QListWidgetItem, message: str
    ):
        self.library_pages_pending.discard(bvid)
        page_list = self.library_page_lists.get(id(item))
        if page_list is None:
            return
        logging.getLogger("echoplay.library").error(
            "Could not load video pages for %s: %s", bvid, message
        )
        page_list.clear()
        error_item = QListWidgetItem(f"加载分 P 失败：{message}")
        error_item.setFlags(Qt.ItemFlag.NoItemFlags)
        page_list.addItem(error_item)
        page_list.setFixedHeight(48)
        self._update_library_item_size(item)

    def _populate_library_pages(self, item: QListWidgetItem, pages: list[Track]):
        page_list = self.library_page_lists.get(id(item))
        if page_list is None:
            return
        if self.selected_library_playlist_id is not None:
            playlist_track_ids = {
                track.playlist_id
                for track in self.store.list_playlist_tracks(
                    self.selected_library_playlist_id
                )
            }
            pages = [
                page for page in pages if page.playlist_id in playlist_track_ids
            ]
        # Keep the same frame height as the full collection in this playlist;
        # filtering changes its visible contents, not its surrounding layout.
        page_list.setProperty("collectionPageCount", len(pages))
        query = self.library_filter.text().strip().casefold()
        if query:
            pages = [page for page in pages
                     if query in (page.part_title or page.title).casefold()]
        page_list.clear()
        for page in pages:
            page_title = page.part_title or f"P{page.page_number}"
            page_item = QListWidgetItem(page_title)
            page_item.setData(Qt.ItemDataRole.UserRole, page)
            page_list.addItem(page_item)
        self._size_collection_page_list(page_list)
        self._update_library_playing_indicator()

    def _sync_library_column_border(self):
        if not hasattr(self, "library_column_border"):
            return
        rect = self.library_items.viewport().geometry()
        self.library_column_border.setGeometry(rect.x(), rect.y(), 1, rect.height())
        self.library_column_border.setVisible(self.skin == "excel")
        self.library_column_border.raise_()

    def _library_details_indent(self):
        if self.skin != "excel":
            return 144
        width = self.library_excel_layout.cellRect(2, 2).width()
        if width > 0:
            return width
        weights = ExcelSheetStack.library_column_weights
        return max(1, self.page_stack.width() * weights[2] // sum(weights))

    def _set_library_details_margins(self, panel):
        if panel is None or panel.layout() is None:
            return
        edge = 0 if self.skin == "excel" else 8
        panel.layout().setContentsMargins(self._library_details_indent(), 0, edge, edge)
        colors = self._theme_colors()
        panel.setProperty("excelCells", self.skin == "excel")
        panel.setProperty("gridColumns", 1)
        panel.setProperty("gridColor", colors["border"])
        panel.setProperty("sheetColor", colors["panel"])
        details = panel.findChild(ExcelCollectionPageListWidget)
        if details is not None:
            details.setProperty("excelCells", self.skin == "excel")
            details.setProperty("gridColor", colors["border"])
            details.setProperty("sheetColor", colors["panel"])
            details.viewport().update()
        panel.update()

    def _update_library_item_size(self, item: QListWidgetItem):
        container = self.library_items.itemWidget(item)
        if container is None:
            return
        layout = container.layout()
        if layout is not None:
            excel = self.skin == "excel"
            layout.setSpacing(0 if excel else 6)
            row = container.findChild(QFrame, "libraryTrackRow")
            if row is not None:
                row.setMinimumHeight(56 if excel else 0)
                row.setMaximumHeight(56 if excel else 16777215)
            if layout.count() > 1:
                panel = layout.itemAt(1).widget()
                self._set_library_details_margins(panel)
            layout.invalidate()
            layout.activate()
        minimum_height = 56 if self.skin == "excel" else 64
        content_height = (
            layout.totalHeightForWidth(container.width())
            if layout is not None and layout.hasHeightForWidth()
            else container.sizeHint().height()
        )
        if layout is not None:
            content_height = max(content_height, layout.minimumSize().height())
            if self.skin != "excel":
                expanded_height = self._expanded_collection_height(container)
                if expanded_height:
                    # Ignore the hidden panel's stale placeholder size hint.
                    content_height = expanded_height
        if self.skin == "excel":
            # Fixed spreadsheet rows must not rely on a cached sizeHint while
            # the newly populated details list is waiting for Qt's layout pass.
            content_height = 2 * ExcelSheetStack.cell_height
            if layout is not None and layout.count() > 1:
                panel = layout.itemAt(1).widget()
                details = panel.findChild(QListWidget) if panel is not None else None
                if panel is not None and not panel.isHidden() and details is not None:
                    content_height += details.height()
        item.setSizeHint(QSize(0, max(minimum_height, content_height)))

    def _play_library_page(self, page_item: QListWidgetItem):
        self._request_play_track(
            page_item.data(Qt.ItemDataRole.UserRole),
            require_library=True,
            display_title=page_item.text(),
        )

    def show_library_page_context_menu(self, page_list: QListWidget, position):
        page_item = page_list.itemAt(position)
        if page_item is None:
            return
        track = page_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(track, Track):
            return
        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        menu.addSeparator()
        remove_action = menu.addAction("从曲库移除该分P")
        chosen = menu.exec(page_list.mapToGlobal(position))
        if chosen == rename_action:
            self.rename_library_page(track, page_list)
            return
        if chosen != remove_action:
            return
        try:
            self.store.remove_library_page(track.bvid, track.page_number)
        except sqlite3.Error:
            logging.getLogger("echoplay.library").exception(
                "Could not remove page %s:%d from the library",
                track.bvid,
                track.page_number,
            )
            self._notify_library_change(
                f"移除分P失败：{track.part_title or track.title}"
            )
            return
        if (
            self.current_track is not None
            and not self.current_track.is_standalone
            and self.current_track.track_id == track.track_id
        ):
            self.current_track = None
            self.stop_playback()
        self.library_pages_by_bvid[track.bvid] = [
            page
            for page in self.library_pages_by_bvid.get(track.bvid, [])
            if page.page_number != track.page_number
        ]
        parent_item = next(
            (
                item
                for item_id, item in self.library_row_items.items()
                if item.data(Qt.ItemDataRole.UserRole).bvid == track.bvid
            ),
            None,
        )
        if parent_item is not None:
            remaining_pages = self.library_pages_by_bvid[track.bvid]
            collection_badge = self.library_collection_badges.get(id(parent_item))
            if collection_badge is not None:
                collection_badge.setVisible(len(remaining_pages) > 1)
            if len(remaining_pages) > 1:
                parent_track = next(
                    (page for page in remaining_pages if page.page_number == 1),
                    remaining_pages[0],
                )
                self._set_library_parent_track(
                    parent_item,
                    replace(
                        parent_track,
                        title=self._aggregate_video_title(parent_track),
                    ),
                )
            elif remaining_pages:
                self._set_library_parent_track(
                    parent_item,
                    replace(
                        remaining_pages[0],
                        title=self._single_page_title(remaining_pages[0]),
                    ),
                )
            page_list = self.library_page_lists.get(id(parent_item))
            previous_height = page_list.height() if page_list is not None else None
            self._populate_library_pages(parent_item, remaining_pages)
            if len(remaining_pages) > 1 and page_list is not None and previous_height is not None:
                page_list.setFixedHeight(previous_height)
            panel = self.library_page_panels.get(id(parent_item))
            if panel is not None:
                panel.setVisible(len(remaining_pages) > 1)
            if len(remaining_pages) <= 1:
                self._update_library_item_size(parent_item)
        self._update_library_count()
        self._sync_playback_playlist()
        self._notify_library_change(
            f"已从曲库移除分P：{track.part_title or track.title}"
        )

    def rename_library_page(self, track: Track, page_list: QListWidget):
        current_title = track.part_title or f"P{track.page_number}"
        title, accepted = QInputDialog.getText(
            self, "重命名分P", "分P名称：", text=current_title
        )
        title = title.strip()
        if not accepted or not title:
            return
        self.store.rename_library_page(track.bvid, track.page_number, title)
        renamed_track = replace(
            track,
            title=title,
            part_title=title,
            is_part_title_renamed=True,
        )
        for item_id, candidate_list in self.library_page_lists.items():
            if candidate_list is page_list:
                pages = self.library_pages_by_bvid.get(track.bvid, [])
                pages = [
                    renamed_track if page.track_id == track.track_id else page
                    for page in pages
                ]
                self.library_pages_by_bvid[track.bvid] = pages
                parent_item = self.library_row_items.get(item_id)
                if parent_item is not None:
                    self._populate_library_pages(parent_item, pages)
                break
        if (
            self.current_track is not None
            and not self.current_track.is_standalone
            and self.current_track.track_id == track.track_id
        ):
            self.current_track = replace(
                self.current_track,
                title=title,
                part_title=title,
                is_part_title_renamed=True,
            )
            self.current_track_title = title
            self.playback_title.setText(title)

    def event(self, event):
        result = super().event(event)
        if (event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange,
                             QEvent.Type.WindowActivate, QEvent.Type.WindowStateChange)
                and getattr(self, "_taskbar_icon_handle", None)
                and hasattr(self, "_taskbar_icon_refresh_timer")):
            self._taskbar_icon_refresh_timer.start()
        return result

    def eventFilter(self, watched, event):
        if watched is self.search_input:
            if (event.type() == QEvent.Type.MouseButtonPress
                  and event.button() == Qt.MouseButton.LeftButton):
                QTimer.singleShot(0, self._show_recent_searches)
            return super().eventFilter(watched, event)
        if event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Show):
            search_item_id = watched.property("searchResultItem")
            item = self.search_result_row_items.get(search_item_id)
            if item is not None:
                self._update_search_result_item_size(item)
        if watched is self.library_items.viewport():
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                self._sync_library_column_border()
            if event.type() == QEvent.Type.Resize and hasattr(self, "library_excel_layout"):
                for panel in self.library_page_panels.values():
                    self._set_library_details_margins(panel)
            if event.type() == QEvent.Type.MouseMove:
                self._set_hovered_library_row(
                    self._library_row_at_global_position(QCursor.pos())
                )
            elif event.type() == QEvent.Type.Leave:
                self._set_hovered_library_row(None)
        else:
            library_row = self._library_row_for_widget(watched)
            if library_row is not None:
                if event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove):
                    self._set_hovered_library_row(library_row)
                elif event.type() == QEvent.Type.Leave:
                    self._set_hovered_library_row(
                        self._library_row_at_global_position(QCursor.pos())
                    )
        if event.type() == QEvent.Type.MouseButtonDblClick:
            library_item = item_for_widget(watched, "libraryItem", self.library_row_items)
            if library_item is not None and event.button() == Qt.MouseButton.LeftButton:
                self.library_click_timer.stop()
                self.pending_library_click = None
                self.library_items.setCurrentItem(library_item)
                self.play_library_track(library_item)
                return True
        if event.type() == QEvent.Type.MouseButtonDblClick:
            item = item_for_widget(watched, "searchResultItem", self.search_result_row_items)
            if (
                item is not None
                and not isinstance(watched, QPushButton)
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self.search_result_click_timer.stop()
                self.pending_search_result_click = None
                self.play_search_result(item)
                return True
        if event.type() == QEvent.Type.MouseButtonPress and (
            event.button() == Qt.MouseButton.LeftButton
            and not isinstance(watched, QPushButton)
        ):
            item = item_for_widget(watched, "searchResultItem", self.search_result_row_items)
            if item is not None:
                if self.search_result_click_timer.isActive():
                    previous_item = self.pending_search_result_click
                    if previous_item is not None and previous_item is not item:
                        self.search_result_click_timer.stop()
                        self.pending_search_result_click = None
                        self.toggle_search_result_pages(previous_item)
                self.pending_search_result_click = item
                self.search_result_click_timer.start()
                return True
        if event.type() == QEvent.Type.MouseButtonPress and (
            event.button() == Qt.MouseButton.LeftButton
            and not isinstance(watched, QPushButton)
        ):
            library_item = item_for_widget(watched, "libraryItem", self.library_row_items)
            if library_item is not None:
                self.library_items.setCurrentItem(library_item)
                previous_item = self.pending_library_click
                if previous_item is not None and previous_item is not library_item:
                    self.library_click_timer.stop()
                    self._process_pending_library_click()
                track = library_item.data(Qt.ItemDataRole.UserRole)
                if not track.is_standalone and (
                    len(self.library_tracks_by_item.get(id(library_item), [])) > 1
                    or len(self.library_pages_by_bvid.get(track.bvid, [])) > 1
                ):
                    self.pending_library_click = library_item
                    self.library_click_timer.start()
                return True
        if event.type() == QEvent.Type.ContextMenu:
            item = item_for_widget(watched, "libraryItem", self.library_row_items)
            if item is not None:
                self.show_library_context_menu(
                    self.library_items.mapFromGlobal(event.globalPos())
                )
                return True
        return super().eventFilter(watched, event)

    def _process_pending_library_click(self):
        item = self.pending_library_click
        self.pending_library_click = None
        if item is not None and id(item) in self.library_row_items:
            self.toggle_library_pages(item)

    def _process_pending_search_result_click(self):
        item = self.pending_search_result_click
        self.pending_search_result_click = None
        if item is not None and self.results.row(item) >= 0:
            self.toggle_search_result_pages(item)

    def remove_selected(self):
        selected_items = self.library_items.selectedItems()
        if not selected_items:
            return
        removed_count = 0
        failed_count = 0
        for item in selected_items:
            track = item.data(Qt.ItemDataRole.UserRole)
            try:
                if track.is_standalone:
                    self.store.remove(
                        track.bvid, track.page_number, is_standalone=True
                    )
                else:
                    self.store.remove(track.bvid)
                    self.store.clear_removed_pages(track.bvid)
            except sqlite3.Error:
                failed_count += 1
                logging.getLogger("echoplay.library").exception(
                    "Could not remove track %s from the library", track.bvid
                )
                continue
            removed_count += 1
            if not track.is_standalone:
                self.library_pages_by_bvid.pop(track.bvid, None)
                self.library_pages_pending.discard(track.bvid)
        remaining_tracks = self._list_current_library_tracks()
        self.playlist = self._playback_tracks_in_library_order(remaining_tracks)
        self._update_track_navigation()
        self.refresh_library()
        if removed_count:
            message = f"已从曲库删除 {removed_count} 项"
            self._notify_library_change(
                message if not failed_count else f"{message}，{failed_count} 项删除失败"
            )
        elif failed_count:
            self._notify_library_change(
                f"删除失败：{failed_count} 项未能从曲库移除"
            )

    def show_library_context_menu(self, position):
        item = self.library_items.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.library_items.clearSelection()
            item.setSelected(True)
        track = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        play_action = menu.addAction("播放")
        pages_action = None
        if not track.is_standalone:
            page_panel = self.library_page_panels.get(id(item))
            pages_action = menu.addAction(
                "收起分P"
                if page_panel is not None and not page_panel.isHidden()
                else "展开分P"
            )
        rename_action = menu.addAction("重命名")
        menu.addSeparator()
        add_to_playlist_menu = menu.addMenu("添加到歌单")
        playlist_actions = {}
        for playlist_id, name in self.store.list_playlists():
            action = add_to_playlist_menu.addAction(name)
            playlist_actions[action] = playlist_id
        if not playlist_actions:
            no_playlists_action = add_to_playlist_menu.addAction("暂无歌单")
            no_playlists_action.setEnabled(False)
        remove_from_playlist_action = None
        if self.selected_library_playlist_id is not None:
            remove_from_playlist_action = menu.addAction("从当前歌单移除")
        menu.addSeparator()
        remove_action = menu.addAction("从曲库删除")
        menu.addSeparator()
        copy_action = menu.addAction("复制视频链接")
        open_action = menu.addAction("在浏览器中打开视频")
        chosen = menu.exec(self.library_items.mapToGlobal(position))
        if chosen == play_action:
            self.play_library_track(item)
        elif chosen == pages_action:
            self.toggle_library_pages(item)
        elif chosen == rename_action:
            self.rename_track(track)
        elif chosen in playlist_actions:
            self._add_library_track_to_playlist(
                playlist_actions[chosen], track
            )
        elif chosen == remove_from_playlist_action:
            self._remove_selected_from_library_playlist()
        elif chosen == remove_action:
            self.remove_selected()
        elif chosen == open_action:
            QDesktopServices.openUrl(QUrl(track.page_url))
        elif chosen == copy_action:
            QGuiApplication.clipboard().setText(track.page_url)

    def _add_library_track_to_playlist(
        self, playlist_id: int, track: Track
    ):
        tracks = [track]
        if not track.is_standalone:
            collection_tracks = [
                candidate
                for candidate in self.store.list_tracks()
                if candidate.bvid == track.bvid and not candidate.is_standalone
            ]
            if collection_tracks:
                tracks = collection_tracks
        added_count = 0
        try:
            for playlist_track in tracks:
                added_count += self.store.add_track_to_playlist(
                    playlist_id, playlist_track
                )
        except sqlite3.Error as exc:
            logging.getLogger("echoplay.library").exception(
                "Could not add track %s to playlist %d",
                track.bvid,
                playlist_id,
            )
            QMessageBox.warning(self, "添加失败", str(exc))
            return
        if playlist_id == self.selected_library_playlist_id:
            self._sync_playback_playlist()
            self.refresh_library()
        if added_count:
            message = f"已添加 {added_count} 首歌曲到歌单。"
        else:
            message = "所选歌曲已在该歌单中。"
        self._notify_library_change(message)

    def _remove_selected_from_library_playlist(self):
        playlist_id = self.selected_library_playlist_id
        if playlist_id is None:
            return
        selected_items = self.library_items.selectedItems()
        removed_count = 0
        failed_count = 0
        for item in selected_items:
            track = item.data(Qt.ItemDataRole.UserRole)
            try:
                self.store.remove_track_from_playlist(playlist_id, track)
            except sqlite3.Error:
                failed_count += 1
                logging.getLogger("echoplay.library").exception(
                    "Could not remove track %s from playlist %d",
                    track.bvid,
                    playlist_id,
                )
                continue
            removed_count += 1
        if not removed_count and not failed_count:
            return
        self._sync_playback_playlist()
        self.refresh_library()
        message = f"已从歌单移除 {removed_count} 项"
        if failed_count:
            message += f"，{failed_count} 项移除失败"
        self._notify_library_change(message)

    def rename_track(self, track: Track):
        title, accepted = QInputDialog.getText(self, "重命名曲目", "曲目名称：", text=track.title)
        title = title.strip()
        if accepted and title:
            if track.is_standalone:
                self.store.rename(
                    track.bvid, title, track.page_number, is_standalone=True
                )
            else:
                self.store.rename_video(track.bvid, title)
            if self.current_track and self.current_track.playlist_id == track.playlist_id:
                self.current_track = Track(
                    track.bvid, title, track.author, track.duration,
                    track.cover_url, track.page_url, track.page_number,
                    track.part_title, track.is_standalone, track.video_title,
                    not track.is_standalone,
                )
                self.playback_title.setText(title)
            self.refresh_library()

    def _task_failed(self, message: str):
        logging.getLogger("echoplay.ui").error("Background operation failed: %s", message)
        self.search_button.setEnabled(True)
        self.search_status.setText(f"操作失败：{message}")
        QMessageBox.warning(self, "操作失败", message)

