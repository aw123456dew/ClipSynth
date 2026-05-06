import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from clip_synth.core.database import DatabaseManager
from clip_synth.services.ai_service import AIModelConfig, AIService
from clip_synth.services.narrate_project_state_service import NarrateProjectStateService
from clip_synth.services.project_state_service import ProjectStateService
from clip_synth.services.settings_service import SettingsService
from clip_synth.ui.pages.settings_page import SettingsPage
from clip_synth.ui.pages.short_drama_mix_page import ShortDramaMixPage
from clip_synth.ui.pages.short_drama_narrate_page import ShortDramaNarratePage
from clip_synth.ui.pages.video_dedup_page import VideoDedupPage

logger = logging.getLogger(__name__)


class CoverExtractorWorker(QThread):
    """异步提取视频封面的工作线程"""
    finished = Signal(str)  # 提取成功时返回封面路径
    failed = Signal()        # 提取失败
    
    def __init__(self, video_path: str, parent=None):
        super().__init__(parent)
        self._video_path = video_path
    
    def run(self):
        """执行封面提取"""
        cover_path = self._extract_first_frame(self._video_path)
        if cover_path:
            self.finished.emit(cover_path)
        else:
            self.failed.emit()
    
    @staticmethod
    def _extract_first_frame(video_path: str) -> str | None:
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
        
        # 设置参数避免弹出黑框
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=False, timeout=30, **kwargs)
            if result.returncode == 0 and Path(output_path).exists():
                logger.info("已提取视频封面: %s", output_path)
                return output_path
            else:
                logger.warning("封面提取失败: %s", result.stderr.decode('utf-8', errors='ignore') if result.stderr else "unknown")
        except subprocess.TimeoutExpired:
            logger.warning("封面提取超时")
        except Exception as e:
            logger.error("封面提取异常: %s", e)
        
        return None


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
        narrate_project_state_service: NarrateProjectStateService | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("contentArea")
        self._settings_service = settings_service
        self._db_manager = db_manager
        self._project_state_service = project_state_service
        self._narrate_project_state_service = narrate_project_state_service or NarrateProjectStateService()
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

        self._narrate_page = ShortDramaNarratePage(self._narrate_project_state_service)
        self._narrate_page.start_narrate_wizard.connect(self.switch_to_narrate_wizard)
        self._narrate_page.open_narrate_project.connect(self.open_narrate_project)
        self._stack.addWidget(self._narrate_page)
        self._pages["short_drama_narrate"] = self._stack.count() - 1

        dedup_page = VideoDedupPage()
        self._stack.addWidget(dedup_page)
        self._pages["video_dedup"] = self._stack.count() - 1

        self._settings_page = SettingsPage(self._settings_service)
        self._stack.addWidget(self._settings_page)
        self._pages["settings"] = self._stack.count() - 1

    def switch_to(self, page_key: str) -> None:
        if page_key in self._pages:
            self._stack.setCurrentIndex(self._pages[page_key])

    def switch_to_project_wizard(self, data) -> None:
        from clip_synth.ui.pages.smart_clipping_wizard import SmartClippingWizard

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
        
        # 设置参数避免弹出黑框
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=False, timeout=30, **kwargs)
            if result.returncode == 0 and Path(output_path).exists():
                logger.info("已提取视频封面: %s", output_path)
                return output_path
        except Exception as e:
            logger.warning("提取视频封面失败: %s", str(e))

        return None

    def open_smart_project(self, project_id: str) -> None:
        from clip_synth.ui.pages.smart_clipping_wizard import SmartClippingWizard

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

    def switch_to_narrate_wizard(self, data) -> None:
        from clip_synth.ui.pages.smart_narrate_wizard import SmartNarrateWizard

        video_paths, project_name, cover_path = data

        # 先检查缓存中是否已有封面，避免不必要的等待
        cached_cover = None
        if not cover_path and video_paths:
            cache_dir = Path(__file__).resolve().parent.parent.parent / "cache" / "covers"
            video_name = Path(video_paths[0]).stem
            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in video_name)
            cached_cover_path = cache_dir / f"{safe_name}_cover.jpg"
            if cached_cover_path.exists():
                cover_path = str(cached_cover_path)

        project = self._narrate_project_state_service.create_project(
            video_paths, name=project_name, cover_path=cover_path,
        )
        vision_ai = self._create_vision_ai_service()
        text_ai = self._create_ai_service()
        wizard_page = SmartNarrateWizard(
            project, self._narrate_project_state_service, vision_ai, text_ai_service=text_ai,
            db_manager=self._db_manager,
        )
        wizard_page.finished.connect(self._on_narrate_wizard_finished)
        wizard_page.cancelled.connect(self._on_narrate_wizard_cancelled)
        self._stack.addWidget(wizard_page)
        self._stack.setCurrentIndex(self._stack.count() - 1)

        # 异步提取封面（不阻塞UI）
        if not cover_path and video_paths:
            self._async_extract_cover(video_paths[0], project.id)

    def open_narrate_project(self, project_id: str) -> None:
        from clip_synth.ui.pages.smart_narrate_wizard import SmartNarrateWizard

        project = self._narrate_project_state_service.load_project(project_id)
        if project:
            vision_ai = self._create_vision_ai_service()
            text_ai = self._create_ai_service()
            wizard_page = SmartNarrateWizard(
                project, self._narrate_project_state_service, vision_ai, text_ai_service=text_ai,
                db_manager=self._db_manager,
            )
            wizard_page.finished.connect(self._on_narrate_wizard_finished)
            wizard_page.cancelled.connect(self._on_narrate_wizard_cancelled)
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

    def _on_narrate_wizard_finished(self) -> None:
        self._remove_wizard_from_stack()
        self._narrate_page._load_projects()
        self.switch_to("short_drama_narrate")

    def _on_narrate_wizard_cancelled(self) -> None:
        self._remove_wizard_from_stack()
        self._narrate_page._load_projects()
        self.switch_to("short_drama_narrate")

    def _remove_wizard_from_stack(self) -> None:
        sender = self.sender()
        if sender:
            sender.deleteLater()
    
    def _async_extract_cover(self, video_path: str, project_id: str) -> None:
        """异步提取视频封面，提取完成后更新项目"""
        if hasattr(self, '_cover_worker') and self._cover_worker is not None:
            self._cover_worker.deleteLater()
        self._cover_worker = CoverExtractorWorker(video_path)
        self._cover_worker.finished.connect(lambda cover_path: self._on_cover_extracted(cover_path, project_id))
        self._cover_worker.failed.connect(lambda: logger.debug("封面提取失败"))
        self._cover_worker.start()
    
    def _on_cover_extracted(self, cover_path: str, project_id: str) -> None:
        """封面提取完成后的回调"""
        try:
            project = self._narrate_project_state_service.load_project(project_id)
            if project:
                project.cover_path = cover_path
                self._narrate_project_state_service.save_project(project)
                logger.info("异步更新项目封面: %s", project_id)
        except Exception as e:
            logger.error("更新项目封面失败: %s", e)
