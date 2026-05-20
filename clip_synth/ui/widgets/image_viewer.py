from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QDialog, QWidget


class ImageViewer(QDialog):
    def __init__(self, image_path: str, title: str = "", parent: QWidget | None = None):
        super().__init__(parent, Qt.FramelessWindowHint)
        self.setWindowTitle(title)
        self.setObjectName("imageViewer")
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._pixmap = QPixmap(image_path)
        self._scale_factor = 1.0
        self._dragging = False
        self._last_pos = None

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def show_image(self) -> None:
        self.showFullScreen()
        screen_size = self.screen().size()
        pw = self._pixmap.width()
        ph = self._pixmap.height()
        if pw > 0 and ph > 0 and pw <= screen_size.width() and ph <= screen_size.height() - 60:
            self._scale_factor = 1.0
        else:
            fit_w = screen_size.width() * 0.9 / pw if pw > 0 else 1
            fit_h = (screen_size.height() - 80) / ph if ph > 0 else 1
            self._scale_factor = min(fit_w, fit_h, 1.0)
        self.exec()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        painter.fillRect(self.rect(), QColor(0, 0, 0, 220))

        if self._pixmap.isNull():
            return

        sw = self.width()
        sh = self.height()
        dw = int(self._pixmap.width() * self._scale_factor)
        dh = int(self._pixmap.height() * self._scale_factor)

        x = (sw - dw) // 2
        y = (sh - dh) // 2

        scaled = self._pixmap.scaled(dw, dh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter.drawPixmap(x, y, scaled)

        if self.underMouse():
            hint = "点击空白处关闭 | 滚轮缩放 | 右键关闭 | ESC 关闭"
            painter.setPen(QColor(200, 200, 200, 180))
            font = painter.font()
            font.setPointSize(10)
            painter.setFont(font)
            text_rect = painter.boundingRect(
                self.rect().adjusted(12, 12, -12, -12),
                Qt.AlignLeft | Qt.AlignTop,
                hint,
            )
            painter.fillRect(text_rect.adjusted(-6, -2, 6, 2), QColor(0, 0, 0, 160))
            painter.drawText(18, text_rect.bottom() - 4, hint)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        factor = 1.1 if delta > 0 else 0.9
        self._scale_factor = max(0.05, min(self._scale_factor * factor, 10.0))
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_pos = event.globalPosition().toPoint()
        elif event.button() == Qt.RightButton:
            self.reject()

    def mouseMoveEvent(self, event) -> None:
        if self._dragging and self._last_pos is not None:
            self._last_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self._dragging and not self._pixmap.isNull():
                pw = int(self._pixmap.width() * self._scale_factor)
                ph = int(self._pixmap.height() * self._scale_factor)
                x = (self.width() - pw) // 2
                y = (self.height() - ph) // 2
                pos = event.position().toPoint()
                if not (x <= pos.x() <= x + pw and y <= pos.y() <= y + ph):
                    self.reject()
            self._dragging = False

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Escape, Qt.Key_Q):
            self.reject()
        elif event.key() in (Qt.Key_Equal, Qt.Key_Plus):
            self._scale_factor = min(self._scale_factor * 1.25, 10.0)
            self.update()
        elif event.key() == Qt.Key_Minus:
            self._scale_factor = max(0.05, self._scale_factor * 0.8)
            self.update()
        elif event.key() == Qt.Key_0:
            self._scale_factor = 1.0
            self.update()


def show_image_viewer(image_path: str, title: str = "", parent: QWidget | None = None) -> None:
    viewer = ImageViewer(image_path, title, parent)
    viewer.show_image()
