import logging
import os

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

from clip_synth.services.ai_service import AIModelConfig
from clip_synth.services.doubao_tts_service import DoubaoTTSWorker
from clip_synth.services.narrate_export_service import NarrateExportService
from clip_synth.services.settings_service import SettingsService
from clip_synth.services.narrate_project_state_service import NarrateProjectStateService
from clip_synth.ui.pages.narrate_v2.subtitle_recognition_page import SubtitleRecognitionPage
from clip_synth.ui.pages.narrate_v2.character_recognition_page import CharacterRecognitionPage
from clip_synth.ui.pages.narrate_v2.generate_script_page import GenerateScriptPage
from clip_synth.ui.pages.voice_selection_page import VoiceSelectionPage
from clip_synth.ui.pages.narrate_export_page import NarrateExportPage

logger = logging.getLogger("clip_synth.narrate_v2")


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
        self._timestamps = {}

    def run(self):
        try:
            worker = DoubaoTTSWorker(self._doubao_settings)
            os.makedirs(self._output_dir, exist_ok=True)

            for i, script in enumerate(self._scripts):
                text = script.get("text", "")
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
            logger.error("TTS生成异常", exc_info=True)
            self.error.emit(str(e))

    def get_audio_paths(self):
        return self._audio_paths

    def get_timestamps(self):
        return self._timestamps


class NarrateV2Wizard(QFrame):
    finished = Signal()
    cancelled = Signal()

    def __init__(
        self,
        project_id: str,
        project_name: str,
        video_paths: list,
        settings_service: SettingsService,
        narrate_project_state_service: 'NarrateProjectStateService',
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project_id = project_id
        self._video_paths = video_paths
        self._project_name = project_name
        self._settings_service = settings_service
        self._narrate_project_state_service = narrate_project_state_service
        self._current_step = 0
        self._total_steps = 5
        self._recognition_result = None
        self._tts_worker: TTSTaskWorker | None = None
        self._generated_audio_files: list = []
        self.setObjectName("smartNarrateWizard")
        self._setup_ui()
        self._restore_saved_state()
        self._update_step_indicators()
        self._update_nav_buttons()
        self._save_current_state()

    def _restore_saved_state(self):
        project = self._narrate_project_state_service.load_project(self._project_id)
        if not project:
            return

        saved_step = project.extra_data.get("saved_step", -1)
        saved_aliases = project.extra_data.get("speaker_aliases", {})
        rearranged = project.extra_data.get("rearranged_utterances")
        saved_data = project.extra_data.get("recognition_result")
        saved_script = project.extra_data.get("script_content", "")
        saved_segments = project.extra_data.get("script_segments", [])

        logger.info("恢复项目状态: saved_step=%s, saved_segments_count=%s, has_rearranged=%s, has_recognition=%s, has_script=%s",
                     saved_step, len(saved_segments) if saved_segments else 0,
                     "yes" if rearranged else "no",
                     "yes" if saved_data else "no",
                     "yes" if saved_script else "no")

        if rearranged:
            self._recognition_result = saved_data
            self._character_page.load_recognition_data(rearranged)
            for card in self._character_page._speaker_cards:
                alias = saved_aliases.get(card._speaker_id, "")
                if alias:
                    card._alias_input.setText(alias)
        elif saved_data:
            self._recognition_result = saved_data
            self._character_page.load_recognition_data(saved_data)
            for card in self._character_page._speaker_cards:
                alias = saved_aliases.get(card._speaker_id, "")
                if alias:
                    card._alias_input.setText(alias)

        if 0 <= saved_step < self._total_steps:
            if saved_step >= 2:
                self._configure_script_page()
                if saved_segments:
                    self._apply_saved_script(saved_script, saved_segments)
                    self._setup_voice()
            if saved_step >= 3 and saved_segments:
                self._setup_export()
            self._current_step = saved_step
            self._stack.setCurrentIndex(saved_step)
            return

        if saved_script or saved_segments:
            self._configure_script_page()
            self._apply_saved_script(saved_script, saved_segments)
            self._current_step = 2
            self._stack.setCurrentIndex(2)
        elif saved_data:
            self._current_step = 1
            self._stack.setCurrentIndex(1)

    def _apply_saved_script(self, script_content: str, segments: list):
        if script_content:
            self._script_page.set_script_content(script_content)
        if segments:
            self._script_page._segments = segments

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("wizardHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 24, 0, 24)

        self._step_numbers = []
        self._step_texts = []

        steps = [
            "字幕识别",
            "人物识别",
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

            header_layout.addWidget(step_container)

            if i < len(steps) - 1:
                separator = QLabel("")
                separator.setObjectName("stepSeparator")
                separator.setMinimumWidth(40)
                header_layout.addWidget(separator)

        header_layout.addStretch()
        layout.addWidget(header)

        self._stack = QStackedWidget()

        self._subtitle_page = SubtitleRecognitionPage(self._video_paths, self._settings_service, self._project_id)
        self._subtitle_page.recognition_done.connect(self._on_subtitle_done)
        self._subtitle_page.video_order_changed.connect(self._on_video_order_changed)
        self._stack.addWidget(self._subtitle_page)

        self._character_page = CharacterRecognitionPage()
        self._character_page.utterance_moved.connect(self._on_utterance_moved)
        self._stack.addWidget(self._character_page)

        self._script_page = GenerateScriptPage()
        self._script_page.script_edited.connect(self._on_script_edited)
        self._stack.addWidget(self._script_page)

        self._voice_page = VoiceSelectionPage(settings_service=self._settings_service)
        self._stack.addWidget(self._voice_page)

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
        self._prev_btn.setVisible(self._current_step > 0)
        self._prev_btn.setEnabled(self._current_step > 0)

        if self._current_step == self._total_steps - 1:
            self._next_btn.hide()
            self._finish_btn.show()
        else:
            self._next_btn.show()
            self._finish_btn.hide()
            self._next_btn.setEnabled(True)

    def _on_video_order_changed(self, new_order: list):
        self._video_paths = new_order
        project = self._narrate_project_state_service.load_project(self._project_id)
        if project:
            ordered_videos = []
            for vp in new_order:
                existing = next((v for v in project.videos if v.video_path == vp), None)
                if existing:
                    ordered_videos.append(existing)
            if ordered_videos:
                project.videos = ordered_videos
                self._narrate_project_state_service.save_project(project)

    def _on_subtitle_done(self, result: dict):
        if self._current_step == 0:
            self._recognition_result = result
            self._character_page.load_recognition_data(result)
            self._current_step = 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_step_indicators()
            self._update_nav_buttons()
            self._save_current_state()

    def _on_prev(self):
        if self._current_step > 0:
            self._current_step -= 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_step_indicators()
            self._update_nav_buttons()
            self._save_current_state()

    def _on_next(self):
        if self._current_step < self._total_steps - 1:
            if self._current_step == 1:
                self._configure_script_page()
            if self._current_step == 2:
                valid, msg = self._script_page.validate_script()
                if not valid:
                    QMessageBox.warning(self, "解说文案验证失败", msg)
                    return
                self._setup_voice()
            if self._current_step == 3:
                self._on_generate_tts()
                return
            self._advance()

    def _configure_script_page(self):
        settings = self._settings_service.load()
        text_config = settings.text_model
        ai_config = AIModelConfig(
            model_name=text_config.model_name,
            api_key=text_config.api_key,
            base_url=text_config.base_url,
        )
        self._script_page.configure(
            ai_config,
            self._character_page.get_utterances(),
            self._character_page.get_speaker_aliases(),
        )

    def _advance(self):
        if self._current_step < self._total_steps - 1:
            self._current_step += 1
            self._stack.setCurrentIndex(self._current_step)
            self._update_step_indicators()
            self._update_nav_buttons()
            self._save_current_state()

    def _setup_voice(self):
        self._script_page.validate_script()
        segments = self._script_page.get_script_segments()
        scripts = []
        for seg in segments:
            for part in seg.get("parts", []):
                if part.get("type") == "narration":
                    script_text = part.get("script", "").strip()
                    if script_text:
                        scripts.append({"text": script_text})
        self._voice_page.set_scripts(scripts)

    def _setup_export(self):
        project = self._narrate_project_state_service.load_project(self._project_id)
        if not project:
            return

        self._script_page.validate_script()
        segs = self._script_page.get_script_segments()
        logger.info("_setup_export: segs=%d, has_parts=%s, first_part_types=%s",
                     len(segs),
                     "parts" in segs[0] if segs else "N/A",
                     [p.get("type") for p in segs[0].get("parts", [])] if segs and "parts" in segs[0] else "N/A")
        project.narration_scripts = segs
        project.audio_files = self._generated_audio_files or project.audio_files
        project.extra_data["merged_video_path"] = self._character_page._merged_video_path

        self._narrate_project_state_service.save_project(project)

        export_dir = os.path.join(
            str(self._narrate_project_state_service.projects_dir),
            self._project_id,
        )
        export_service = NarrateExportService(export_dir)
        self._export_page.set_project(project, export_service)

    def _on_generate_tts(self):
        voice_settings = self._voice_page.get_settings()

        if voice_settings.get("tts_engine") == "custom":
            custom_items = voice_settings.get("custom_items", [])
            project = self._narrate_project_state_service.load_project(self._project_id)
            if project:
                project.tts_engine = "custom"
                project.custom_audio_files = custom_items
                project.audio_files = []
                self._generated_audio_files = []
                self._narrate_project_state_service.save_project(project)
            self._setup_export()
            self._advance()
            return

        self._script_page.validate_script()
        segments = self._script_page.get_script_segments()
        logger.info("生成配音: get_script_segments 返回 %d 个片段", len(segments) if segments else 0)

        scripts = []
        for seg_idx, seg in enumerate(segments):
            for part_idx, part in enumerate(seg.get("parts", [])):
                if part.get("type") == "narration":
                    text = part.get("script", "").strip()
                    if text:
                        scripts.append({
                            "text": text,
                            "segment_index": seg_idx,
                            "part_index": part_idx,
                        })
        logger.info("从 parts 中提取到 %d 段解说文案", len(scripts))
        if not scripts:
            logger.warning("没有有效的解说文案片段. 当前 _current_step=%d", self._current_step)
            QMessageBox.warning(self, "没有解说文案", "解说文案片段中没有有效的 narration 文本，请返回上一步检查解说文案内容。")
            return

        settings = self._settings_service.load()
        doubao_settings = settings.doubao_voice

        if not doubao_settings.is_configured:
            QMessageBox.warning(self, "提示", "豆包语音配置不完整，请先在系统设置中完成配置")
            return

        project_dir = os.path.join(
            str(self._narrate_project_state_service.projects_dir),
            self._project_id,
            "audio",
        )

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

        self._status_label.setText("配音生成完成！")
        self._next_btn.setEnabled(True)
        self._prev_btn.setEnabled(True)
        self._status_label.hide()

        self._setup_export()
        self._save_current_state()
        self._advance()

    def _on_tts_error(self, error_msg: str):
        self._status_label.setText("配音生成失败")
        self._next_btn.setEnabled(True)
        self._prev_btn.setEnabled(True)
        logger.error("配音生成失败: %s", error_msg)
        self._status_label.hide()

    def _on_finish(self):
        self._save_current_state()
        self.finished.emit()

    def _on_cancel(self):
        if self._tts_worker and self._tts_worker.isRunning():
            self._tts_worker.quit()
            self._tts_worker.wait()
        self._save_current_state()
        self.cancelled.emit()

    def _on_utterance_moved(self):
        self._save_current_state()

    def _on_script_edited(self):
        self._save_current_state()

    def closeEvent(self, event):
        self._save_current_state()
        super().closeEvent(event)

    def _save_current_state(self):
        project = self._narrate_project_state_service.load_project(self._project_id)
        if not project:
            return
        project.current_step = self._current_step
        project.extra_data["saved_step"] = self._current_step
        if self._recognition_result:
            project.extra_data["recognition_result"] = self._recognition_result
        project.extra_data["speaker_aliases"] = self._character_page.get_speaker_aliases()
        project.extra_data["rearranged_utterances"] = {
            "utterances": self._character_page.get_utterances(),
            "merged_video_path": self._character_page._merged_video_path,
        }
        project.extra_data["script_content"] = self._script_page.get_script_content()
        script_segments = self._script_page.get_script_segments()
        if script_segments and any(
            (seg.get("script", "") or "").strip() or "parts" in seg
            for seg in script_segments
        ):
            project.extra_data["script_segments"] = script_segments
        if self._generated_audio_files:
            project.audio_files = self._generated_audio_files
        # 保存分集模式状态
        project.episode_mode = self._script_page._episode_mode
        project.episode_count = len(self._script_page._episodes)
        self._narrate_project_state_service.save_project(project)
