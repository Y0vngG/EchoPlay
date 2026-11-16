from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QSizePolicy

from echoplay.ui.widgets import ExcelSheetStack
from echoplay.ui.icons import _make_control_icon


class ThemeMixin:
    def _apply_theme(self):
        excel = self.skin == "excel"
        colors = self._theme_colors()
        self.results.setProperty("excelCells", excel)
        self.results.setProperty("sheetColor", colors["panel"])
        self.results.setProperty("gridColor", colors["border"])
        self.results.setProperty("textColor", colors["foreground"])
        self.library_playlist_list.setProperty("excelCells", excel)
        self.library_playlist_list.setProperty("gridColor", colors["border"])
        self.library_playlist_list.viewport().update()
        self.library_items.setProperty("excelCells", excel)
        self.library_items.setProperty("sheetColor", colors["panel"])
        self.library_items.setProperty("gridColor", colors["border"])
        self.library_items.viewport().update()
        self.excel_ribbon.set_theme(self.theme)
        self.excel_ribbon.setVisible(excel)
        self.excel_column_header_row.setVisible(excel)
        self.default_header.setVisible(not excel)
        self.excel_title_row.setVisible(excel)
        self.excel_navigation_row_number.setVisible(excel)
        self.excel_row_header.setVisible(excel)
        self.page_stack.setMinimumHeight(0)
        self.excel_row_header.setProperty("excelSheetColor", colors["window"])
        self.excel_row_header.setProperty("excelGridColor", colors["border"])
        self.excel_row_header.setProperty("excelTextColor", colors["muted"])
        self.excel_row_header.update()
        self._update_excel_sheet_content_minimum_height()
        self.navigation_layout.setSpacing(0 if excel else 10)
        self.excel_sheet_layout.setSpacing(0 if excel else 10)
        self.root_layout.setContentsMargins(
            12 if excel else 28,
            8 if excel else 22,
            12 if excel else 28,
            8 if excel else 18,
        )
        self.root_layout.setSpacing(4 if excel else 14)
        if excel:
            self._update_excel_ribbon_display_size()
        for button in self.navigation_group.buttons():
            button.setMinimumHeight(
                ExcelSheetStack.cell_height if excel else 44
            )
            button.setIconSize(QSize(18, 18) if excel else QSize(22, 22))
        self._set_excel_search_page(excel)
        self._set_excel_library_page(excel)
        self.library_playlist_title.setContentsMargins(
            4 if excel else 0, 0, 0, 0
        )
        if excel:
            self.add_playlist_button.setMinimumWidth(0)
            self.add_playlist_button.setMaximumWidth(16777215)
            self.add_playlist_button.setFixedHeight(
                ExcelSheetStack.cell_height
            )
            self.add_playlist_button.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Fixed,
            )
        else:
            self.add_playlist_button.setFixedSize(34, 34)
            self.add_playlist_button.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )
        self.library_filter.setMinimumHeight(
            ExcelSheetStack.cell_height if excel else 0
        )
        self.library_filter.setMaximumHeight(
            ExcelSheetStack.cell_height if excel else 16777215
        )
        self.library_playlist_header.setFixedHeight(
            ExcelSheetStack.cell_height if excel else 34
        )
        self._update_library_playlist_item_sizes()
        for item_id, item in self.library_row_items.items():
            title = self.library_title_labels.get(item_id)
            author = self.library_author_labels.get(item_id)
            for label in (title, author):
                if label is not None:
                    label.setMinimumHeight(
                        ExcelSheetStack.cell_height - 1 if excel else 0
                    )
                    label.setMaximumHeight(
                        ExcelSheetStack.cell_height - 1
                        if excel
                        else 16777215
                    )
            container = self.library_items.itemWidget(item)
            row = (
                container.findChild(QFrame, "libraryTrackRow")
                if container is not None
                else None
            )
            if row is not None and row.layout() is not None:
                default_spacing = self.library_row_layout_spacings.get(
                    item_id
                )
                if default_spacing is not None:
                    row.layout().setSpacing(0 if excel else default_spacing)
                row.layout().setContentsMargins(
                    0 if excel else 10,
                    0 if excel else 4,
                    0 if excel else 8,
                    0 if excel else 4,
                )
            inner_layouts = self.library_track_inner_layouts.get(item_id)
            default_margins = self.library_track_inner_layout_defaults.get(
                item_id
            )
            if inner_layouts is not None and default_margins is not None:
                for inner_layout, margins in zip(
                    inner_layouts, default_margins
                ):
                    if excel:
                        inner_layout.setContentsMargins(0, 0, 0, 0)
                        inner_layout.setSpacing(0)
                    else:
                        left, top, right, bottom, spacing = margins
                        inner_layout.setContentsMargins(
                            left, top, right, bottom
                        )
                        inner_layout.setSpacing(spacing)
            page_panel = self.library_page_panels.get(item_id)
            self._set_library_details_margins(page_panel)
            page_list = self.library_page_lists.get(item_id)
            if page_list is not None:
                self._size_collection_page_list(page_list)
            self._update_library_item_size(item)
        self.search_input.setMinimumHeight(
            ExcelSheetStack.cell_height if excel else 0
        )
        self.search_input.setMaximumHeight(
            ExcelSheetStack.cell_height if excel else 16777215
        )
        self.search_button.setMinimumHeight(
            ExcelSheetStack.cell_height if excel else 0
        )
        self.search_button.setMaximumHeight(
            ExcelSheetStack.cell_height if excel else 16777215
        )
        self.history_button.setMinimumHeight(ExcelSheetStack.cell_height if excel else 0)
        self.history_button.setMaximumHeight(ExcelSheetStack.cell_height if excel else 16777215)
        for button, icon_name in ((self.history_button, "history"), (self.search_button, "search")):
            button.setIcon(_make_control_icon(icon_name, QColor("#ffffff")))
            button.setIconSize(QSize(20, 20))
        # Keep the icon buttons the same width in both skins.
        button_width = max(self.history_button.sizeHint().width(), self.search_button.sizeHint().width())
        for button in (self.history_button, self.search_button):
            button.setFixedWidth(button_width if not excel else max(1, self.excel_search_view.width() // 12))
        self.search_history_popup.delete_button.setIcon(
            _make_control_icon("close", QColor(colors["muted"])))
        self.search_history_popup.setStyleSheet(f"""
            QListView#recentSearchKeywords {{
                background: {colors['panel']}; color: {colors['foreground']};
                border: 1px solid {colors['border']};
                border-radius: {0 if excel else 10}px;
                padding: 4px 0;
            }}
            QListView::item {{ min-height: 28px; padding: 0 36px 0 8px; }}
            QToolButton#recentSearchDeleteButton {{
                color: {colors['muted']}; background: transparent;
                border: none; border-radius: 4px; padding: 0; font-size: 18px;
            }}
            QToolButton#recentSearchDeleteButton:hover {{
                color: {colors['accent']}; background: {colors['hover']};
            }}
            QListView::item:hover {{
                background: {colors['hover']}; color: {colors['foreground']};
            }}
            QListView::item:selected,
            QListView::item:selected:hover {{ background: {colors['accent']}; color: #ffffff; }}
        """)
        self.search_status.setMinimumHeight(
            ExcelSheetStack.cell_height if excel else 0
        )
        self.search_input.setPlaceholderText("搜索关键词，或粘贴 B 站视频链接")
        self.search_input.setToolTip(
            "输入关键词或粘贴 B 站视频链接" if excel else ""
        )
        self.page_stack.setProperty("excelGridEnabled", excel)
        self.page_stack.setProperty("excelSheetColor", colors["panel"])
        self.page_stack.setProperty("excelGridColor", colors["border"])
        self.page_stack.update()
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("无法应用主题：QApplication 尚未初始化。")
        palette = app.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["foreground"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(colors["panel"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["window"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors["foreground"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(colors["panel"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["foreground"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["accent"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["muted"]))
        app.setPalette(palette)
        playing_page_pixmap = QPixmap(16, 16)
        playing_page_pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(playing_page_pixmap)
        painter.fillRect(0, 0, 3, 16, QColor(colors["accent"]))
        painter.end()
        self.playing_page_icon = QIcon(playing_page_pixmap)
        excel_search_button_color = colors["accent"]
        library_track_background = (
            colors["panel"] if excel else "transparent"
        )
        library_track_border = (
            f"1px solid {colors['border']}" if excel else "none"
        )
        stylesheet = f"""
            QWidget#mainRoot {{
                background-color: {colors["window"]};
                color: {colors["foreground"]};
                font-family: "Segoe UI";
                font-size: 14px;
            }}
            QLabel#brandTitle {{
                color: {colors["foreground"]};
                font-size: 18px;
                font-weight: 700;
            }}
            QLabel#brandSubtitle {{
                color: {colors["muted"]};
                font-size: 11px;
            }}
            QPushButton {{
                background-color: {colors["panel"]};
                color: {colors["foreground"]};
                border: 1px solid {colors["border"]};
                border-radius: 10px;
                padding: 9px 14px;
            }}
            QPushButton:hover {{ border-color: {colors["accent"]}; }}
            QPushButton:disabled {{ color: {colors["muted"]}; }}
            QPushButton#searchResultAddButton {{
                color: {colors["accent"]};
                font-size: 22px;
                font-weight: 500;
                padding: 4px 12px;
            }}
            QPushButton#searchResultAddButton:hover,
            QPushButton#searchResultAddButton:pressed {{
                color: {colors["accent"]};
                background-color: {colors["hover"]};
            }}
            QToolButton#taskbarIconTile, QToolButton#taskbarIconAdd {{
                background-color: transparent;
                color: {colors["foreground"]};
                border: 1px solid {colors["border"]};
                border-radius: 14px;
                padding: 8px;
            }}
            QToolButton#skinOptionTile {{
                background-color: transparent;
                color: {colors["foreground"]};
                border: 1px solid {colors["border"]};
                border-radius: 14px;
                padding: 8px;
            }}
            QToolButton#taskbarIconTile:hover,
            QToolButton#taskbarIconAdd:hover,
            QToolButton#skinOptionTile:hover {{
                background-color: {colors["hover"]};
                border-color: {colors["accent"]};
            }}
            QToolButton#taskbarIconTile:checked,
            QToolButton#skinOptionTile:checked {{
                background-color: {colors["hover"]};
                border-color: {colors["accent"]};
            }}
            QToolButton#taskbarIconAdd {{
                color: {colors["accent"]};
                font-size: 34px;
            }}
            QToolButton#playlistAddButton {{
                background-color: transparent;
                color: {colors["accent"]};
                border: 1px solid {colors["border"]};
                border-radius: 9px;
                font-size: 20px;
            }}
            QToolButton#playlistAddButton:hover {{
                background-color: {colors["hover"]};
                border-color: {colors["accent"]};
            }}
            QSplitter#librarySplitter::handle {{
                background-color: transparent;
                border-left: 1px solid rgba(128, 128, 128, 80);
            }}
            QSplitter#librarySplitter::handle:hover {{
                background-color: rgba(128, 128, 128, 20);
            }}
            QPushButton#navigationTab {{
                background-color: transparent;
                color: {colors["muted"]};
                border: 1px solid transparent;
                border-radius: 12px;
                font-size: 14px;
                font-weight: 600;
                padding: 10px 12px;
            }}
            QPushButton#navigationTab:hover {{
                background-color: {colors["hover"]};
                color: {colors["foreground"]};
            }}
            QPushButton#navigationTab:checked {{
                background-color: {colors["panel"]};
                color: {colors["accent"]};
                border-color: {colors["border"]};
            }}
            QPushButton#playbackControlButton {{
                min-width: 40px;
                min-height: 40px;
                padding: 6px;
                border-radius: 12px;
            }}
            QToolButton#volumeButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 9px;
                padding: 4px;
            }}
            QToolButton#volumeButton:hover {{
                background-color: {colors["hover"]};
                border-color: {colors["border"]};
            }}
            QToolButton#playbackOrderButton {{
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 9px;
                padding: 4px;
            }}
            QToolButton#playbackOrderButton:hover {{
                background-color: {colors["hover"]};
                border-color: {colors["border"]};
            }}
            QToolButton#playbackOrderButton:checked {{
                background-color: {colors["hover"]};
                border-color: {colors["accent"]};
            }}
            QPushButton#searchButton {{
                background-color: {colors["accent"]};
                color: #ffffff;
                border-color: {colors["accent"]};
                font-weight: 600;
            }}
            QPushButton#searchButton:hover {{
                background-color: {colors["accent"]};
                border-color: {colors["accent"]};
            }}
            QLineEdit, QListWidget, QComboBox {{
                background-color: {colors["panel"]};
                color: {colors["foreground"]};
                border: 1px solid {colors["border"]};
                border-radius: 10px;
                padding: 8px 10px;
            }}
            QLineEdit#searchInput {{
                background-color: {colors["panel"]};
                color: {colors["foreground"]};
                border: 1px solid {colors["border"]};
                border-radius: 10px;
                padding: 8px 10px;
            }}
            QLineEdit#searchInput:focus {{
                border-color: {colors["accent"]};
            }}
            QLineEdit:focus, QKeySequenceEdit:focus {{
                border-color: {colors["accent"]};
            }}
            QSlider::groove:horizontal {{
                height: 6px;
                background-color: {colors["progress_track"]};
                border: 1px solid {colors["border"]};
                border-radius: 3px;
            }}
            QSlider::sub-page:horizontal {{
                background-color: {colors["accent"]};
                border-radius: 3px;
            }}
            QSlider::add-page:horizontal {{
                background-color: {colors["progress_unplayed"]};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                width: 14px;
                margin: -5px 0;
                background-color: {colors["panel"]};
                border: 2px solid {colors["accent"]};
                border-radius: 7px;
            }}
            QListWidget::item:hover, QListWidget::item:selected:hover {{
                background-color: {colors["hover"]};
            }}
            QListWidget::item:selected {{
                background-color: transparent;
                border: none;
                outline: none;
            }}
            QListWidget#libraryPlaylistList::item:selected {{
                background-color: {colors["hover"]};
                color: {colors["accent"]};
                border-radius: 6px;
            }}
            QStackedWidget#pageStack {{
                background-color: {colors["panel"]};
                border: 1px solid {colors["border"]};
                border-radius: 18px;
            }}
            QWidget#searchPage, QWidget#libraryPage, QWidget#settingsPage {{
                background-color: transparent;
            }}
            QWidget#settingsContent, QWidget#settingsSection,
            QScrollArea#settingsScrollArea,
            QScrollArea#settingsScrollArea QWidget#qt_scrollarea_viewport {{
                background-color: transparent;
            }}
            QLabel#pageTitle, QLabel#settingsTitle {{
                color: {colors["foreground"]};
                font-size: 24px;
                font-weight: 700;
            }}
            QLabel#pageSubtitle, QLabel#settingsDescription {{
                color: {colors["muted"]};
            }}
            QLabel#excelSearchStatusCell {{
                color: {colors["muted"]};
            }}
            QFrame#settingsSeparator {{
                background-color: {colors["border"]};
                border: none;
            }}
            QLabel#settingsSectionTitle {{
                color: {colors["foreground"]};
                font-size: 20px;
                font-weight: 600;
            }}
            QLabel#settingsAccount {{
                color: {colors["foreground"]};
                font-size: 18px;
                font-weight: 600;
            }}
            QLabel#accountAvatar {{
                background-color: {colors["badge"]};
                color: {colors["accent"]};
                border-radius: 20px;
                font-size: 16px;
                font-weight: 600;
            }}
            QFrame#playbackBar {{
                background-color: {colors["panel"]};
                border: 1px solid {colors["border"]};
                border-radius: 16px;
            }}
            QLabel#mutedText {{ color: {colors["muted"]}; }}
            QLabel#coverPlaceholder {{
                background-color: {colors["cover"]};
                color: {colors["muted"]};
            }}
            QLabel#collectionBadge {{
                background-color: {colors["badge"]};
                color: {colors["foreground"]};
                border: 1px solid {colors["border"]};
                border-radius: 8px;
                padding: 2px 7px;
                font-size: 11px;
            }}
            QLabel#libraryNotification {{
                background-color: {colors["panel"]};
                color: {colors["foreground"]};
                border: 1px solid {colors["border"]};
                border-radius: 10px;
                font-size: 13px;
            }}
            QMenu {{
                background-color: {colors["panel"]};
                color: {colors["foreground"]};
                border: 1px solid {colors["border"]};
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item {{
                min-height: 20px;
                padding: 7px 28px 7px 12px;
                margin: 2px 4px;
                border: 1px solid transparent;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: {colors["hover"]};
                border-color: transparent;
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {colors["border"]};
                margin: 4px 8px;
            }}
            """
        if excel:
            stylesheet += f"""
            QWidget#mainRoot {{
                font-family: "Aptos", "Segoe UI";
                font-size: 13px;
            }}
            QLabel#brandTitle {{
                font-size: 21px;
                font-weight: 600;
            }}
            QPushButton {{
                border-radius: 0;
                padding: 6px 10px;
            }}
            QPushButton#navigationTab {{
                background-color: {colors["panel"]};
                color: {colors["foreground"]};
                border: none;
                border-right: 1px solid {colors["border"]};
                border-bottom: 1px solid {colors["border"]};
                border-radius: 0;
                font-size: 12px;
                font-weight: 400;
                padding: 0 6px;
            }}
            QPushButton#navigationTab:hover {{
                background-color: {colors["hover"]};
                color: {colors["foreground"]};
            }}
            QPushButton#navigationTab:checked {{
                background-color: {colors["panel"]};
                color: {colors["accent"]};
                border-right: 1px solid {colors["border"]};
                border-bottom: 1px solid {colors["accent"]};
                font-weight: 600;
            }}
            QPushButton#searchButton,
            QPushButton#searchButton:hover,
            QPushButton#searchButton:pressed,
            QPushButton#searchButton:disabled {{
                background-color: {excel_search_button_color};
                color: #ffffff;
                border: none;
                border-right: 1px solid {colors["border"]};
                border-bottom: 1px solid {colors["border"]};
                padding: 0 10px;
            }}
            QLineEdit#searchInput {{
                border-radius: 0;
                border: none;
                border-right: 1px solid {colors["border"]};
                border-bottom: 1px solid {colors["border"]};
                padding: 0 10px;
            }}
            QPushButton#playbackControlButton,
            QToolButton#volumeButton,
            QToolButton#playbackOrderButton {{
                background-color: {colors["panel"]};
                border: 1px solid {colors["border"]};
                border-radius: 0;
            }}
            QPushButton#playbackControlButton:hover,
            QToolButton#volumeButton:hover,
            QToolButton#playbackOrderButton:hover,
            QToolButton#playbackOrderButton:checked {{
                background-color: {colors["hover"]};
                border-color: {colors["accent"]};
            }}
            QToolButton#skinOptionTile {{
                border-radius: 0;
            }}
            QToolButton#taskbarIconTile,
            QToolButton#taskbarIconAdd,
            QToolButton#playlistAddButton {{
                border-radius: 0;
            }}
            QLineEdit, QListWidget, QComboBox, QKeySequenceEdit {{
                border-radius: 0;
                border: 1px solid {colors["border"]};
            }}
            QLineEdit:focus, QKeySequenceEdit:focus {{
                border-color: {colors["accent"]};
            }}
            QComboBox#playbackOrderCombo {{
                border-radius: 0;
            }}
            QStackedWidget#pageStack {{
                background-color: {colors["panel"]};
                border: 1px solid {colors["border"]};
                border-radius: 0;
            }}
            QFrame#playbackBar {{
                background-color: {colors["window"]};
                border: 1px solid {colors["border"]};
                border-radius: 0;
            }}
            QFrame#settingsSeparator {{
                background-color: {colors["border"]};
            }}
            QToolButton#playlistAddButton {{
                border-radius: 0;
            }}
            QFrame#librarySplitter::handle {{
                border-left: 1px solid {colors["border"]};
            }}
            QLabel#excelRibbonImage {{
                background-color: {colors["window"]};
                border-bottom: 1px solid {colors["border"]};
            }}
            QWidget#excelColumnHeaderRow,
            QWidget#excelRowHeader,
            QLabel#excelRowHeader {{
                background-color: {colors["window"]};
                border: 1px solid {colors["border"]};
            }}
            QLabel#excelColumnHeader {{
                color: {colors["muted"]};
                border-right: 1px solid {colors["border"]};
                font-size: 11px;
            }}
            QLabel#excelRowNumber {{
                background-color: {colors["window"]};
                color: {colors["muted"]};
                border-right: 1px solid {colors["border"]};
                border-bottom: 1px solid {colors["border"]};
                font-size: 11px;
            }}
            QLabel#excelTitleCell, QLabel#excelBlankCell {{
                background-color: {colors["panel"]};
                border: none;
                border-right: 1px solid {colors["border"]};
                border-bottom: 1px solid {colors["border"]};
            }}
            QLabel#excelTitleCell {{
                color: {colors["accent"]};
                font-size: 12px;
                font-weight: 600;
            }}
            QWidget#excelSearchView {{
                background-color: transparent;
            }}
            QLabel#excelSearchTitleCell {{
                background-color: {colors["panel"]};
                color: {colors["foreground"]};
                border: none;
                border-right: 1px solid {colors["border"]};
                border-bottom: 1px solid {colors["border"]};
                font-size: 14px;
                font-weight: 600;
            }}
            QLabel#excelSearchStatusCell {{
                background-color: {colors["panel"]};
                border: none;
                border-bottom: 1px solid {colors["border"]};
                padding: 3px 8px;
            }}
            QLabel#excelSearchBlankCell {{
                background-color: {colors["panel"]};
                border-right: 1px solid {colors["border"]};
                border-bottom: 1px solid {colors["border"]};
            }}
            QListWidget#searchResults {{
                background-color: transparent;
                border: none;
                padding: 0;
            }}
            QListWidget#searchResults::item {{
                border-bottom: none;
            }}
            QListWidget#searchResults::item:hover,
            QListWidget#searchResults::item:selected,
            QListWidget#searchResults::item:selected:hover {{
                background-color: transparent;
            }}
            QWidget#searchResultContainer,
            QFrame#searchResultRow {{
                background-color: transparent;
            }}
            QFrame#searchResultRow {{
                background-color: {colors["panel"]};
                border: none;
                border-bottom: 1px solid {colors["border"]};
            }}
            QPushButton#searchResultAddButton {{
                background-color: {colors["panel"]};
                color: {colors["accent"]};
                border: none;
                border-left: 1px solid {colors["border"]};
                border-radius: 0;
                padding: 0 4px;
                font-size: 22px;
            }}
            QPushButton#searchResultAddButton:hover {{
                background-color: {colors["hover"]};
            }}
            QPushButton#searchResultAddButton:pressed {{
                background-color: {colors["hover"]};
                color: {colors["accent"]};
            }}
            QFrame#searchResultRow QWidget#searchCoverCell {{
                background-color: {colors["panel"]};
                border-right: 1px solid {colors["border"]};
            }}
            QWidget#searchResultContainer {{
                background-color: {colors["panel"]};
            }}
            QFrame#searchResultBottomBorder {{
                background-color: {colors["border"]};
                border: none;
            }}
            QListWidget#searchResultPages, QListWidget#libraryResultPages {{
                background-color: {colors["panel"]};
                border: none;
                border-left: 1px solid {colors["border"]};
                padding: 0;
            }}
            QListWidget#searchResultPages::item, QListWidget#libraryResultPages::item {{
                padding: 0 4px;
                border-bottom: 1px solid {colors["border"]};
            }}
            QStackedWidget#pageStack {{
                background-color: transparent;
                border: none;
            }}
            QScrollArea#excelContentScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QScrollArea#excelContentScrollArea QWidget#qt_scrollarea_viewport {{
                background-color: transparent;
            }}
            QListWidget {{
                background-color: transparent;
            }}
            QListWidget::viewport {{
                background-color: transparent;
            }}
            QListWidget::item {{
                border-bottom: 1px solid {colors["border"]};
            }}
            QListWidget#libraryPlaylistList::item:selected {{
                background-color: {colors["hover"]};
                color: {colors["accent"]};
                border-radius: 0;
            }}
            QWidget#excelLibraryView {{
                background-color: transparent;
            }}
            QWidget#excelLibraryPlaylistHeader {{
                background-color: {colors["panel"]};
                border-bottom: 1px solid {colors["border"]};
            }}
            QLabel#excelLibraryTitleCell,
            QLabel#excelLibraryBlankCell {{
                background-color: {colors["panel"]};
                border: none;
                border-right: 1px solid {colors["border"]};
                border-bottom: 1px solid {colors["border"]};
            }}
            QLabel#excelLibraryTitleCell {{
                color: {colors["foreground"]};
                font-size: 14px;
                font-weight: 600;
                padding-left: 4px;
            }}
            QLineEdit#libraryFilter {{
                border: none;
                border-left: 1px solid {colors["border"]};
                border-bottom: 1px solid {colors["border"]};
                padding: 0 4px;
            }}
            QToolButton#playlistAddButton {{
                background-color: {colors["accent"]};
                color: #ffffff;
                border: none;
                border-bottom: 1px solid {colors["border"]};
                border-radius: 0;
                font-size: 18px;
                padding: 0;
            }}
            QToolButton#playlistAddButton:hover {{
                background-color: {colors["accent"]};
            }}
            QListWidget#libraryPlaylistList,
            QListWidget#libraryItems {{
                background-color: transparent;
                border: none;
                padding: 0;
            }}
            QListWidget#libraryPlaylistList::item {{
                border-bottom: none;
                padding: 0 4px;
            }}
            QListWidget#libraryItems::item {{
                border-bottom: none;
                padding: 0;
            }}
            QListWidget#libraryItems::item:selected {{
                background-color: transparent;
            }}
            QListWidget#libraryItems::item:hover,
            QListWidget#libraryItems::item:selected:hover {{
                background-color: transparent;
            }}
            QFrame#libraryTrackRow {{
                background-color: {library_track_background};
                border-left: {library_track_border};
                border-right: {library_track_border};
                border-bottom: {library_track_border};
                border-top: none;
            }}
            QFrame#libraryColumnBorder {{
                background-color: {colors["border"]};
                border: none;
            }}
            """
        stylesheet += f"""
            QPushButton#streamQualityOption {{
                border-radius: {2 if excel else 8}px;
            }}
            QPushButton#streamQualityOption:checked {{
                background-color: {colors["accent"]};
                border-color: {colors["accent"]};
                color: #ffffff;
                font-weight: 600;
            }}
            QPushButton#streamQualityOption:disabled {{
                background-color: {colors["window"]};
                border-color: {colors["border"]};
                color: {colors["muted"]};
            }}
            QScrollBar:vertical, QScrollBar:horizontal {{
                background-color: {colors["scroll_track"]};
                border: none;
                margin: 0;
            }}
            QScrollBar:vertical {{ width: 14px; }}
            QScrollBar:horizontal {{ height: 14px; }}
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
                background-color: {colors["scroll_handle"]};
                border: 2px solid {colors["scroll_track"]};
                border-radius: {2 if excel else 6}px;
            }}
            QScrollBar::handle:vertical {{ min-height: 28px; }}
            QScrollBar::handle:horizontal {{ min-width: 28px; }}
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
                background-color: {colors["scroll_hover"]};
            }}
            QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed {{
                background-color: {colors["scroll_pressed"]};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
                background: none;
            }}
            QAbstractScrollArea::corner {{ background-color: {colors["scroll_track"]}; }}
        """
        self.setStyleSheet(stylesheet)
        for item in self.search_result_row_items.values():
            self._update_search_result_item_size(item)
        self._refresh_navigation_icons()
        self._refresh_track_control_icons()
        self._refresh_play_pause_icon()
        self._refresh_volume_icon()
        self._refresh_playback_order_icons()
        self._update_library_playing_indicator()
        self.results.viewport().update()
        self._sync_search_sheet_scroll()
        self._sync_library_column_border()

