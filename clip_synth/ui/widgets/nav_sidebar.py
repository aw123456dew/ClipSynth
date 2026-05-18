from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class NavButton(QPushButton):
    def __init__(self, text: str, icon_char: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._icon_char = icon_char
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(52)
        self.setText(f"  {icon_char}  {text}" if icon_char else text)
        self.setObjectName("navButton")

    def set_active(self, active: bool) -> None:
        self.setChecked(active)
        if active:
            self.setProperty("active", True)
        else:
            self.setProperty("active", False)
        self.style().unpolish(self)
        self.style().polish(self)


class NavSidebar(QFrame):
    page_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("navSidebar")
        self.setFixedWidth(200)
        self._buttons: list[NavButton] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("navHeader")
        header.setMinimumHeight(80)
        header_layout = QVBoxLayout(header)
        header_layout.setAlignment(Qt.AlignCenter)

        logo = QLabel("ClipSynth")
        logo.setObjectName("navLogo")
        logo.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(logo)

        subtitle = QLabel("AI Video Editor")
        subtitle.setObjectName("navSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(subtitle)

        layout.addWidget(header)

        menu_container = QFrame()
        menu_container.setObjectName("navMenuContainer")
        menu_layout = QVBoxLayout(menu_container)
        menu_layout.setContentsMargins(12, 16, 12, 16)
        menu_layout.setSpacing(4)

        nav_items = [
            ("short_drama_mix", "短剧混剪", "\u25b6"),
            ("short_drama_narrate", "视频解说", "\u25b6"),
            ("short_drama_narrate_v2", "视频解说 V2", "\u25b6"),
            ("video_dedup", "视频处理", "\u25b6"),
            ("novel_mix", "小说混剪", "\u25b6"),
            ("settings", "系统配置", "\u25b6"),
        ]

        for key, label, icon in nav_items:
            btn = NavButton(label, icon)
            btn.clicked.connect(lambda checked, k=key: self._on_nav_clicked(k))
            menu_layout.addWidget(btn)
            self._buttons.append(btn)

        menu_layout.addStretch()
        layout.addWidget(menu_container, stretch=1)

        version_label = QLabel("v0.1.0")
        version_label.setObjectName("navVersion")
        version_label.setAlignment(Qt.AlignCenter)
        version_label.setMinimumHeight(32)
        layout.addWidget(version_label)

        if self._buttons:
            self._buttons[0].set_active(True)

    def _on_nav_clicked(self, page_key: str) -> None:
        for btn in self._buttons:
            btn.set_active(False)
        sender = self.sender()
        if isinstance(sender, NavButton):
            sender.set_active(True)
        self.page_changed.emit(page_key)
