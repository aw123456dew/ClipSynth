import logging
import os
import re

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from clip_synth.models.novel_mix_project_state import NovelMixProjectState
from clip_synth.services.doubao_tts_service import DoubaoTTSWorker
from clip_synth.services.settings_service import SettingsService

logger = logging.getLogger("clip_synth.novel_mix_dub")

DOUBAO_VOICE_OPTIONS = {
    "BV700_V2_streaming": "灿灿 2.0",
    "BV705_streaming": "炀炀",
    "BV701_V2_streaming": "擎苍 2.0",
    "BV001_V2_streaming": "通用女声 2.0",
    "BV700_streaming": "灿灿",
    "BV406_V2_streaming": "超自然音色-梓梓2.0",
    "BV406_streaming": "超自然音色-梓梓",
    "BV407_V2_streaming": "超自然音色-燃燃2.0",
    "BV407_streaming": "超自然音色-燃燃",
    "BV001_streaming": "通用女声",
    "BV002_streaming": "通用男声",
    "BV701_streaming": "擎苍",
    "BV123_streaming": "阳光青年",
    "BV120_streaming": "反卷青年",
    "BV119_streaming": "通用赘婿",
    "BV115_streaming": "古风少御",
    "BV107_streaming": "霸气青叔",
    "BV100_streaming": "质朴青年",
    "BV104_streaming": "温柔淑女",
    "BV004_streaming": "开朗青年",
    "BV113_streaming": "甜宠少御",
    "BV102_streaming": "儒雅青年",
    "BV405_streaming": "甜美小源",
    "BV007_streaming": "亲切女声",
    "BV009_streaming": "知性女声",
    "BV419_streaming": "诚诚",
    "BV415_streaming": "童童",
    "BV008_streaming": "亲切男声",
    "BV408_streaming": "译制片男声",
    "BV426_streaming": "懒小羊",
    "BV428_streaming": "清新文艺女声",
    "BV403_streaming": "鸡汤女声",
    "BV158_streaming": "智慧老者",
    "BV157_streaming": "慈爱姥姥",
    "BR001_streaming": "说唱小哥",
    "BV410_streaming": "活力解说男",
    "BV411_streaming": "影视解说小帅",
    "BV437_streaming": "解说小帅-多情感",
    "BV412_streaming": "影视解说小美",
    "BV159_streaming": "纨绔青年",
    "BV418_streaming": "直播一姐",
    "BV142_streaming": "沉稳解说男",
    "BV143_streaming": "潇洒青年",
    "BV056_streaming": "阳光男声",
    "BV005_streaming": "活泼女声",
    "BV064_streaming": "小萝莉",
    "BV051_streaming": "奶气萌娃",
    "BV063_streaming": "动漫海绵",
    "BV417_streaming": "动漫海星",
    "BV050_streaming": "动漫小新",
    "BV061_streaming": "天才童声",
    "BV401_streaming": "促销男声",
    "BV402_streaming": "促销女声",
    "BV006_streaming": "磁性男声",
    "BV011_streaming": "新闻女声",
    "BV012_streaming": "新闻男声",
    "BV034_streaming": "知性姐姐-双语",
    "BV033_streaming": "温柔小哥",
    "BV511_streaming": "慵懒女声-Ava",
    "BV505_streaming": "议论女声-Alicia",
    "BV138_streaming": "情感女声-Lawrence",
    "BV027_streaming": "美式女声-Amelia",
    "BV502_streaming": "讲述女声-Amanda",
    "BV503_streaming": "活力女声-Ariana",
    "BV504_streaming": "活力男声-Jackson",
    "BV421_streaming": "天才少女",
    "BV702_streaming": "Stefan",
    "BV506_streaming": "天真萌娃-Lily",
    "BV040_streaming": "亲切女声-Anna",
    "BV516_streaming": "澳洲男声-Henry",
    "BV520_streaming": "元气少女",
    "BV521_streaming": "萌系少女",
    "BV522_streaming": "气质女声",
    "BV524_streaming": "日语男声",
    "BV531_streaming": "活力男声Carlos（巴西地区）",
    "BV530_streaming": "活力女声（巴西地区）",
    "BV065_streaming": "气质御姐（墨西哥地区）",
    "BV021_streaming": "东北老铁",
    "BV020_streaming": "东北丫头",
    "BV704_streaming": "方言灿灿",
    "BV210_streaming": "西安佟掌柜",
    "BV217_streaming": "沪上阿姐",
    "BV213_streaming": "广西表哥",
    "BV025_streaming": "甜美台妹",
    "BV227_streaming": "台普男声",
    "BV026_streaming": "港剧男神",
    "BV424_streaming": "广东女仔",
    "BV212_streaming": "相声演员",
    "BV019_streaming": "重庆小伙",
    "BV221_streaming": "四川甜妹儿",
    "BV423_streaming": "重庆幺妹儿",
    "BV214_streaming": "乡村企业家",
    "BV226_streaming": "湖南妹坨",
    "BV216_streaming": "长沙靓女",
}

DOUBAO_EMOTION_OPTIONS = {
    "": "默认",
    "customer_service": "客服",
    "professional": "专业",
    "serious": "严肃",
    "narrator": "旁白-舒缓",
    "narrator_immersive": "旁白-沉浸",
    "comfort": "安慰鼓励",
    "lovey-dovey": "撒娇",
    "energetic": "可爱元气",
    "conniving": "绿茶",
    "tsundere": "傲娇",
    "charming": "娇媚",
    "storytelling": "讲故事",
    "radio": "情感电台",
    "yoga": "瑜伽",
    "advertising": "广告",
    "assistant": "助手",
    "chat": "自然对话",
    "pleased": "愉悦",
    "sorry": "抱歉",
    "annoyed": "嗔怪",
    "happy": "开心",
    "sad": "悲伤",
    "angry": "愤怒",
    "scare": "害怕",
    "hate": "厌恶",
    "surprise": "惊讶",
    "tear": "哭腔",
    "novel_dialog": "平和",
}

DOUBAO_LANGUAGE_OPTIONS = {
    "cn": "中文",
    "en": "英语",
    "ja": "日语",
    "thth": "泰语",
    "vivn": "越南语",
    "id": "印尼语",
    "ptbr": "葡萄牙语",
    "esmx": "西班牙语",
}


class TTSWorker(QThread):
    progress = Signal(str)
    tts_finished = Signal()
    error = Signal(str)

    def __init__(
        self,
        text: str,
        voice_type: str,
        speed: float,
        pitch: float,
        volume: float,
        emotion: str,
        language: str,
        doubao_settings,
        output_dir: str,
        parent=None,
    ):
        super().__init__(parent)
        self._text = text
        self._voice_type = voice_type
        self._speed = speed
        self._pitch = pitch
        self._volume = volume
        self._emotion = emotion
        self._language = language
        self._doubao_settings = doubao_settings
        self._output_dir = output_dir
        self._audio_files = []

    def run(self):
        try:
            from clip_synth.services.doubao_tts_service import split_text_by_length

            worker = DoubaoTTSWorker(self._doubao_settings)
            os.makedirs(self._output_dir, exist_ok=True)

            chunks = split_text_by_length(self._text, 1000)
            total = len(chunks)

            for i, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue
                self.progress.emit(f"正在生成配音 ({i+1}/{total})...")
                audio_path = os.path.join(self._output_dir, f"novel_dub_{i:04d}.mp3")

                success, msg, timestamps = worker.tts_single_with_timestamps(
                    text=chunk,
                    voice_type=self._voice_type,
                    output_path=audio_path,
                    speed=self._speed,
                    pitch=self._pitch,
                    volume=self._volume,
                    emotion=self._emotion,
                    language=self._language,
                )

                if not success:
                    self.error.emit(f"第 {i+1} 段配音生成失败: {msg}")
                    return

                from clip_synth.services.narrate_export_service import _get_media_duration
                duration = _get_media_duration(audio_path)

                self._audio_files.append({
                    "index": i,
                    "path": audio_path,
                    "text": chunk,
                    "duration": duration,
                    "timestamps": timestamps,
                })

            self.progress.emit("配音生成完成")
            self.tts_finished.emit()
        except Exception as e:
            logger.error("TTS生成异常: %s", e, exc_info=True)
            self.error.emit(str(e))

    def get_audio_files(self):
        return self._audio_files


class NovelMixDubPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings_service: SettingsService | None = None
        self._tts_worker: TTSWorker | None = None
        self._generated_audio_files: list | None = None
        self._project_id = ""
        self.setObjectName("novelMixDubPage")
        self._setup_ui()

    def set_settings_service(self, settings_service: SettingsService) -> None:
        self._settings_service = settings_service

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("mixScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer_layout.addWidget(scroll)

        content = QWidget()
        content.setObjectName("mixScrollContent")
        scroll.setWidget(content)

        outer = QVBoxLayout(content)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(10)

        title = QLabel("输入文案 & 配音设置")
        title.setObjectName("wizardStepTitle")
        outer.addWidget(title)

        main_row = QHBoxLayout()
        main_row.setSpacing(20)

        left_panel = QVBoxLayout()
        left_panel.setSpacing(8)

        text_header = QHBoxLayout()
        text_header.setSpacing(12)
        text_label = QLabel("文案内容")
        text_label.setObjectName("sectionTitle")
        text_header.addWidget(text_label)
        text_header.addStretch()
        self._import_btn = QPushButton("导入 TXT 文件")
        self._import_btn.setObjectName("importTxtBtn")
        self._import_btn.setCursor(Qt.PointingHandCursor)
        self._import_btn.clicked.connect(self._on_import_txt)
        text_header.addWidget(self._import_btn)
        left_panel.addLayout(text_header)

        self._text_edit = QTextEdit()
        self._text_edit.setObjectName("novelMixTextEdit")
        self._text_edit.setPlaceholderText("在这里输入或粘贴小说/故事文本...")
        left_panel.addWidget(self._text_edit, stretch=1)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)

        self._char_count_label = QLabel("已输入 0 字")
        self._char_count_label.setObjectName("charCountLabel")
        bottom_row.addWidget(self._char_count_label)

        bottom_row.addStretch()

        self._format_btn = QPushButton("格式化文本")
        self._format_btn.setObjectName("formatTextBtn")
        self._format_btn.setCursor(Qt.PointingHandCursor)
        self._format_btn.clicked.connect(self._on_format_text)
        bottom_row.addWidget(self._format_btn)

        left_panel.addLayout(bottom_row)

        self._text_edit.textChanged.connect(self._on_text_changed)

        main_row.addLayout(left_panel, stretch=1)

        right_panel = QGroupBox("配音参数")
        right_panel.setObjectName("ttsEngineGroup")
        right_panel.setFixedWidth(340)
        engine_layout = QVBoxLayout(right_panel)
        engine_layout.setContentsMargins(16, 4, 16, 10)
        engine_layout.setSpacing(6)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(10)

        voice_layout = QVBoxLayout()
        voice_label = QLabel("音色选择")
        voice_label.setObjectName("paramLabel")
        voice_layout.addWidget(voice_label)
        self._voice_combo = QComboBox()
        self._voice_combo.setObjectName("voiceCombo")
        self._voice_combo.setMinimumHeight(32)
        for voice_id, voice_name in DOUBAO_VOICE_OPTIONS.items():
            self._voice_combo.addItem(voice_name, voice_id)
        self._voice_combo.setCurrentIndex(0)
        voice_layout.addWidget(self._voice_combo)
        combo_row.addLayout(voice_layout, stretch=1)

        emotion_layout = QVBoxLayout()
        emotion_label = QLabel("风格/情感")
        emotion_label.setObjectName("paramLabel")
        emotion_layout.addWidget(emotion_label)
        self._emotion_combo = QComboBox()
        self._emotion_combo.setObjectName("styleCombo")
        self._emotion_combo.setMinimumHeight(32)
        for emotion_id, emotion_name in DOUBAO_EMOTION_OPTIONS.items():
            self._emotion_combo.addItem(emotion_name, emotion_id)
        self._emotion_combo.setCurrentIndex(0)
        emotion_layout.addWidget(self._emotion_combo)
        combo_row.addLayout(emotion_layout, stretch=1)

        engine_layout.addLayout(combo_row)

        lang_layout = QVBoxLayout()
        lang_label = QLabel("语种")
        lang_label.setObjectName("paramLabel")
        lang_layout.addWidget(lang_label)
        self._lang_combo = QComboBox()
        self._lang_combo.setObjectName("langCombo")
        self._lang_combo.setMinimumHeight(32)
        for lang_id, lang_name in DOUBAO_LANGUAGE_OPTIONS.items():
            self._lang_combo.addItem(lang_name, lang_id)
        self._lang_combo.setCurrentIndex(0)
        lang_layout.addWidget(self._lang_combo)
        engine_layout.addLayout(lang_layout)

        slider_row = QVBoxLayout()
        slider_row.setSpacing(6)

        self._speed_slider = self._create_slider_row("语速", 0.2, 3.0, 1.0, "rateSlider", slider_row)
        self._pitch_slider = self._create_slider_row("音调", 0.2, 3.0, 1.0, "pitchSlider", slider_row)
        self._volume_slider = self._create_slider_row("音量", 0.2, 3.0, 1.0, "volumeSlider", slider_row)

        engine_layout.addLayout(slider_row)

        engine_layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.setContentsMargins(0, 8, 0, 0)
        btn_row.addStretch()
        self._generate_btn = QPushButton("生成全部配音")
        self._generate_btn.setObjectName("generateAllBtn")
        self._generate_btn.setCursor(Qt.PointingHandCursor)
        self._generate_btn.clicked.connect(self._on_generate_all)
        btn_row.addWidget(self._generate_btn)
        engine_layout.addLayout(btn_row)

        self._progress_label = QLabel("")
        self._progress_label.setObjectName("ttsProgressLabel")
        self._progress_label.setAlignment(Qt.AlignCenter)
        self._progress_label.hide()
        engine_layout.addWidget(self._progress_label)

        main_row.addWidget(right_panel)

        outer.addLayout(main_row, stretch=1)

    def _create_slider_row(self, label_text: str, min_val: float, max_val: float, default: float,
                           slider_name: str, parent_layout) -> QSlider:
        row = QHBoxLayout()
        row.setSpacing(8)

        label = QLabel(label_text)
        label.setObjectName("paramLabel")
        label.setMinimumWidth(40)
        row.addWidget(label)

        slider = QSlider(Qt.Horizontal)
        slider.setObjectName(slider_name)
        slider.setRange(int(min_val * 10), int(max_val * 10))
        slider.setValue(int(default * 10))
        row.addWidget(slider, stretch=1)

        value_label = QLabel(f"{default:.1f}x")
        value_label.setObjectName("sliderValueLabel")
        value_label.setMinimumWidth(40)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(value_label)

        slider.valueChanged.connect(
            lambda v, lbl=value_label: lbl.setText(f"{v / 10.0:.1f}x")
        )

        parent_layout.addLayout(row)
        return slider

    def _on_text_changed(self) -> None:
        text = self._text_edit.toPlainText()
        char_count = len(text.replace("\n", "").replace("\r", "").replace(" ", ""))
        self._char_count_label.setText(f"已输入 {char_count} 字")

    def _on_import_txt(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "导入文本文件", "", "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self._text_edit.setPlainText(content)
            except Exception as e:
                logger.error("读取文本文件失败: %s", e)

    def _on_format_text(self) -> None:
        text = self._text_edit.toPlainText()
        if not text.strip():
            return

        text = re.sub(r'[\[\]「」""]', '', text)
        text = re.sub(r'[！？。]', ',', text)
        text = re.sub(r',+', ',', text)
        text = re.sub(r'，+', ',', text)

        self._text_edit.setPlainText(text)

    def _on_generate_all(self) -> None:
        if self._tts_worker is not None and self._tts_worker.isRunning():
            return

        if not self._settings_service:
            logger.error("SettingsService 未设置")
            return

        settings = self._settings_service.load()
        doubao_settings = settings.doubao_voice
        if not doubao_settings.is_configured:
            logger.warning("豆包语音配置未完成")
            return

        text = self._text_edit.toPlainText().strip()
        if not text:
            logger.warning("没有文本内容可生成配音")
            return

        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "cache", "novel_mix_dub")
        output_dir = os.path.abspath(output_dir)

        voice_type = self._voice_combo.currentData()
        emotion = self._emotion_combo.currentData()
        language = self._lang_combo.currentData()
        speed = self._speed_slider.value() / 10.0
        pitch = self._pitch_slider.value() / 10.0
        volume = self._volume_slider.value() / 10.0

        self._progress_label.setText("准备生成配音...")
        self._progress_label.show()
        self._generate_btn.setEnabled(False)

        self._tts_worker = TTSWorker(
            text=text,
            voice_type=voice_type,
            speed=speed,
            pitch=pitch,
            volume=volume,
            emotion=emotion,
            language=language,
            doubao_settings=doubao_settings,
            output_dir=output_dir,
        )
        self._tts_worker.progress.connect(self._on_tts_progress)
        self._tts_worker.tts_finished.connect(self._on_tts_finished)
        self._tts_worker.error.connect(self._on_tts_error)
        self._tts_worker.start()
        self._generated_audio_files = None

    def _on_tts_progress(self, msg: str) -> None:
        self._progress_label.setText(msg)

    def _on_tts_finished(self) -> None:
        self._progress_label.setText("配音生成完成")
        self._generate_btn.setEnabled(True)
        if self._tts_worker:
            self._generated_audio_files = self._tts_worker.get_audio_files()

    def _on_tts_error(self, msg: str) -> None:
        self._progress_label.setText(f"错误: {msg}")
        self._generate_btn.setEnabled(True)

    def save(self, project: NovelMixProjectState) -> None:
        project.narration_text = self._text_edit.toPlainText()
        project.voice_type = self._voice_combo.currentData() or "BV700_V2_streaming"
        project.emotion = self._emotion_combo.currentData() or ""
        project.language = self._lang_combo.currentData() or "cn"
        project.speed = self._speed_slider.value() / 10.0
        project.pitch = self._pitch_slider.value() / 10.0
        project.volume = self._volume_slider.value() / 10.0
        if self._generated_audio_files is not None:
            project.audio_files = self._generated_audio_files

    def restore(self, project: NovelMixProjectState) -> None:
        self._text_edit.setPlainText(project.narration_text)
        voice_idx = self._voice_combo.findData(project.voice_type)
        if voice_idx >= 0:
            self._voice_combo.setCurrentIndex(voice_idx)
        emotion_idx = self._emotion_combo.findData(project.emotion)
        if emotion_idx >= 0:
            self._emotion_combo.setCurrentIndex(emotion_idx)
        lang_idx = self._lang_combo.findData(project.language)
        if lang_idx >= 0:
            self._lang_combo.setCurrentIndex(lang_idx)
        self._speed_slider.setValue(int(project.speed * 10))
        self._pitch_slider.setValue(int(project.pitch * 10))
        self._volume_slider.setValue(int(project.volume * 10))
