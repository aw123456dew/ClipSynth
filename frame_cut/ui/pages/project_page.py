from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class ProjectPage(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.StyledPanel)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        header_label = QLabel("Project")
        header_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(header_label)

        info_group = QGroupBox("Project Info")
        info_layout = QFormLayout(info_group)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("Untitled Project")
        info_layout.addRow("Name:", self._name_input)

        self._description_input = QTextEdit()
        self._description_input.setMaximumHeight(80)
        self._description_input.setPlaceholderText("Project description...")
        info_layout.addRow("Description:", self._description_input)

        layout.addWidget(info_group)

        clips_group = QGroupBox("Clips")
        clips_layout = QVBoxLayout(clips_group)

        self._clip_list = QListWidget()
        clips_layout.addWidget(self._clip_list)

        clip_btn_layout = QHBoxLayout()
        add_clip_btn = QPushButton("Add Clip")
        clip_btn_layout.addWidget(add_clip_btn)

        remove_clip_btn = QPushButton("Remove")
        clip_btn_layout.addWidget(remove_clip_btn)

        clips_layout.addLayout(clip_btn_layout)
        layout.addWidget(clips_group, stretch=1)
