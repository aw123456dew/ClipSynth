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

from clip_synth.ui.pages.narrate_v2.recognition_worker import RecognitionWorker, TencentRecognitionWorker
from clip_synth.services.settings_service import SettingsService

logger = logging.getLogger("clip_synth.narrate_v2")


VOLC_ENGINE_OPTIONS = [
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

TENCENT_ENGINE_OPTIONS = [
    ("中文普通话（中英粤+方言大模型，推荐）", "16k_zh_en"),
    ("中文普通话（普方英大模型）", "16k_zh_large"),
    ("中文普通话通用", "16k_zh"),
    ("中英粤混合", "16k_zh-PY"),
    ("中文繁体", "16k_zh-TW"),
    ("粤语", "16k_yue"),
    ("英语", "16k_en"),
    ("英语（大模型）", "16k_en_large"),
    ("日语", "16k_ja"),
    ("韩语", "16k_ko"),
    ("越南语", "16k_vi"),
    ("马来语", "16k_ms"),
    ("印度尼西亚语", "16k_id"),
    ("菲律宾语", "16k_fil"),
    ("泰语", "16k_th"),
    ("葡萄牙语", "16k_pt"),
    ("土耳其语", "16k_tr"),
    ("阿拉伯语", "16k_ar"),
    ("西班牙语", "16k_es"),
    ("印地语", "16k_hi"),
    ("法语", "16k_fr"),
    ("德语", "16k_de"),
    ("中文医疗", "16k_zh_medical"),
    ("多语种自动识别（15语种）", "16k_multi_lang"),
]


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

    def _populate_lang_combo(self, provider):
        self._lang_combo.blockSignals(True)
        self._lang_combo.clear()
        options = TENCENT_ENGINE_OPTIONS if provider == "tencent" else VOLC_ENGINE_OPTIONS
        for label, code in options:
            self._lang_combo.addItem(label, code)
        self._lang_combo.blockSignals(False)

    def _on_provider_changed(self):
        provider = self._asr_provider_combo.currentData()
        self._populate_lang_combo(provider)

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

        provider_label = QLabel("ASR引擎：")
        provider_label.setStyleSheet("color: #cbd5e1; font-size: 14px;")
        bottom_row.addWidget(provider_label)

        self._asr_provider_combo = QComboBox()
        self._asr_provider_combo.setObjectName("asrProviderCombo")
        self._asr_provider_combo.setMinimumWidth(140)
        self._asr_provider_combo.addItem("火山引擎", "volcengine")
        self._asr_provider_combo.addItem("腾讯云ASR", "tencent")
        self._asr_provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        bottom_row.addWidget(self._asr_provider_combo)

        lang_label = QLabel("字幕语言：")
        lang_label.setStyleSheet("color: #cbd5e1; font-size: 14px;")
        bottom_row.addWidget(lang_label)

        self._lang_combo = QComboBox()
        self._lang_combo.setObjectName("styleCombo")
        self._lang_combo.setMinimumWidth(160)
        bottom_row.addWidget(self._lang_combo)

        self._populate_lang_combo("volcengine")

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
        provider = self._asr_provider_combo.currentData()

        if provider == "tencent":
            tencent = settings.tencent_asr
            if not tencent.is_configured:
                self._status_label.setText("请先在系统配置中配置腾讯云ASR参数")
                self._status_label.show()
                return
        else:
            doubao = settings.doubao_voice
            if not doubao.is_configured:
                self._status_label.setText("请先在系统配置中配置豆包语音参数")
                self._status_label.show()
                return

        engine = self._lang_combo.currentData()
        self._start_btn.setEnabled(False)
        self._progress_bar.show()
        self._status_label.setText("正在准备...")
        self._status_label.show()

        if provider == "tencent":
            self._worker = TencentRecognitionWorker(
                self._video_paths,
                engine,
                settings.tencent_asr.secret_id,
                settings.tencent_asr.secret_key,
                settings.tencent_asr.region,
                self._project_id,
            )
        else:
            doubao = settings.doubao_voice
            self._worker = RecognitionWorker(
                self._video_paths,
                engine,
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
