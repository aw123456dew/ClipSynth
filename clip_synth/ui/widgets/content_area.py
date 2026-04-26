import logging
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from clip_synth.core.database import DatabaseManager
from clip_synth.services.ai_service import AIModelConfig, AIService
from clip_synth.services.project_state_service import ProjectStateService
from clip_synth.services.settings_service import SettingsService
from clip_synth.ui.pages.settings_page import SettingsPage
from clip_synth.ui.pages.short_drama_mix_page import ShortDramaMixPage
from clip_synth.ui.pages.smart_clipping_wizard import SmartClippingWizard

logger = logging.getLogger(__name__)


class PlaceholderPage(QFrame):
    def __init__(self, title: str, description: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("placeholderPage")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(16)

        icon_label = QLabel("\u25b6")
        icon_label.setObjectName("placeholderIcon")
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setObjectName("placeholderTitle")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        desc_label = QLabel(description)
        desc_label.setObjectName("placeholderDesc")
        desc_label.setAlignment(Qt.AlignCenter)
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)


class ContentArea(QFrame):
    def __init__(
        self,
        settings_service: SettingsService,
        db_manager: DatabaseManager,
        project_state_service: ProjectStateService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("contentArea")
        self._settings_service = settings_service
        self._db_manager = db_manager
        self._project_state_service = project_state_service
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        self._pages: dict[str, int] = {}

        self._mix_page = ShortDramaMixPage(self._db_manager, self._project_state_service)
        self._mix_page.start_project_wizard.connect(self.switch_to_project_wizard)
        self._mix_page.open_smart_project.connect(self.open_smart_project)
        self._stack.addWidget(self._mix_page)
        self._pages["short_drama_mix"] = self._stack.count() - 1

        narrate_page = PlaceholderPage("短剧解说", "AI 自动生成解说文案并配音")
        self._stack.addWidget(narrate_page)
        self._pages["short_drama_narrate"] = self._stack.count() - 1

        self._settings_page = SettingsPage(self._settings_service)
        self._stack.addWidget(self._settings_page)
        self._pages["settings"] = self._stack.count() - 1

    def switch_to(self, page_key: str) -> None:
        if page_key in self._pages:
            self._stack.setCurrentIndex(self._pages[page_key])

    def switch_to_project_wizard(self, data) -> None:
        video_paths, project_name, cover_path = data

        if not cover_path and video_paths:
            cover_path = self._extract_first_frame(video_paths[0])

        project = self._project_state_service.create_project(
            video_paths, {}, name=project_name, cover_path=cover_path,
        )
        vision_ai = self._create_vision_ai_service()
        text_ai = self._create_ai_service()
        wizard_page = SmartClippingWizard(
            project, self._project_state_service, vision_ai, text_ai_service=text_ai,
        )
        wizard_page.finished.connect(self._on_wizard_finished)
        wizard_page.cancelled.connect(self._on_wizard_cancelled)
        self._stack.addWidget(wizard_page)
        self._stack.setCurrentIndex(self._stack.count() - 1)

    def _extract_first_frame(self, video_path: str) -> str | None:
        """提取视频第一帧作为封面"""
        cache_dir = Path(__file__).resolve().parent.parent.parent / "cache" / "covers"
        cache_dir.mkdir(parents=True, exist_ok=True)

        video_name = Path(video_path).stem
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in video_name)
        output_path = str(cache_dir / f"{safe_name}_cover.jpg")

        if Path(output_path).exists():
            return output_path

        cmd = [
            "ffmpeg",
            "-ss", "0",
            "-i", video_path,
            "-vframes", "1",
            "-vf", "scale=400:-1",
            "-q:v", "3",
            "-y",
            output_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=False, timeout=30)
            if result.returncode == 0 and Path(output_path).exists():
                logger.info("已提取视频封面: %s", output_path)
                return output_path
        except Exception as e:
            logger.warning("提取视频封面失败: %s", str(e))

        return None

    def open_smart_project(self, project_id: str) -> None:
        project = self._project_state_service.load_project(project_id)
        if project:
            vision_ai = self._create_vision_ai_service()
            text_ai = self._create_ai_service()
            wizard_page = SmartClippingWizard(
                project, self._project_state_service, vision_ai, text_ai_service=text_ai,
            )
            wizard_page.finished.connect(self._on_wizard_finished)
            wizard_page.cancelled.connect(self._on_wizard_cancelled)
            self._stack.addWidget(wizard_page)
            self._stack.setCurrentIndex(self._stack.count() - 1)

    def _create_ai_service(self) -> AIService:
        settings = self._settings_service.load()
        text_config = settings.text_model
        config = AIModelConfig(
            model_name=text_config.model_name,
            api_key=text_config.api_key,
            base_url=text_config.base_url,
        )
        return AIService(config)

    def _create_vision_ai_service(self) -> AIService:
        settings = self._settings_service.load()
        vision_config = settings.vision_model
        config = AIModelConfig(
            model_name=vision_config.model_name,
            api_key=vision_config.api_key,
            base_url=vision_config.base_url,
        )
        return AIService(config)

    def _on_wizard_finished(self) -> None:
        self._remove_wizard_from_stack()
        self._mix_page._load_projects()
        self.switch_to("short_drama_mix")

    def _on_wizard_cancelled(self) -> None:
        self._remove_wizard_from_stack()
        self._mix_page._load_projects()
        self.switch_to("short_drama_mix")

    def _remove_wizard_from_stack(self) -> None:
        sender = self.sender()
        if sender:
            sender.deleteLater()
