from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class ProjectWizardPage(QFrame):
    def __init__(self, video_paths: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self._video_paths = video_paths
        self.setObjectName("projectWizardPage")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("wizardHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(24, 16, 24, 16)

        title_label = QLabel("项目创建流程")
        title_label.setObjectName("wizardTitle")
        header_layout.addWidget(title_label)

        desc_label = QLabel(f"已选择 {len(self._video_paths)} 个视频文件")
        desc_label.setObjectName("wizardDesc")
        header_layout.addWidget(desc_label)

        layout.addWidget(header)

        content = QFrame()
        content.setObjectName("wizardContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(24, 16, 24, 16)
        content_layout.setAlignment(Qt.AlignCenter)

        placeholder_label = QLabel("项目创建流程页面（待实现）")
        placeholder_label.setObjectName("wizardPlaceholder")
        placeholder_label.setAlignment(Qt.AlignCenter)
        content_layout.addWidget(placeholder_label)

        layout.addWidget(content, stretch=1)
