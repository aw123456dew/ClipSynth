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
from clip_synth.ui.pages.video_process_page import VideoProcessPage

logger = logging.getLogger(__name__)


class CoverExtractorWorker(QThread):
    """异步提取视频封面的工作线程"""
    cover_finished = Signal(str)  # 提取成功时返回封面路径
    failed = Signal()        # 提取失败
    
    def __init__(self, video_path: str, parent=None):
        super().__init__(parent)
        self._video_path = video_path
    
    def run(self):
        """执行封面提取"""
        cover_path = self._extract_first_frame(self._video_path)
        if cover_path:
            self.cover_finished.emit(cover_path)
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
    cover_extracted = Signal(str)  # 封面提取完成，通知刷新项目卡片

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
        from clip_synth.services.novel_mix_state_service import NovelMixStateService
        from clip_synth.services.novel_comic_state_service import NovelComicStateService
        self._novel_mix_state_service = NovelMixStateService()
        self._novel_comic_state_service = NovelComicStateService()
        self._cover_worker: CoverExtractorWorker | None = None
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
        self.cover_extracted.connect(self._narrate_page.refresh_project_cover)
        self._stack.addWidget(self._narrate_page)
        self._pages["short_drama_narrate"] = self._stack.count() - 1

        video_process_page = VideoProcessPage()
        self._stack.addWidget(video_process_page)
        self._pages["video_dedup"] = self._stack.count() - 1

        self._settings_page = SettingsPage(self._settings_service)
        self._stack.addWidget(self._settings_page)
        self._pages["settings"] = self._stack.count() - 1

        from clip_synth.ui.pages.short_drama_narrate_v2_page import ShortDramaNarrateV2Page
        self._narrate_v2_page = ShortDramaNarrateV2Page(self._narrate_project_state_service)
        self._narrate_v2_page.start_wizard.connect(self.switch_to_narrate_v2_wizard)
        self._narrate_v2_page.open_project.connect(self.open_narrate_v2_project)
        self.cover_extracted.connect(self._narrate_v2_page.refresh_project_cover)
        self._stack.addWidget(self._narrate_v2_page)
        self._pages["short_drama_narrate_v2"] = self._stack.count() - 1

        from clip_synth.ui.pages.novel_mix_project_list_page import NovelMixProjectListPage
        self._novel_mix_page = NovelMixProjectListPage(self._novel_mix_state_service)
        self._novel_mix_page.start_wizard.connect(self._switch_to_novel_mix_wizard)
        self._novel_mix_page.open_project.connect(self._open_novel_mix_project)
        self._stack.addWidget(self._novel_mix_page)
        self._pages["novel_mix"] = self._stack.count() - 1

        from clip_synth.ui.pages.novel_comic_project_list_page import NovelComicProjectListPage
        self._novel_comic_page = NovelComicProjectListPage(self._novel_comic_state_service)
        self._novel_comic_page.open_project.connect(self._open_novel_comic_project)
        self._novel_comic_page.start_chapter_page.connect(self._switch_to_novel_comic_chapter)
        self._stack.addWidget(self._novel_comic_page)
        self._pages["novel_comic"] = self._stack.count() - 1

    def switch_to(self, page_key: str) -> None:
        if page_key in self._pages:
            if page_key == "short_drama_mix":
                self._mix_page._load_projects()
            elif page_key == "short_drama_narrate":
                self._narrate_page._load_projects()
                self._narrate_page.sync_all_covers()
            elif page_key == "short_drama_narrate_v2":
                self._narrate_v2_page._load_projects()
                self._narrate_v2_page.sync_all_covers()
            elif page_key == "novel_mix":
                self._novel_mix_page._load_projects()
            elif page_key == "novel_comic":
                self._novel_comic_page._load_projects()
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

        project = self._narrate_project_state_service.create_project(
            video_paths, name=project_name, cover_path=cover_path,
        )

        # 没有上传封面时，异步提取视频第一帧作为封面
        if not cover_path and video_paths:
            self._async_extract_cover(video_paths[0], project.id)

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
        self.switch_to("short_drama_mix")

    def _on_wizard_cancelled(self) -> None:
        self._remove_wizard_from_stack()
        self.switch_to("short_drama_mix")

    def _on_narrate_wizard_finished(self) -> None:
        self._remove_wizard_from_stack()
        self.switch_to("short_drama_narrate")

    def _on_narrate_wizard_cancelled(self) -> None:
        self._remove_wizard_from_stack()
        self.switch_to("short_drama_narrate")

    def switch_to_narrate_v2_wizard(self, data) -> None:
        from clip_synth.ui.pages.narrate_v2.narrate_v2_wizard import NarrateV2Wizard

        video_paths, project_name = data

        project = self._narrate_project_state_service.create_project(
            video_paths, name=project_name, version=2,
        )
        if video_paths:
            self._async_extract_cover(video_paths[0], project.id)

        wizard_page = NarrateV2Wizard(
            project.id, project_name, video_paths, self._settings_service,
            self._narrate_project_state_service,
        )
        wizard_page.finished.connect(self._on_narrate_v2_wizard_finished)
        wizard_page.cancelled.connect(self._on_narrate_v2_wizard_cancelled)
        self._stack.addWidget(wizard_page)
        self._stack.setCurrentIndex(self._stack.count() - 1)

    def open_narrate_v2_project(self, project_id: str) -> None:
        from clip_synth.ui.pages.narrate_v2.narrate_v2_wizard import NarrateV2Wizard

        project = self._narrate_project_state_service.load_project(project_id)
        if not project:
            return

        video_paths = [v.video_path for v in project.videos]
        wizard_page = NarrateV2Wizard(
            project.id, project.name, video_paths, self._settings_service,
            self._narrate_project_state_service,
        )
        wizard_page.finished.connect(self._on_narrate_v2_wizard_finished)
        wizard_page.cancelled.connect(self._on_narrate_v2_wizard_cancelled)
        self._stack.addWidget(wizard_page)
        self._stack.setCurrentIndex(self._stack.count() - 1)

    def _on_narrate_v2_wizard_finished(self) -> None:
        self._remove_wizard_from_stack()
        self.switch_to("short_drama_narrate_v2")

    def _on_narrate_v2_wizard_cancelled(self) -> None:
        self._remove_wizard_from_stack()
        self.switch_to("short_drama_narrate_v2")

    def _remove_wizard_from_stack(self) -> None:
        sender = self.sender()
        if sender:
            sender.deleteLater()
    
    def _async_extract_cover(self, video_path: str, project_id: str) -> None:
        """异步提取视频封面，全局后台运行，不随页面切换停止"""
        if self._cover_worker is not None:
            self._cover_worker.quit()
            self._cover_worker.wait(2000)
            self._cover_worker.deleteLater()
        self._cover_worker = CoverExtractorWorker(video_path)
        self._cover_worker.cover_finished.connect(
            lambda cover_path, pid=project_id: self._on_cover_extracted(cover_path, pid))
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
                self.cover_extracted.emit(project_id)
        except Exception as e:
            logger.error("更新项目封面失败: %s", e)

    def _switch_to_novel_mix_wizard(self, project_id: str) -> None:
        from clip_synth.ui.pages.novel_mix_wizard import NovelMixWizard

        project = self._novel_mix_state_service.load_project(project_id)
        if not project:
            return
        wizard_page = NovelMixWizard(
            project, self._novel_mix_state_service, self._settings_service,
            db_manager=self._db_manager,
        )
        wizard_page.finished.connect(self._on_novel_mix_wizard_finished)
        wizard_page.cancelled.connect(self._on_novel_mix_wizard_cancelled)
        self._stack.addWidget(wizard_page)
        self._stack.setCurrentIndex(self._stack.count() - 1)

    def _open_novel_mix_project(self, project_id: str) -> None:
        self._switch_to_novel_mix_wizard(project_id)

    def _on_novel_mix_wizard_finished(self) -> None:
        self._remove_wizard_from_stack()
        self.switch_to("novel_mix")

    def _on_novel_mix_wizard_cancelled(self) -> None:
        self._remove_wizard_from_stack()
        self.switch_to("novel_mix")

    def _open_novel_comic_project(self, project_id: str) -> None:
        self._switch_to_novel_comic_chapter(project_id)

    def _switch_to_novel_comic_chapter(self, project_id: str) -> None:
        from clip_synth.ui.pages.novel_comic_chapter_page import NovelComicChapterPage

        project = self._novel_comic_state_service.load_project(project_id)
        if not project:
            return
        chapter_page = NovelComicChapterPage(project, self._novel_comic_state_service)
        chapter_page.back_to_list.connect(self._on_novel_comic_chapter_back)
        chapter_page.open_generate_page.connect(self._switch_to_novel_comic_generate)
        chapter_page.open_comic_video_dub.connect(self._switch_to_comic_video_dub)
        self._stack.addWidget(chapter_page)
        self._stack.setCurrentIndex(self._stack.count() - 1)

    def _switch_to_novel_comic_generate(self, project_id: str, episode_num: int) -> None:
        from clip_synth.ui.pages.novel_comic_generate_page import NovelComicGeneratePage

        generate_page = NovelComicGeneratePage(
            project_id, episode_num,
            self._novel_comic_state_service,
            self._settings_service,
        )
        generate_page.back_to_chapters.connect(self._on_novel_comic_generate_back)
        self._stack.addWidget(generate_page)
        self._stack.setCurrentIndex(self._stack.count() - 1)

    def _on_novel_comic_chapter_back(self) -> None:
        self._remove_wizard_from_stack()
        self.switch_to("novel_comic")

    def _on_novel_comic_generate_back(self) -> None:
        self._remove_wizard_from_stack()

    def _switch_to_comic_video_dub(self, project_id: str, episode_num: int) -> None:
        from clip_synth.ui.pages.comic_video_page import ComicVideoDubPage

        project = self._novel_comic_state_service.load_project(project_id)
        if not project:
            return
        chapter_text = ""
        if episode_num <= len(project.chapters):
            chapter_text = project.chapters[episode_num - 1].text

        dub_page = ComicVideoDubPage(
            project_id, episode_num, chapter_text,
            self._settings_service,
        )
        self._comic_video_dub_page = dub_page
        dub_page.next_page.connect(self._switch_to_comic_video_image)
        dub_page.back_to_chapters.connect(self._on_novel_comic_video_back)
        self._stack.addWidget(dub_page)
        self._stack.setCurrentIndex(self._stack.count() - 1)

    def _switch_to_comic_video_image(self, project_id: str, episode_num: int) -> None:
        from clip_synth.ui.pages.comic_video_page import ComicVideoImagePage

        image_page = ComicVideoImagePage(
            project_id, episode_num,
            self._settings_service,
        )
        image_page.back_to_chapters.connect(self._on_novel_comic_video_back)
        self._stack.addWidget(image_page)
        self._stack.setCurrentIndex(self._stack.count() - 1)

    def _on_novel_comic_video_back(self) -> None:
        if hasattr(self, '_comic_video_dub_page') and self._comic_video_dub_page:
            self._comic_video_dub_page.deleteLater()
            self._comic_video_dub_page = None
        self._remove_wizard_from_stack()
