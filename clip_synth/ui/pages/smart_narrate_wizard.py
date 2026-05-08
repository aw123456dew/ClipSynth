import logging
import os
import traceback

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from clip_synth.models.narrate_project_state import NarrateProjectState
from clip_synth.services.ai_service import AIService
from clip_synth.services.clipping_analysis_service import ClippingAnalysisService
from clip_synth.services.doubao_tts_service import DoubaoTTSWorker
from clip_synth.services.narrate_export_service import NarrateExportService
from clip_synth.services.narrate_project_state_service import NarrateProjectStateService
from clip_synth.services.video_analysis_service import VideoAnalysisService

logger = logging.getLogger("clip_synth.smart_narrate_wizard")


class TTSTaskWorker(QThread):
    progress = Signal(str)
    tts_finished = Signal()
    error = Signal(str)

    def __init__(
        self,
        scripts: list,
        voice_type: str,
        speed: float,
        pitch: float,
        volume: float,
        silence_duration: float,
        emotion: str,
        language: str,
        doubao_settings,
        output_dir: str,
        parent=None,
    ):
        super().__init__(parent)
        self._scripts = scripts
        self._voice_type = voice_type
        self._speed = speed
        self._pitch = pitch
        self._volume = volume
        self._silence_duration = silence_duration
        self._emotion = emotion
        self._language = language
        self._doubao_settings = doubao_settings
        self._output_dir = output_dir
        self._audio_paths = []
        self._timestamps = {}  # 存储时间戳信息

    def run(self):
        try:
            worker = DoubaoTTSWorker(self._doubao_settings)
            os.makedirs(self._output_dir, exist_ok=True)

            for i, script in enumerate(self._scripts):
                text = script.get("narration_script", "") or script.get("narration", "") or script.get("text", "")
                if not text:
                    continue

                self.progress.emit(f"正在生成第 {i+1}/{len(self._scripts)} 段配音...")
                audio_path = os.path.join(self._output_dir, f"narration_{i}.mp3")

                success, timestamps = worker.tts_with_timestamps(
                    text=text,
                    voice_type=self._voice_type,
                    output_path=audio_path,
                    speed=self._speed,
                    pitch=self._pitch,
                    volume=self._volume,
                    silence_duration=self._silence_duration,
                    emotion=self._emotion,
                    language=self._language,
                )

                if not success:
                    self.error.emit(f"第 {i+1} 段配音生成失败")
                    return

                audio_info = {"index": i, "path": audio_path, "text": text, **script}
                if timestamps:
                    audio_info["timestamps"] = timestamps
                    self._timestamps[i] = timestamps
                self._audio_paths.append(audio_info)

            self.progress.emit("配音生成完成")
            self.tts_finished.emit()
        except Exception as e:
            logger.error(f"TTS生成异常: {e}", exc_info=True)
            self.error.emit(str(e))

    def get_audio_paths(self):
        return self._audio_paths

    def get_timestamps(self):
        return self._timestamps


class SmartNarrateWizard(QFrame):
    finished = Signal()
    cancelled = Signal()

    def __init__(
        self,
        project: NarrateProjectState,
        narrate_project_state_service: NarrateProjectStateService,
        ai_service: AIService,
        text_ai_service: AIService | None = None,
        db_manager=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project = project
        self._narrate_project_state_service = narrate_project_state_service
        self._analysis_service = VideoAnalysisService(ai_service)
        self._text_ai_service = text_ai_service or ai_service
        self._db_manager = db_manager
        self._tts_worker = None
        self._audio_output_dir: str = ""
        self._generated_audio_files: list = []
        self._current_step = self._project.current_step
        self._total_steps = 5

        export_dir = os.path.join(
            str(self._narrate_project_state_service.projects_dir),
            self._project.id,
        )
        self._export_service = NarrateExportService(export_dir)

        logger.info(f"SmartNarrateWizard 初始化: current_step={self._current_step}, project={self._project.id}")

        self.setObjectName("smartNarrateWizard")
        self._setup_ui()
        self._update_step_indicators()

    def _init_export_page(self) -> None:
        self._export_page.set_project(self._project, self._export_service)

    def _g_nav_ok(self) -> None:
        self._next_btn.setEnabled(True)
        self._prev_btn.setEnabled(self._current_step > 0)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("wizardHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 24, 0, 24)

        self._step_indicators = []
        self._step_numbers = []
        self._step_texts = []

        steps = [
            "上传字幕",
            "AI视频分析",
            "生成解说文案",
            "选择配音",
            "导出",
        ]

        for i, step_text in enumerate(steps):
            step_container = QFrame()
            step_layout = QHBoxLayout(step_container)
            step_layout.setContentsMargins(0, 0, 0, 0)
            step_layout.setSpacing(12)

            step_num = QLabel(str(i + 1))
            step_num.setObjectName("stepNumber")
            step_layout.addWidget(step_num)
            self._step_numbers.append(step_num)

            step_label = QLabel(step_text)
            step_label.setObjectName("stepText")
            step_layout.addWidget(step_label)
            self._step_texts.append(step_label)

            self._step_indicators.append(step_container)
            header_layout.addWidget(step_container)

            if i < len(steps) - 1:
                separator = QLabel("")
                separator.setObjectName("stepSeparator")
                separator.setMinimumWidth(40)
                header_layout.addWidget(separator)

        header_layout.addStretch()
        layout.addWidget(header)

        self._stack = QStackedWidget()

        from clip_synth.ui.pages.upload_subtitle_page import UploadSubtitlePage

        self._upload_page = UploadSubtitlePage(self._project.videos)
        self._stack.addWidget(self._upload_page)

        from clip_synth.ui.pages.ai_analysis_page import AiAnalysisPage

        self._ai_page = AiAnalysisPage(
            self._project, self._analysis_service, self._narrate_project_state_service,
        )
        self._ai_page.ready_for_next.connect(self._on_ai_ready)
        self._stack.addWidget(self._ai_page)

        from clip_synth.ui.pages.clipping_method_page import ClippingMethodPage

        clipping_service = ClippingAnalysisService(self._text_ai_service)
        self._method_page = ClippingMethodPage(
            self._project, clipping_service,
            project_state_service=self._narrate_project_state_service,
        )
        self._method_page.ready_for_next.connect(self._on_method_ready)
        self._stack.addWidget(self._method_page)

        from clip_synth.ui.pages.voice_selection_page import VoiceSelectionPage
        from clip_synth.services.settings_service import SettingsService

        self._settings_service = SettingsService(self._db_manager) if self._db_manager else None
        self._voice_page = VoiceSelectionPage(settings_service=self._settings_service)
        self._stack.addWidget(self._voice_page)

        from clip_synth.ui.pages.narrate_export_page import NarrateExportPage

        self._export_page = NarrateExportPage(self._settings_service)
        self._stack.addWidget(self._export_page)

        layout.addWidget(self._stack, stretch=1)

        self._status_label = QLabel("")
        self._status_label.setObjectName("wizardStatusLabel")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.hide()
        layout.addWidget(self._status_label)

        footer = QFrame()
        footer.setObjectName("wizardFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 16, 24, 32)

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setObjectName("wizardCancelBtn")
        self._cancel_btn.clicked.connect(self._on_cancel)
        footer_layout.addWidget(self._cancel_btn)

        footer_layout.addStretch()

        self._prev_btn = QPushButton("上一步")
        self._prev_btn.setObjectName("wizardPrevBtn")
        self._prev_btn.clicked.connect(self._on_prev)
        self._prev_btn.setEnabled(False)
        footer_layout.addWidget(self._prev_btn)

        self._next_btn = QPushButton("下一步")
        self._next_btn.setObjectName("wizardNextBtn")
        self._next_btn.clicked.connect(self._on_next)
        footer_layout.addWidget(self._next_btn)

        self._finish_btn = QPushButton("完成")
        self._finish_btn.setObjectName("wizardFinishBtn")
        self._finish_btn.clicked.connect(self._on_finish)
        self._finish_btn.hide()
        footer_layout.addWidget(self._finish_btn)

        layout.addWidget(footer)

        self._init_export_page()
        self._sync_ui()

    def _sync_ui(self) -> None:
        self._stack.setCurrentIndex(self._current_step)
        self._update_step_indicators()
        self._init_page_data()

    def _init_page_data(self):
        if self._current_step == 3:
            self._voice_page.set_scripts(self._project.narration_scripts, self._project.original_sound_ratio)
            if self._project.tts_engine == "custom":
                self._voice_page.load_custom_state(self._project.custom_audio_files)
        elif self._current_step == 4:
            if self._project.tts_engine == "custom" and self._project.custom_audio_files:
                audio_files = []
                for item in self._project.custom_audio_files:
                    audio_files.append({
                        "path": item.get("audio_path", ""),
                        "audio_path": item.get("audio_path", ""),
                        "text": item.get("text", ""),
                        "subtitle_path": item.get("subtitle_path", ""),
                        "timestamps": [],
                    })
                self._project.audio_files = audio_files
                self._save_project()
            self._export_page.set_project(self._project, self._export_service)
        self._update_nav_buttons()

    def _update_step_indicators(self):
        for i in range(self._total_steps):
            is_active = i == self._current_step
            is_done = i < self._current_step

            self._step_numbers[i].setProperty("active", is_active)
            self._step_numbers[i].setProperty("done", is_done)
            self._step_numbers[i].style().unpolish(self._step_numbers[i])
            self._step_numbers[i].style().polish(self._step_numbers[i])

            self._step_texts[i].setProperty("active", is_active)
            self._step_texts[i].setProperty("done", is_done)
            self._step_texts[i].style().unpolish(self._step_texts[i])
            self._step_texts[i].style().polish(self._step_texts[i])

    def _update_nav_buttons(self):
        self._prev_btn.setEnabled(self._current_step > 0)

        if self._current_step == self._total_steps - 1:
            self._next_btn.hide()
            self._finish_btn.hide()
        else:
            self._next_btn.show()
            self._finish_btn.hide()
            self._next_btn.setEnabled(True)

    def _on_prev(self):
        if self._current_step > 0:
            self._current_step -= 1
            self._project.current_step = self._current_step
            self._save_project()
            self._sync_ui()
            self._status_label.hide()

    def _advance(self) -> None:
        if self._current_step >= self._total_steps - 1:
            return
        self._current_step += 1
        self._project.current_step = self._current_step
        self._save_project()
        self._sync_ui()

        if self._current_step == 1:
            self._next_btn.setEnabled(False)
            self._ai_page.check_ready()

    def _on_next(self):
        try:
            if self._current_step >= self._total_steps - 1:
                return

            if self._current_step == 0:
                subtitles = self._upload_page.get_subtitles()
                for video_state in self._project.videos:
                    video_state.subtitle_path = subtitles.get(video_state.video_path)
                self._save_project()
                self._advance()
            elif self._current_step == 1:
                if not self._project.is_all_videos_ready():
                    logger.warning("视频分析未完成，无法继续")
                    return
                self._save_project()
                self._advance()
            elif self._current_step == 2:
                self._method_page.save_state()
                if not self._project.narration_scripts:
                    QMessageBox.warning(
                        self,
                        "提示",
                        "请先生成解说文案后再进入下一步"
                    )
                    return
                self._save_project()
                self._advance()
            elif self._current_step == 3:
                # 如果使用自定义配音，检查是否所有配音和字幕都已上传
                if self._project.tts_engine == "custom":
                    if not self._voice_page.is_custom_voiceover_ready():
                        missing_items = self._voice_page.get_missing_custom_items()
                        message = "<html><head/><body>"
                        message += "<p style='margin-bottom: 12px; font-size: 14px;'>请先上传完整的配音和字幕文件：</p>"
                        message += "<ul style='margin-left: 20px; font-size: 13px;'>"
                        for item in missing_items:
                            message += f"<li style='margin-bottom: 4px;'>{item}</li>"
                        message += "</ul>"
                        message += "</body></html>"
                        
                        msg_box = QMessageBox(self)
                        msg_box.setIcon(QMessageBox.Warning)
                        msg_box.setWindowTitle("提示")
                        msg_box.setText(message)
                        msg_box.setStyleSheet("""
                            QMessageBox {
                                background-color: #0f1320;
                                color: #c8d6e5;
                                font-family: 'Microsoft YaHei';
                            }
                            QMessageBox QLabel {
                                color: #c8d6e5;
                            }
                            QMessageBox QPushButton {
                                background-color: #3b82f6;
                                color: white;
                                border: none;
                                border-radius: 4px;
                                padding: 8px 24px;
                                min-width: 80px;
                            }
                            QMessageBox QPushButton:hover {
                                background-color: #2563eb;
                            }
                        """)
                        msg_box.exec()
                        return
                
                if self._project.narration_scripts:
                    self._on_generate_tts()
                else:
                    logger.warning("没有解说文案，跳过配音直接进入导出页")
                    self._advance()
        except Exception as e:
            logger.error(f"下一步操作失败: {e}\n{traceback.format_exc()}")
            self._g_nav_ok()

    def _on_generate_tts(self):
        voice_settings = self._voice_page.get_settings()
        tts_engine = voice_settings.get("tts_engine", "doubao")

        if tts_engine == "custom":
            custom_items = voice_settings.get("custom_items", [])
            self._project.tts_engine = "custom"
            self._project.custom_audio_files = custom_items
            self._project.audio_files = []
            self._generated_audio_files = []
            self._save_project()
            logger.info("自定义配音: %d条", len(custom_items))
            self._status_label.setText("自定义配音已就绪")
            self._g_nav_ok()
            self._advance()
            self._status_label.hide()
            return

        old_audio = self._project.audio_files
        for path in old_audio:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    logger.info(f"删除旧配音文件: {path}")
            except Exception as e:
                logger.warning(f"删除旧配音文件失败: {path} -> {e}")
        self._project.audio_files = []
        self._generated_audio_files = []

        voice_settings = self._voice_page.get_settings()

        scripts = self._project.narration_scripts
        if not scripts:
            logger.error("没有解说文案，无法生成配音")
            self._g_nav_ok()
            return

        if self._db_manager is None:
            logger.error("数据库管理器未配置，无法读取豆包语音配置")
            self._g_nav_ok()
            return

        app_settings = self._settings_service.load()
        doubao_settings = app_settings.doubao_voice

        if not doubao_settings.is_configured:
            logger.error("豆包语音配置不完整，请先在系统设置中完成配置")
            self._g_nav_ok()
            return

        project_dir = os.path.join(
            str(self._narrate_project_state_service.projects_dir),
            self._project.id,
            "audio",
        )
        self._audio_output_dir = project_dir
        os.makedirs(project_dir, exist_ok=True)

        self._next_btn.setEnabled(False)
        self._prev_btn.setEnabled(False)
        self._status_label.setText("正在生成配音...")
        self._status_label.show()

        self._tts_worker = TTSTaskWorker(
            scripts=scripts,
            voice_type=voice_settings["voice_type"],
            speed=voice_settings["rate"],
            pitch=voice_settings["pitch"],
            volume=voice_settings["volume"],
            silence_duration=voice_settings["silence"],
            emotion=voice_settings["emotion"],
            language=voice_settings["language"],
            doubao_settings=doubao_settings,
            output_dir=project_dir,
        )
        self._tts_worker.progress.connect(self._on_tts_progress)
        self._tts_worker.tts_finished.connect(self._on_tts_finished)
        self._tts_worker.error.connect(self._on_tts_error)
        self._tts_worker.start()

    def _on_tts_progress(self, message: str):
        self._status_label.setText(message)

    def _on_tts_finished(self):
        self._generated_audio_files = self._tts_worker.get_audio_paths() if self._tts_worker else []
        self._project.audio_files = self._generated_audio_files
        self._save_project()

        self._status_label.setText("配音生成完成！")
        self._g_nav_ok()
        self._advance()
        self._status_label.hide()

    def _on_tts_error(self, message: str):
        self._status_label.setText(f"配音生成失败，可尝试重新生成")
        self._g_nav_ok()
        logger.error(f"TTS生成失败: {message}")

    def _on_ai_ready(self, ready):
        if self._current_step == 1:
            self._next_btn.setEnabled(ready)

    def _on_method_ready(self, ready):
        if self._current_step == 2:
            self._next_btn.setEnabled(ready)

    def _on_finish(self):
        if self._project.audio_files:
            self._save_project()
        self.finished.emit()

    def _on_cancel(self):
        if self._tts_worker and self._tts_worker.isRunning():
            self._tts_worker.quit()
            self._tts_worker.wait()
        self._save_project()
        self.cancelled.emit()

    def _save_project(self):
        self._narrate_project_state_service.save_project(self._project)
