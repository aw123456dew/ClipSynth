import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("clip_synth.narrate_v2")


class ExportPageV2(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(24)

        title = QLabel("导出")
        title.setObjectName("aiStyleTitle")
        layout.addWidget(title)

        desc = QLabel("将解说配音与视频合并，导出为完整视频或剪映草稿。")
        desc.setObjectName("dialogFieldHint")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(16)
        cards_row.setAlignment(Qt.AlignCenter)

        export_video_card = QFrame()
        export_video_card.setObjectName("exportVideoCard")
        export_video_card.setMinimumSize(280, 100)
        ev_layout = QVBoxLayout(export_video_card)
        ev_layout.setAlignment(Qt.AlignCenter)
        ev_label = QLabel("🎬\n直接导出视频")
        ev_label.setAlignment(Qt.AlignCenter)
        ev_label.setStyleSheet("color: #94a3b8; font-size: 14px;")
        ev_layout.addWidget(ev_label)
        cards_row.addWidget(export_video_card)

        export_draft_card = QFrame()
        export_draft_card.setObjectName("exportDraftCard")
        export_draft_card.setMinimumSize(280, 100)
        ed_layout = QVBoxLayout(export_draft_card)
        ed_layout.setAlignment(Qt.AlignCenter)
        ed_label = QLabel("✂️\n导出到剪映草稿")
        ed_label.setAlignment(Qt.AlignCenter)
        ed_label.setStyleSheet("color: #94a3b8; font-size: 14px;")
        ed_layout.addWidget(ed_label)
        cards_row.addWidget(export_draft_card)

        layout.addLayout(cards_row)
        layout.addStretch()
