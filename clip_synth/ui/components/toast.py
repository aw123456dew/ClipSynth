from PySide6.QtCore import Property, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


class Toast(QFrame):
    def __init__(
        self,
        message: str,
        parent: QWidget | None = None,
        toast_type: str = "success",
        duration: int = 2500,
    ):
        super().__init__(parent)
        self._toast_type = toast_type
        self._opacity = 1.0
        self._setup_ui(message)
        self._setup_animation(duration)

    def _setup_ui(self, message: str) -> None:
        self.setObjectName(f"toast{self._toast_type.capitalize()}")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(10)

        icon_label = QLabel("✓" if self._toast_type == "success" else "✕")
        icon_label.setObjectName("toastIcon")
        icon_label.setStyleSheet(
            "color: #34d399; font-size: 16px; font-weight: bold;"
            if self._toast_type == "success"
            else "color: #f87171; font-size: 16px; font-weight: bold;"
        )
        layout.addWidget(icon_label)

        msg_label = QLabel(message)
        msg_label.setObjectName("toastMessage")
        msg_label.setStyleSheet("color: #e2e8f0; font-size: 13px; font-weight: 500;")
        layout.addWidget(msg_label)

        layout.addStretch()

    def _setup_animation(self, duration: int) -> None:
        self._fade_out = QPropertyAnimation(self, b"windowOpacity")
        self._fade_out.setDuration(300)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.finished.connect(self._on_fade_out_finished)

        QTimer.singleShot(duration, self._fade_out.start)

    def _on_fade_out_finished(self) -> None:
        self.close()
        self.deleteLater()

    def show_toast(self) -> None:
        self.show()
        self.raise_()

    @Property(float)
    def windowOpacity(self) -> float:  # noqa: N802
        return self._opacity

    @windowOpacity.setter
    def windowOpacity(self, value: float) -> None:  # noqa: N802
        self._opacity = value
        self.setWindowOpacity(value)


def show_toast(
    parent: QWidget,
    message: str,
    toast_type: str = "success",
    duration: int = 2500,
) -> Toast:
    toast = Toast(message, parent, toast_type, duration)
    toast.adjustSize()

    parent_rect = parent.rect()
    toast_width = toast.width()
    toast_x = (parent_rect.width() - toast_width) // 2
    toast_y = parent_rect.height() - toast.height() - 40
    toast.move(toast_x, toast_y)

    toast.show_toast()
    return toast
