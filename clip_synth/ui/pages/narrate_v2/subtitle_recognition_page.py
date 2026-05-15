import logging
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from clip_synth.ui.pages.narrate_v2.recognition_worker import RecognitionWorker
from clip_synth.services.settings_service import SettingsService

logger = logging.getLogger("clip_synth.narrate_v2")


class ReorderableVideoList(QListWidget):
    order_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setSelectionMode(QListWidget.SingleSelection)

    def dropEvent(self, event: QDropEvent) -> None:
        super().dropEvent(event)
        self.order_changed.emit()


class SubtitleRecognitionPage(QFrame):
    recognition_done = Signal(dict)
    video_order_changed = Signal(list)

    def __init__(self, video_paths: list, settings_service: SettingsService,
                 project_id: str = "", parent=None):
        super().__init__(parent)
        self._video_paths = list(video_paths)
        self._settings_service = settings_service
        self._project_id = project_id
        self._worker = None
        self._setup_ui()

    def _sync_video_paths_from_list(self):
        new_paths = []
        for i in range(self._video_list.count()):
            item = self._video_list.item(i)
            if item:
                new_paths.append(item.toolTip())
        self._video_paths = new_paths
        self.video_order_changed.emit(list(self._video_paths))

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)

        title = QLabel("字幕识别")
        title.setObjectName("aiStyleTitle")
        layout.addWidget(title)

        hint = QLabel("提示：可拖拽视频列表左侧空白区域调整顺序")
        hint.setObjectName("dialogFieldHint")
        hint.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(hint)

        self._video_list = ReorderableVideoList()
        self._video_list.setObjectName("videoListWidget")
        self._video_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._video_list.setMinimumHeight(200)

        for video_path in self._video_paths:
            filename = os.path.basename(video_path)
            item = QListWidgetItem(f"  {filename}")
            item.setToolTip(video_path)
            self._video_list.addItem(item)

        self._video_list.order_changed.connect(self._sync_video_paths_from_list)
        layout.addWidget(self._video_list, stretch=1)

        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("recognitionProgressBar")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.hide()
        layout.addWidget(self._progress_bar)

        self._status_label = QLabel("")
        self._status_label.setObjectName("wizardStatusLabel")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.hide()
        layout.addWidget(self._status_label)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)

        lang_label = QLabel("字幕语言：")
        lang_label.setStyleSheet("color: #cbd5e1; font-size: 14px;")
        bottom_row.addWidget(lang_label)

        self._lang_combo = QComboBox()
        self._lang_combo.setObjectName("styleCombo")
        self._lang_combo.setMinimumWidth(160)
        languages = [
            ("中文普通话（中英混合）", "zh-CN"),
            ("粤语", "yue"),
            ("吴语-上海话", "wuu"),
            ("闽南语", "nan"),
            ("西南官话", "xghu"),
            ("中原官话", "zgyu"),
            ("维语", "ug"),
            ("英语（美国）", "en-US"),
            ("日语", "ja-JP"),
            ("韩语", "ko-KR"),
            ("西班牙语", "es-MX"),
            ("俄语", "ru-RU"),
            ("法语", "fr-FR"),
        ]
        for label, code in languages:
            self._lang_combo.addItem(label, code)
        bottom_row.addWidget(self._lang_combo)

        bottom_row.addStretch()

        self._start_btn = QPushButton("开始识别")
        self._start_btn.setObjectName("startAiAnalysisBtn")
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.setMinimumWidth(140)
        self._start_btn.clicked.connect(self._on_start_recognition)
        bottom_row.addWidget(self._start_btn)

        layout.addLayout(bottom_row)

    def _on_start_recognition(self):
        settings = self._settings_service.load()
        doubao = settings.doubao_voice
        if not doubao.is_configured:
            self._status_label.setText("请先在系统配置中配置豆包语音参数")
            self._status_label.show()
            return

        language = self._lang_combo.currentData()
        self._start_btn.setEnabled(False)
        self._progress_bar.show()
        self._status_label.setText("正在准备...")
        self._status_label.show()

        self._worker = RecognitionWorker(
            self._video_paths,
            language,
            doubao.app_id,
            doubao.token,
            self._project_id,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.recognition_finished.connect(self._on_recognition_finished)
        self._worker.error.connect(self._on_recognition_error)
        self._worker.start()

    def _on_progress(self, value: int, status_text: str):
        self._progress_bar.setValue(value)
        self._status_label.setText(status_text)

    def _on_recognition_finished(self, result: dict):
        self._progress_bar.setValue(100)
        self._status_label.setText("识别完成")
        self._start_btn.setEnabled(True)
        self.recognition_done.emit(result)

    def _on_recognition_error(self, error_msg: str):
        self._progress_bar.hide()
        self._status_label.setText(f"识别失败: {error_msg}")
        self._status_label.setStyleSheet("color: #f87171;")
        self._start_btn.setEnabled(True)

    def cancel_worker(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.quit()
            self._worker.wait()
