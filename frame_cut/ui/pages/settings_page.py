import logging

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from frame_cut.models.settings import AIModelSettings, AppSettings
from frame_cut.services.ai_service import AIModelConfig, AIService
from frame_cut.services.settings_service import SettingsService
from frame_cut.ui.components.toast import show_toast

logger = logging.getLogger("frame_cut.settings")


class ConnectionTestThread(QThread):
    result_ready = Signal(bool, str)

    def __init__(self, config: AIModelSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._config = config

    def run(self) -> None:
        logger.info(
            "开始测试连接: model=%s, base_url=%s",
            self._config.model_name,
            self._config.base_url,
        )
        try:
            service = AIService(
                AIModelConfig(
                    model_name=self._config.model_name,
                    api_key=self._config.api_key,
                    base_url=self._config.base_url,
                )
            )
            success, message = service.test_connection()
            logger.info("连接测试结果: success=%s, message=%s", success, message)
            self.result_ready.emit(success, message)
        except Exception as e:
            logger.error("连接测试异常: %s", str(e), exc_info=True)
            self.result_ready.emit(False, str(e))


class ModelConfigGroup(QGroupBox):
    def __init__(
        self,
        title: str,
        settings: AIModelSettings,
        parent: QWidget | None = None,
    ):
        super().__init__(title, parent)
        self._settings = settings
        self._test_thread: ConnectionTestThread | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QFormLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 24, 16, 16)

        self._model_name_input = QLineEdit()
        self._model_name_input.setPlaceholderText("例如 gpt-4o, deepseek-chat")
        self._model_name_input.setText(self._settings.model_name)
        layout.addRow("模型名称:", self._model_name_input)

        self._api_key_input = QLineEdit()
        self._api_key_input.setPlaceholderText("sk-...")
        self._api_key_input.setEchoMode(QLineEdit.Password)
        self._api_key_input.setText(self._settings.api_key)
        layout.addRow("API 密钥:", self._api_key_input)

        self._base_url_input = QLineEdit()
        self._base_url_input.setPlaceholderText("例如 https://api.openai.com/v1")
        self._base_url_input.setText(self._settings.base_url)
        layout.addRow("接口地址:", self._base_url_input)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._test_btn = QPushButton("测试连接")
        self._test_btn.setObjectName("testConnectionBtn")
        self._test_btn.clicked.connect(self._on_test_connection)
        btn_layout.addWidget(self._test_btn)

        self._test_status = QLabel("")
        self._test_status.setObjectName("testStatus")
        btn_layout.addWidget(self._test_status)

        layout.addRow("", btn_layout)

    def _on_test_connection(self) -> None:
        config = self.collect_settings()
        if not config.is_configured:
            show_toast(self, "请先填写所有字段再测试连接", "error")
            return

        logger.info("用户点击测试连接: %s", config.model_name)

        self._test_btn.setEnabled(False)
        self._test_status.setText("测试中...")
        self._test_status.setStyleSheet("color: #9ca3af;")

        self._test_thread = ConnectionTestThread(config)
        self._test_thread.result_ready.connect(self._on_test_result)
        self._test_thread.finished.connect(self._on_thread_finished)
        self._test_thread.start()

    @Slot(bool, str)
    def _on_test_result(self, success: bool, message: str) -> None:
        logger.info("收到连接测试结果: success=%s", success)
        self._test_btn.setEnabled(True)
        if success:
            self._test_status.setText("连接成功")
            self._test_status.setStyleSheet("color: #34d399;")
        else:
            self._test_status.setText("连接失败")
            self._test_status.setStyleSheet("color: #f87171;")
            show_toast(self, f"连接失败: {message}", "error", duration=4000)

    def _on_thread_finished(self) -> None:
        logger.info("连接测试线程已结束")

    def collect_settings(self) -> AIModelSettings:
        return AIModelSettings(
            model_name=self._model_name_input.text().strip(),
            api_key=self._api_key_input.text().strip(),
            base_url=self._base_url_input.text().strip().rstrip("/"),
        )

    def update_settings(self, settings: AIModelSettings) -> None:
        self._settings = settings
        self._model_name_input.setText(settings.model_name)
        self._api_key_input.setText(settings.api_key)
        self._base_url_input.setText(settings.base_url)


class SettingsPage(QFrame):
    settings_changed = Signal()

    def __init__(
        self,
        settings_service: SettingsService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._settings_service = settings_service
        self._settings = AppSettings()
        self._setup_ui()
        self._load_settings()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("settingsScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_content.setObjectName("settingsScrollContent")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(32, 24, 32, 24)
        scroll_layout.setSpacing(24)

        header = QLabel("系统配置")
        header.setObjectName("settingsHeader")
        scroll_layout.addWidget(header)

        self._text_model_group = ModelConfigGroup(
            "文案生成模型设置",
            self._settings.text_model,
        )
        scroll_layout.addWidget(self._text_model_group)

        self._vision_model_group = ModelConfigGroup(
            "视频分析模型设置",
            self._settings.vision_model,
        )
        scroll_layout.addWidget(self._vision_model_group)

        draft_group = QGroupBox("剪映草稿地址")
        draft_group.setObjectName("draftGroup")
        draft_layout = QVBoxLayout(draft_group)
        draft_layout.setContentsMargins(16, 24, 16, 16)
        draft_layout.setSpacing(12)

        draft_desc = QLabel("选择剪映草稿输出目录，用于存放生成的草稿文件")
        draft_desc.setObjectName("draftDesc")
        draft_layout.addWidget(draft_desc)

        draft_path_layout = QHBoxLayout()
        self._draft_path_input = QLineEdit()
        self._draft_path_input.setPlaceholderText("选择草稿输出目录...")
        self._draft_path_input.setText(self._settings.draft_output_dir)
        draft_path_layout.addWidget(self._draft_path_input, stretch=1)

        browse_btn = QPushButton("浏览...")
        browse_btn.setObjectName("browseBtn")
        browse_btn.clicked.connect(self._on_browse_draft_dir)
        draft_path_layout.addWidget(browse_btn)

        draft_layout.addLayout(draft_path_layout)

        scroll_layout.addWidget(draft_group)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        save_btn = QPushButton("保存配置")
        save_btn.setObjectName("saveSettingsBtn")
        save_btn.clicked.connect(self._on_save_settings)
        btn_layout.addWidget(save_btn)

        scroll_layout.addLayout(btn_layout)
        scroll_layout.addStretch()

        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area)

    def _on_browse_draft_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "选择草稿输出目录"
        )
        if directory:
            self._draft_path_input.setText(directory)

    def _load_settings(self) -> None:
        self._settings = self._settings_service.load()
        self._text_model_group.update_settings(self._settings.text_model)
        self._vision_model_group.update_settings(self._settings.vision_model)
        self._draft_path_input.setText(self._settings.draft_output_dir)

    def _on_save_settings(self) -> None:
        self._settings.text_model = self._text_model_group.collect_settings()
        self._settings.vision_model = self._vision_model_group.collect_settings()
        self._settings.draft_output_dir = self._draft_path_input.text().strip()

        self._settings_service.save(self._settings)
        show_toast(self, "系统配置已保存成功", "success")
        self.settings_changed.emit()

    @property
    def settings(self) -> AppSettings:
        return self._settings
