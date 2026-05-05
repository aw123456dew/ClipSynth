import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

logger = logging.getLogger("clip_synth.narrate_export")


class _ExportActionCard(QFrame):
    clicked = Signal()

    def __init__(self, icon: str, title: str, description: str, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self._setup_ui(icon, title, description)

    def _setup_ui(self, icon: str, title: str, description: str) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(20)

        icon_label = QLabel(icon)
        icon_label.setObjectName("exportCardIcon")
        icon_label.setFixedSize(48, 48)
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setObjectName("exportCardTitle")
        text_layout.addWidget(title_label)

        desc_label = QLabel(description)
        desc_label.setObjectName("exportCardDesc")
        desc_label.setWordWrap(True)
        text_layout.addWidget(desc_label)

        layout.addLayout(text_layout, stretch=1)

        arrow_label = QLabel("→")
        arrow_label.setObjectName("exportCardArrow")
        arrow_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(arrow_label)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class NarrateExportPage(QFrame):
    export_video = Signal()
    export_draft = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("narrateExportPage")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(24)

        title = QLabel("导出视频")
        title.setObjectName("wizardStepTitle")
        layout.addWidget(title)

        info = QLabel("解说配音已生成完毕，请选择导出方式")
        info.setObjectName("exportInfo")
        info.setWordWrap(True)
        layout.addWidget(info)

        card_container = QVBoxLayout()
        card_container.setSpacing(16)
        card_container.setAlignment(Qt.AlignCenter)

        self._export_video_card = _ExportActionCard(
            icon="🎬",
            title="直接导出视频",
            description="将解说配音与视频合并，直接导出为完整的MP4视频文件",
        )
        self._export_video_card.clicked.connect(self.export_video.emit)
        self._export_video_card.setObjectName("exportVideoCard")
        card_container.addWidget(self._export_video_card)

        self._export_draft_card = _ExportActionCard(
            icon="✂️",
            title="导出到剪映草稿",
            description="将解说配音与视频生成剪映草稿文件，可在剪映专业版中继续编辑",
        )
        self._export_draft_card.clicked.connect(self.export_draft.emit)
        self._export_draft_card.setObjectName("exportDraftCard")
        card_container.addWidget(self._export_draft_card)

        placeholder = QLabel("提示：功能开发中，按钮暂不可用")
        placeholder.setObjectName("exportPlaceholderHint")
        placeholder.setAlignment(Qt.AlignCenter)
        card_container.addWidget(placeholder)

        layout.addLayout(card_container, stretch=1)
        layout.addStretch()
