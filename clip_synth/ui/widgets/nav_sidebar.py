from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


EXPANDED_WIDTH = 200
COLLAPSED_WIDTH = 0


class NavButton(QPushButton):
    def __init__(self, text: str, icon_char: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self._icon_char = icon_char
        self._collapsed = False
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(52)
        self._update_label()
        self.setObjectName("navButton")

    def set_collapsed(self, collapsed: bool) -> None:
        if self._collapsed == collapsed:
            return
        self._collapsed = collapsed
        self._update_label()
        self.setProperty("collapsed", collapsed)
        self.style().unpolish(self)
        self.style().polish(self)

    def _update_label(self) -> None:
        if self._collapsed:
            self.setText(f"  {self._icon_char}  ")
            self.setToolTip(self._text)
        else:
            self.setText(f"  {self._icon_char}  {self._text}")
            self.setToolTip("")

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
    collapse_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("navSidebar")
        self._buttons: list[NavButton] = []
        self._collapsed = False
        self._anim: QPropertyAnimation | None = None
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.setMinimumWidth(COLLAPSED_WIDTH)
        self.setMaximumWidth(EXPANDED_WIDTH)
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

        logo = QLabel("AI推")
        logo.setObjectName("navLogo")
        logo.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(logo)

        subtitle = QLabel("AI Video Editor")
        subtitle.setObjectName("navSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(subtitle)

        self._collapse_btn = QPushButton("\u00ab")
        self._collapse_btn.setObjectName("navCollapseBtn")
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.setFixedSize(32, 32)
        self._collapse_btn.clicked.connect(self._toggle_collapse)
        header_layout.addWidget(self._collapse_btn, 0, Qt.AlignRight)

        layout.addWidget(header)

        self._menu_container = QFrame()
        self._menu_container.setObjectName("navMenuContainer")
        self._menu_layout = QVBoxLayout(self._menu_container)
        self._menu_layout.setContentsMargins(12, 16, 12, 16)
        self._menu_layout.setSpacing(4)

        nav_items = [
            ("short_drama_mix", "短剧混剪", "\u25b6"),
            ("short_drama_narrate", "视频解说", "\u25b6"),
            ("short_drama_narrate_v2", "视频解说 V2", "\u25b6"),
            ("video_dedup", "视频处理", "\u25b6"),
            ("novel_mix", "视频配音混剪", "\u25b6"),
            ("novel_comic", "漫画生成", "\u25b6"),
            ("novel_rewrite", "小说改写", "\u25b6"),
            ("settings", "系统配置", "\u25b6"),
        ]

        for key, label, icon in nav_items:
            btn = NavButton(label, icon)
            btn.clicked.connect(lambda checked, k=key: self._on_nav_clicked(k))
            self._menu_layout.addWidget(btn)
            self._buttons.append(btn)

        self._menu_layout.addStretch()
        layout.addWidget(self._menu_container, stretch=1)

        self._version_label = QLabel("v0.1.0")
        self._version_label.setObjectName("navVersion")
        self._version_label.setAlignment(Qt.AlignCenter)
        self._version_label.setMinimumHeight(32)
        layout.addWidget(self._version_label)

        if self._buttons:
            self._buttons[0].set_active(True)

    def _toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed

        if self._anim and self._anim.state() == QPropertyAnimation.Running:
            self._anim.stop()

        start_width = self.width()
        end_width = COLLAPSED_WIDTH if self._collapsed else EXPANDED_WIDTH

        self._anim = QPropertyAnimation(self, b"maximumWidth")
        self._anim.setDuration(200)
        self._anim.setStartValue(start_width)
        self._anim.setEndValue(end_width)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_anim_finished)

        self._collapse_btn.setText("\u00bb" if self._collapsed else "\u00ab")
        self.setProperty("collapsed", self._collapsed)
        self.style().unpolish(self)
        self.style().polish(self)

        for btn in self._buttons:
            btn.set_collapsed(self._collapsed)
        self._version_label.setVisible(not self._collapsed)

        self._menu_layout.setContentsMargins(
            6 if self._collapsed else 12,
            16, 6 if self._collapsed else 12, 16,
        )

        self._anim.start()

    def _on_anim_finished(self) -> None:
        self.collapse_changed.emit(self._collapsed)

    def _on_nav_clicked(self, page_key: str) -> None:
        for btn in self._buttons:
            btn.set_active(False)
        sender = self.sender()
        if isinstance(sender, NavButton):
            sender.set_active(True)
        self.page_changed.emit(page_key)

    def is_collapsed(self) -> bool:
        return self._collapsed
