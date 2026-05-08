import logging
import os
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("clip_synth.custom_voiceover")


class CustomVoiceoverList(QFrame):
    data_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scripts: List[dict] = []
        self._items: List[dict] = []  # [{text, audio_path, subtitle_path}]
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._export_btn = QPushButton("导出配音文案")
        self._export_btn.setObjectName("exportVoiceoverBtn")
        self._export_btn.clicked.connect(self._on_export)
        toolbar.addWidget(self._export_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._table = QTableWidget()
        self._table.setObjectName("customVoiceoverTable")
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["配音文案", "操作"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._table.setColumnWidth(1, 200)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._table, 1)

    def set_scripts(self, scripts: List[dict], original_sound_ratio: int = 0):
        self._scripts = scripts
        self._rebuild_table()

    def _rebuild_table(self):
        texts = [s.get("narration_script", "") or s.get("text", "") for s in self._scripts]

        self._table.setRowCount(len(texts))

        while len(self._items) < len(texts):
            self._items.append({"text": "", "audio_path": "", "subtitle_path": ""})

        for i, text in enumerate(texts):
            text_item = QTableWidgetItem(text)
            text_item.setFlags(text_item.flags() | Qt.ItemIsEditable)
            self._table.setItem(i, 0, text_item)

            btn_widget = QWidget()
            btn_layout = QHBoxLayout(btn_widget)
            btn_layout.setContentsMargins(4, 4, 4, 4)
            btn_layout.setSpacing(8)

            audio_btn = QPushButton("上传配音")
            audio_btn.setObjectName("uploadAudioBtn")
            audio_btn.setProperty("row", i)
            audio_btn.clicked.connect(self._on_upload_audio)
            audio_btn.setMinimumWidth(75)
            audio_btn.setMinimumHeight(40)
            btn_layout.addWidget(audio_btn)

            sub_btn = QPushButton("上传字幕")
            sub_btn.setObjectName("uploadSubtitleBtn")
            sub_btn.setProperty("row", i)
            sub_btn.clicked.connect(self._on_upload_subtitle)
            sub_btn.setMinimumWidth(75)
            sub_btn.setMinimumHeight(40)
            btn_layout.addWidget(sub_btn)

            self._table.setCellWidget(i, 1, btn_widget)
            self._table.setRowHeight(i, 60)

    def _on_upload_audio(self):
        btn = self.sender()
        row = btn.property("row")
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择配音文件", "", "音频文件 (*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.wma)"
        )
        if file_path:
            while row >= len(self._items):
                self._items.append({"text": "", "audio_path": "", "subtitle_path": ""})
            self._items[row]["audio_path"] = file_path
            text = self._table.item(row, 0).text() if self._table.item(row, 0) else ""
            self._items[row]["text"] = text
            logger.info(f"行{row} 上传配音: {file_path}")
            self.data_changed.emit()

    def _on_upload_subtitle(self):
        btn = self.sender()
        row = btn.property("row")
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择字幕文件", "", "字幕文件 (*.srt)"
        )
        if file_path:
            while row >= len(self._items):
                self._items.append({"text": "", "audio_path": "", "subtitle_path": ""})
            self._items[row]["subtitle_path"] = file_path
            logger.info(f"行{row} 上传字幕: {file_path}")
            self.data_changed.emit()

    def _on_export(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not dir_path:
            return
        for i, item in enumerate(self._items):
            text = item.get("text", "") or (self._table.item(i, 0).text() if self._table.item(i, 0) else "")
            if not text.strip():
                continue
            file_path = os.path.join(dir_path, f"narration_{i+1}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text.strip())
            logger.info(f"导出配音文案: {file_path}")

    def get_items(self) -> List[dict]:
        items = []
        for i in range(len(self._items)):
            text = self._table.item(i, 0).text() if self._table.item(i, 0) else ""
            item = {
                "text": text,
                "audio_path": self._items[i].get("audio_path", "") if i < len(self._items) else "",
                "subtitle_path": self._items[i].get("subtitle_path", "") if i < len(self._items) else "",
            }
            items.append(item)
        return items

    def is_all_uploaded(self) -> bool:
        if not self._items:
            return False

        for i, item in enumerate(self._items):
            audio_path = item.get("audio_path", "")
            subtitle_path = item.get("subtitle_path", "")

            if not audio_path or not subtitle_path:
                return False

        return True

    def get_missing_items(self) -> List[str]:
        missing = []
        if not self._items:
            return missing

        for i, item in enumerate(self._items):
            audio_path = item.get("audio_path", "")
            subtitle_path = item.get("subtitle_path", "")

            issues = []
            if not audio_path:
                issues.append("配音")
            if not subtitle_path:
                issues.append("字幕")

            if issues:
                missing.append(f"第{i+1}行: {', '.join(issues)}")

        return missing

    def load_items(self, items: List[dict]):
        self._items = list(items)
        # will be rebuilt when set_scripts is called
