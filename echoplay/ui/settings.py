"""Settings page, quality preferences, shortcuts and taskbar customization."""

from pathlib import Path
import ctypes
import hashlib
import logging
import subprocess
import sys

from PySide6.QtCore import QIODevice, QSaveFile, QSize, Qt
from PySide6.QtGui import QIcon, QImageReader, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QMessageBox,
    QPushButton,
    QKeySequenceEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from echoplay.services.stream_quality import STREAM_QUALITIES, DEFAULT_STREAM_QUALITY
from echoplay.services.taskbar import set_taskbar_identity
from echoplay.ui.icons import (
    _create_windows_icon,
    _set_taskbar_large_icon,
    _asset_path,
    _make_skin_preview_icon,
    _make_excel_skin_preview_icon,
)
from echoplay.ui.widgets import SmoothScrollArea, SettingsDetailLabel
from echoplay.version import VERSION


class SettingsMixin:
    def _make_settings_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)
        settings_content = QWidget()
        settings_content.setObjectName("settingsContent")
        settings_content.setAutoFillBackground(False)
        settings_content_layout = QVBoxLayout(settings_content)
        settings_content_layout.setContentsMargins(18, 8, 18, 8)
        settings_content_layout.setSpacing(0)

        title = QLabel("设置")
        title.setObjectName("settingsTitle")
        subtitle = QLabel("根据你的习惯调整 EchoPlay。")
        subtitle.setObjectName("settingsDescription")
        heading = QHBoxLayout()
        heading.addWidget(title, 0, Qt.AlignmentFlag.AlignBottom)
        heading.addStretch(1)
        heading.addWidget(
            subtitle, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom
        )
        layout.addLayout(heading)

        appearance_section = QWidget()
        appearance_section.setObjectName("settingsSection")
        appearance_section.setAutoFillBackground(False)
        appearance_layout = QVBoxLayout(appearance_section)
        appearance_layout.setContentsMargins(0, 12, 0, 12)
        appearance_layout.setSpacing(10)
        appearance_title = QLabel("外观")
        appearance_title.setObjectName("settingsSectionTitle")
        appearance_description = SettingsDetailLabel(
            "切换亮色或暗色模式，选择舒适的聆听环境。"
        )
        appearance_description.setObjectName("settingsDescription")
        appearance_row = QHBoxLayout()
        appearance_row.addWidget(appearance_description, 1)
        appearance_row.addWidget(self.theme_toggle)
        appearance_layout.addWidget(appearance_title)
        appearance_layout.addLayout(appearance_row)

        account_section = QWidget()
        account_section.setObjectName("settingsSection")
        account_section.setAutoFillBackground(False)
        account_layout = QVBoxLayout(account_section)
        account_layout.setContentsMargins(0, 12, 0, 12)
        account_layout.setSpacing(10)
        account_title = QLabel("账号管理")
        account_title.setObjectName("settingsSectionTitle")
        account_row = QHBoxLayout()
        self.settings_account_label.setObjectName("settingsAccount")
        account_row.addWidget(self.account_avatar)
        account_row.addWidget(self.settings_account_label, 1)
        account_row.addWidget(self.login_button)
        account_row.addWidget(self.logout_button)
        account_layout.addWidget(account_title)
        account_layout.addLayout(account_row)

        settings_content_layout.addWidget(account_section)
        settings_content_layout.addWidget(self._make_settings_separator())
        settings_content_layout.addWidget(self._make_quality_section())
        settings_content_layout.addWidget(self._make_settings_separator())
        settings_content_layout.addWidget(appearance_section)
        settings_content_layout.addWidget(self._make_settings_separator())
        settings_content_layout.addWidget(self._make_shortcuts_section())
        settings_content_layout.addWidget(self._make_settings_separator())
        settings_content_layout.addWidget(self._make_taskbar_icon_section())
        settings_content_layout.addWidget(self._make_settings_separator())
        settings_content_layout.addWidget(self._make_skin_section())
        settings_content_layout.addWidget(self._make_settings_separator())
        settings_content_layout.addWidget(self._make_version_section())
        settings_content_layout.addStretch(1)

        scroll_area = SmoothScrollArea()
        scroll_area.setObjectName("settingsScrollArea")
        scroll_area.setAutoFillBackground(False)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.viewport().setAutoFillBackground(False)
        scroll_area.setWidget(settings_content)
        layout.addWidget(scroll_area, 1)
        return page

    def _make_version_section(self) -> QWidget:
        section = QWidget()
        section.setObjectName("settingsSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(10)
        title = QLabel("版本说明")
        title.setObjectName("settingsSectionTitle")
        description = SettingsDetailLabel(f"当前软件版本：{VERSION}")
        description.setObjectName("settingsDescription")
        layout.addWidget(title)
        layout.addWidget(description)
        return section

    def _make_quality_section(self) -> QWidget:
        section = QWidget()
        section.setObjectName("settingsSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(10)
        title = QLabel("播放品质")
        title.setObjectName("settingsSectionTitle")
        row = QHBoxLayout()
        description = SettingsDetailLabel(
            "360P 获取对应音视频流；1080P 获取最高可用非 HiRes 音轨；"
            "HiRes 优先获取无损音轨，需有效大会员；不可用时自动播放最高可用音质。更改后下次播放生效。"
        )
        description.setObjectName("settingsDescription")
        self.quality_group = QButtonGroup(section)
        self.quality_group.setExclusive(True)
        self.quality_buttons = {}
        for quality in STREAM_QUALITIES:
            button = QPushButton(quality, section)
            button.setObjectName("streamQualityOption")
            button.setCheckable(True)
            if quality == "1080P":
                button.setToolTip("选择最高可用非 HiRes 音轨，包括接口提供的杜比音轨")
            button.setMinimumHeight(38)
            self.quality_group.addButton(button)
            self.quality_buttons[quality] = button
            row.addWidget(button, 1)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addLayout(row)
        self.quality_group.buttonClicked.connect(lambda button: self._set_stream_quality(button.text()))
        self._sync_stream_quality_access()
        return section

    def _sync_stream_quality_access(self):
        if not hasattr(self, "quality_buttons"):
            return
        allowed = bool(self.bilibili_cookies.get("SESSDATA")) and self.account_is_vip
        item = self.quality_buttons["HiRes"]
        item.setEnabled(allowed)
        item.setToolTip("优先选择 HiRes，不可用时自动播放最高可用音质" if allowed else "需登录并确认有效 B 站大会员")
        selected = self._preferred_stream_quality
        if selected == "HiRes" and not allowed:
            selected = DEFAULT_STREAM_QUALITY
        self.quality_buttons[selected].setChecked(True)
        self._apply_stream_quality(selected)

    def _set_stream_quality(self, quality: str):
        if quality not in STREAM_QUALITIES:
            return
        if quality == "HiRes" and not (self.account_is_vip and self.bilibili_cookies.get("SESSDATA")):
            self._sync_stream_quality_access()
            return
        self._preferred_stream_quality = quality
        self.settings.setValue("playback/stream_quality", quality)
        self.quality_buttons[quality].setChecked(True)
        self._apply_stream_quality(quality)

    def _apply_stream_quality(self, quality: str):
        if self.stream_quality == quality:
            return
        self.stream_quality = quality
        if self._next_audio_prefetch is not None:
            self._next_audio_prefetch.cancel()
            self._next_audio_prefetch = None
        self._clear_preloaded_audio()
        if self.active_play_id is not None:
            self._schedule_next_audio_prefetch()

    @staticmethod
    def _make_settings_separator() -> QFrame:
        separator = QFrame()
        separator.setObjectName("settingsSeparator")
        separator.setFixedHeight(1)
        return separator

    def _make_skin_section(self) -> QWidget:
        section = QWidget()
        section.setObjectName("settingsSection")
        section.setAutoFillBackground(False)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 12, 0, 12)
        section_layout.setSpacing(10)

        title = QLabel("皮肤")
        title.setObjectName("settingsSectionTitle")
        description = SettingsDetailLabel("选择默认样式或 Excel 工作簿样式。")
        description.setObjectName("settingsDescription")
        section_layout.addWidget(title)
        section_layout.addWidget(description)

        self.skin_option_group = QButtonGroup(self)
        self.skin_option_group.setExclusive(True)
        self.skin_buttons: dict[str, QToolButton] = {}
        self.default_skin_button = self._make_skin_option(
            "default",
            "默认皮肤",
            "当前样式（亮色与暗色）",
            _make_skin_preview_icon(),
        )
        self.excel_skin_button = self._make_skin_option(
            "excel",
            "Excel",
            "Excel 工作簿样式",
            _make_excel_skin_preview_icon(),
        )
        self.skin_buttons[self.skin].setChecked(True)

        options = QHBoxLayout()
        options.addWidget(self.default_skin_button, 0, Qt.AlignmentFlag.AlignLeft)
        options.addWidget(self.excel_skin_button, 0, Qt.AlignmentFlag.AlignLeft)
        options.addStretch(1)
        section_layout.addLayout(options)
        return section

    def _make_skin_option(
        self, skin: str, label: str, tooltip: str, icon: QIcon
    ) -> QToolButton:
        button = QToolButton()
        button.setObjectName("skinOptionTile")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIcon(icon)
        button.setIconSize(QSize(96, 52))
        button.setText(label)
        button.setToolTip(tooltip)
        button.setAccessibleName(label)
        button.setCheckable(True)
        button.setFixedSize(112, 104)
        button.clicked.connect(
            lambda checked=False, selected_skin=skin: self._set_skin(
                selected_skin
            )
        )
        self.skin_option_group.addButton(button)
        self.skin_buttons[skin] = button
        return button

    def _make_taskbar_icon_section(self) -> QWidget:
        section = QWidget()
        section.setObjectName("settingsSection")
        section.setAutoFillBackground(False)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 12, 0, 12)
        section_layout.setSpacing(10)

        title = QLabel("软件图标")
        title.setObjectName("settingsSectionTitle")
        description = SettingsDetailLabel("选择后重启软件生效")
        description.setObjectName("settingsDescription")
        section_layout.addWidget(title)
        section_layout.addWidget(description)

        self.taskbar_icon_buttons: dict[str, QToolButton] = {}
        self.taskbar_icon_group = QButtonGroup(self)
        self.taskbar_icon_group.setExclusive(True)
        self.taskbar_icon_grid = QHBoxLayout()
        self.taskbar_icon_grid.setContentsMargins(0, 0, 0, 0)
        self.taskbar_icon_grid.setSpacing(10)
        self.taskbar_icon_add_button = QToolButton()
        self.taskbar_icon_add_button.setObjectName("taskbarIconAdd")
        self.taskbar_icon_add_button.setText("+")
        self.taskbar_icon_add_button.setToolTip("上传本地图片作为任务栏图标")
        self.taskbar_icon_add_button.setMinimumWidth(84)
        self.taskbar_icon_add_button.setMaximumWidth(112)
        self.taskbar_icon_add_button.setFixedHeight(104)
        self.taskbar_icon_add_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.taskbar_icon_add_button.clicked.connect(
            self._choose_custom_taskbar_icon
        )
        for source, label, filename in self.TASKBAR_ICON_PRESETS:
            icon = QIcon(str(_asset_path("assets", "taskbar_icons", filename)))
            self._add_taskbar_icon_tile(source, label, icon)

        if self._custom_taskbar_icon_path and Path(
            self._custom_taskbar_icon_path
        ).is_file():
            self._add_custom_taskbar_icon_tile(self._custom_taskbar_icon_path)

        selected_button = self.taskbar_icon_buttons.get(self._taskbar_icon_source)
        if selected_button is None:
            selected_button = self.taskbar_icon_buttons["current"]
        selected_button.setChecked(True)

        self._position_taskbar_icon_tiles()
        section_layout.addLayout(self.taskbar_icon_grid)
        section_layout.setAlignment(
            self.taskbar_icon_grid,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )
        return section

    def _add_taskbar_icon_tile(
        self, source: str, label: str, icon: QIcon
    ) -> QToolButton:
        button = QToolButton()
        button.setObjectName("taskbarIconTile")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIcon(icon)
        button.setIconSize(QSize(48, 48))
        button.setText(label)
        button.setCheckable(True)
        button.setMinimumWidth(84)
        button.setMaximumWidth(112)
        button.setFixedHeight(104)
        button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        button.clicked.connect(
            lambda checked=False, selected_source=source:
                self._select_taskbar_icon(selected_source)
        )
        self.taskbar_icon_group.addButton(button)
        self.taskbar_icon_buttons[source] = button
        self._position_taskbar_icon_tiles()
        return button

    def _add_custom_taskbar_icon_tile(self, image_path: str):
        icon = QIcon(image_path)
        button = self.taskbar_icon_buttons.get("custom")
        if button is None:
            button = self._add_taskbar_icon_tile("custom", "自定义", icon)
        else:
            button.setIcon(icon)
        button.setProperty("iconPath", image_path)
        self._position_taskbar_icon_tiles()

    def _position_taskbar_icon_tiles(self):
        while self.taskbar_icon_grid.count():
            self.taskbar_icon_grid.takeAt(0)
        buttons = list(self.taskbar_icon_buttons.values())
        buttons.append(self.taskbar_icon_add_button)
        for button in buttons:
            self.taskbar_icon_grid.addWidget(button, 1)

    def _make_shortcuts_section(self) -> QWidget:
        section = QWidget()
        section.setObjectName("settingsSection")
        section.setAutoFillBackground(False)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 12, 0, 12)
        section_layout.setSpacing(10)

        heading = QHBoxLayout()
        title = QLabel("快捷键")
        title.setObjectName("settingsSectionTitle")
        reset_button = QPushButton("恢复默认")
        reset_button.clicked.connect(self._reset_shortcuts)
        heading.addWidget(title)
        heading.addStretch(1)
        heading.addWidget(reset_button)
        section_layout.addLayout(heading)

        description = SettingsDetailLabel(
            "支持组合键；仅在 EchoPlay 窗口处于活动状态时生效。"
        )
        description.setObjectName("settingsDescription")
        section_layout.addWidget(description)

        self.shortcut_editors: dict[str, QKeySequenceEdit] = {}
        self._shortcut_sequences: dict[str, QKeySequence] = {}
        self._shortcuts: dict[str, QShortcut] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        for index, (action, label_text) in enumerate(self.SHORTCUT_LABELS.items()):
            field_layout = QVBoxLayout()
            field_layout.setSpacing(4)
            label = QLabel(label_text)
            editor = QKeySequenceEdit()
            editor.setMaximumSequenceLength(1)
            editor.setMinimumWidth(140)
            saved = self.settings.value(
                f"shortcuts/{action}",
                self.SHORTCUT_DEFAULTS[action],
                type=str,
            )
            editor.setKeySequence(QKeySequence(saved))
            editor.keySequenceChanged.connect(
                lambda sequence, shortcut_action=action: self._on_shortcut_changed(
                    shortcut_action, sequence
                )
            )
            field_layout.addWidget(label)
            field_layout.addWidget(editor)
            grid.addLayout(field_layout, index // 2, index % 2)
            self.shortcut_editors[action] = editor
        section_layout.addLayout(grid)
        return section

    def apply_saved_taskbar_icon(self):
        if self._taskbar_icon_source == "custom":
            try:
                self._custom_taskbar_icon_path = self._cache_custom_taskbar_icon(
                    self._custom_taskbar_icon_path
                )
                self.settings.setValue("appearance/custom_taskbar_icon_path", self._custom_taskbar_icon_path)
                self.settings.sync()
                self._add_custom_taskbar_icon_tile(self._custom_taskbar_icon_path)
            except (OSError, ValueError, RuntimeError) as exc:
                logging.getLogger("echoplay.ui").warning("Could not cache saved taskbar icon: %s", exc)
        error = self._apply_taskbar_icon(
            self._taskbar_icon_source, self._custom_taskbar_icon_path
        )
        if error is None:
            logging.getLogger("echoplay.ui").info("Restored taskbar icon: source=%s", self._taskbar_icon_source)
            return

        logging.getLogger("echoplay.ui").error(
            "Could not apply saved taskbar icon: %s", error
        )
        QMessageBox.warning(self, "任务栏图标设置失败", error)
        # A transient startup failure must not erase the user's saved choice.

    def _choose_custom_taskbar_icon(self):
        initial_path = self._custom_taskbar_icon_path
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择任务栏图标图片",
            initial_path,
            "图标图片 (*.ico *.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not file_path:
            return
        self._select_taskbar_icon("custom", file_path)

    def _select_taskbar_icon(self, source: str, image_path: str = ""):
        try:
            if source == "custom":
                button = self.taskbar_icon_buttons.get("custom")
                image_path = image_path or (
                    button.property("iconPath") if button is not None else ""
                ) or self._custom_taskbar_icon_path
                image_path = self._cache_custom_taskbar_icon(image_path)
            error = self._apply_taskbar_icon(source, image_path)
        except (OSError, ValueError, RuntimeError) as exc:
            error = str(exc)
        if error is not None:
            logging.getLogger("echoplay.ui").warning(
                "Could not apply taskbar icon source %s: %s", source, error
            )
            QMessageBox.warning(self, "任务栏图标设置失败", error)
            current_button = self.taskbar_icon_buttons.get(
                self._taskbar_icon_source
            )
            if current_button is not None:
                current_button.setChecked(True)
            return

        self._taskbar_icon_source = source
        self._custom_taskbar_icon_path = image_path if source == "custom" else ""
        if source == "custom":
            self._add_custom_taskbar_icon_tile(image_path)
        self.taskbar_icon_buttons[source].setChecked(True)
        self.settings.setValue("appearance/taskbar_icon_source", source)
        if source == "custom":
            self.settings.setValue(
                "appearance/custom_taskbar_icon_path", image_path
            )
        else:
            self.settings.remove("appearance/custom_taskbar_icon_path")
        self.settings.sync()
        logging.getLogger("echoplay.ui").info("Saved taskbar icon: source=%s", source)

    def _taskbar_shell_metadata(self, image):
        icon = image.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        digest = hashlib.sha256(icon.constBits()).hexdigest()[:24]
        directory = self.store.path.parent / "icons"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"shell-{digest}.ico"
        if not path.exists():
            output = QSaveFile(str(path))
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            if not icon.save(output, "ICO"):
                output.cancelWriting()
                raise OSError("无法保存 Windows 任务栏图标。")
            if not output.commit():
                raise OSError(output.errorString())
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve().parents[2] / "main.py"))
        return str(path), f"EchoPlay.Desktop.{digest}", subprocess.list2cmdline(command)

    def _cache_custom_taskbar_icon(self, image_path: str) -> str:
        image = QImageReader(image_path).read()
        if image.isNull():
            raise ValueError("无法读取自定义图标图片。")
        digest = hashlib.sha256()
        digest.update(f"{image.width()}:{image.height()}:{image.format()}".encode())
        digest.update(image.constBits())
        directory = self.store.path.parent / "icons"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"taskbar-{digest.hexdigest()[:24]}.png"
        output = QSaveFile(str(path))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if not image.save(output, "PNG"):
            output.cancelWriting()
            raise OSError("保存自定义图标失败。")
        if not output.commit():
            raise OSError(output.errorString())
        return str(path)

    def _apply_taskbar_icon(self, source: str, image_path: str = "") -> str | None:
        icon_handle = None
        try:
            if source == "custom":
                reader = QImageReader(image_path)
                image = reader.read()
                if image.isNull():
                    return f"无法读取所选图片：{reader.errorString()}"
            else:
                filename = next(
                    (
                        preset_filename
                        for preset_source, _, preset_filename
                        in self.TASKBAR_ICON_PRESETS
                        if preset_source == source
                    ),
                    None,
                )
                if filename is None:
                    return f"未知的任务栏图标选项：{source}"
                image = QImageReader(
                    str(_asset_path("assets", "taskbar_icons", filename))
                ).read()
                if image.isNull():
                    return f"无法读取预设图标：{filename}"
            icon_handle = _create_windows_icon(image)

            old_icon_handle = self._taskbar_icon_handle
            window_handle = int(self.winId())
            identity = self._taskbar_shell_metadata(image)
            set_taskbar_identity(window_handle, *identity)
            _set_taskbar_large_icon(window_handle, icon_handle)
            self._taskbar_shell_identity = identity
            self._taskbar_shell_hwnd = window_handle
            self._taskbar_icon_handle = icon_handle
            if old_icon_handle:
                self._destroy_windows_icon(old_icon_handle)
            return None
        except (OSError, ValueError, RuntimeError) as exc:
            if icon_handle:
                self._destroy_windows_icon(icon_handle)
            return str(exc)

    def _refresh_taskbar_icon(self):
        # Reapply ICON_BIG after Qt processes window events. Leave Qt's own
        # icon and ICON_SMALL (the title bar) unchanged.
        if self._taskbar_icon_handle:
            try:
                window_handle = int(self.winId())
                if self._taskbar_shell_identity and self._taskbar_shell_hwnd != window_handle:
                    set_taskbar_identity(window_handle, *self._taskbar_shell_identity)
                    self._taskbar_shell_hwnd = window_handle
                _set_taskbar_large_icon(window_handle, self._taskbar_icon_handle)
            except OSError as exc:
                logging.getLogger("echoplay.ui").warning("Could not refresh taskbar identity: %s", exc)

    @staticmethod
    def _destroy_windows_icon(icon_handle: int):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.DestroyIcon.argtypes = [ctypes.c_void_p]
        user32.DestroyIcon.restype = ctypes.c_int
        user32.DestroyIcon(ctypes.c_void_p(icon_handle))

    def _setup_shortcuts(self):
        actions = {
            "previous": self.play_previous,
            "next": self.play_next,
            "play_pause": self.toggle_play_pause,
            "minimize": self.showMinimized,
        }
        for action, callback in actions.items():
            sequence = self.shortcut_editors[action].keySequence()
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts[action] = shortcut
            self._shortcut_sequences[action] = sequence

    def _on_shortcut_changed(self, action: str, sequence: QKeySequence):
        for other_action, other_sequence in self._shortcut_sequences.items():
            if other_action == action or other_sequence.isEmpty() or sequence.isEmpty():
                continue
            if (
                sequence.matches(other_sequence)
                != QKeySequence.SequenceMatch.NoMatch
            ):
                editor = self.shortcut_editors[action]
                editor.blockSignals(True)
                editor.setKeySequence(self._shortcut_sequences[action])
                editor.blockSignals(False)
                QMessageBox.warning(
                    self,
                    "快捷键冲突",
                    f"该快捷键已用于“{self.SHORTCUT_LABELS[other_action]}”，未保存。",
                )
                return

        self._shortcut_sequences[action] = sequence
        self._shortcuts[action].setKey(sequence)
        self.settings.setValue(
            f"shortcuts/{action}",
            sequence.toString(QKeySequence.SequenceFormat.PortableText),
        )

    def _reset_shortcuts(self):
        for action, default in self.SHORTCUT_DEFAULTS.items():
            self.shortcut_editors[action].setKeySequence(QKeySequence(default))

