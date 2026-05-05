import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("clip_synth.voice_selection")

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


class VoiceSelectionPage(QFrame):
    ready_for_next = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("voiceSelectionPage")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(24)

        title = QLabel("选择配音")
        title.setObjectName("wizardStepTitle")
        layout.addWidget(title)

        tts_group = QGroupBox("TTS引擎")
        tts_group.setObjectName("ttsEngineGroup")
        tts_layout = QVBoxLayout(tts_group)
        tts_layout.setContentsMargins(16, 16, 16, 16)

        self._tts_combo = QComboBox()
        self._tts_combo.addItem("豆包语音", "doubao")
        self._tts_combo.setObjectName("ttsEngineCombo")
        tts_layout.addWidget(self._tts_combo)
        layout.addWidget(tts_group)

        params_group = QGroupBox("配音参数")
        params_group.setObjectName("voiceParamsGroup")
        params_layout = QVBoxLayout(params_group)
        params_layout.setContentsMargins(24, 24, 24, 24)
        params_layout.setSpacing(20)

        combo_row = QHBoxLayout()
        combo_row.setSpacing(16)

        voice_layout = QVBoxLayout()
        voice_label = QLabel("音色选择")
        voice_label.setObjectName("paramLabel")
        voice_layout.addWidget(voice_label)
        self._voice_combo = QComboBox()
        self._voice_combo.setObjectName("voiceCombo")
        self._voice_combo.setMinimumHeight(36)
        for voice_id, voice_name in DOUBAO_VOICE_OPTIONS.items():
            self._voice_combo.addItem(voice_name, voice_id)
        self._voice_combo.setCurrentIndex(0)
        voice_layout.addWidget(self._voice_combo)
        combo_row.addLayout(voice_layout, stretch=1)

        style_layout = QVBoxLayout()
        style_label = QLabel("风格/情感")
        style_label.setObjectName("paramLabel")
        style_layout.addWidget(style_label)
        self._style_combo = QComboBox()
        self._style_combo.setObjectName("styleCombo")
        self._style_combo.setMinimumHeight(36)
        for emotion_id, emotion_name in DOUBAO_EMOTION_OPTIONS.items():
            self._style_combo.addItem(emotion_name, emotion_id)
        self._style_combo.setCurrentIndex(0)
        style_layout.addWidget(self._style_combo)
        combo_row.addLayout(style_layout, stretch=1)

        lang_layout = QVBoxLayout()
        lang_label = QLabel("语种")
        lang_label.setObjectName("paramLabel")
        lang_layout.addWidget(lang_label)
        self._lang_combo = QComboBox()
        self._lang_combo.setObjectName("langCombo")
        self._lang_combo.setMinimumHeight(36)
        for lang_id, lang_name in DOUBAO_LANGUAGE_OPTIONS.items():
            self._lang_combo.addItem(lang_name, lang_id)
        self._lang_combo.setCurrentIndex(0)
        lang_layout.addWidget(self._lang_combo)
        combo_row.addLayout(lang_layout, stretch=1)

        params_layout.addLayout(combo_row)

        separator = QFrame()
        separator.setObjectName("paramSeparator")
        params_layout.addWidget(separator)

        sliders_layout = QFormLayout()
        sliders_layout.setContentsMargins(0, 0, 0, 0)
        sliders_layout.setSpacing(24)

        rate_layout = QHBoxLayout()
        rate_layout.setContentsMargins(0, 0, 0, 0)
        rate_layout.setSpacing(12)
        self._rate_slider = QSlider(Qt.Horizontal)
        self._rate_slider.setRange(2, 30)
        self._rate_slider.setValue(10)
        self._rate_slider.setObjectName("rateSlider")
        rate_layout.addWidget(self._rate_slider, stretch=1)
        self._rate_label = QLabel("1.0")
        self._rate_label.setObjectName("rateLabel")
        self._rate_label.setFixedSize(56, 32)
        self._rate_label.setAlignment(Qt.AlignCenter)
        rate_layout.addWidget(self._rate_label)
        self._rate_slider.valueChanged.connect(self._on_rate_changed)
        sliders_layout.addRow("语速:", rate_layout)

        pitch_layout = QHBoxLayout()
        pitch_layout.setContentsMargins(0, 0, 0, 0)
        pitch_layout.setSpacing(12)
        self._pitch_slider = QSlider(Qt.Horizontal)
        self._pitch_slider.setRange(5, 15)
        self._pitch_slider.setValue(10)
        self._pitch_slider.setObjectName("pitchSlider")
        pitch_layout.addWidget(self._pitch_slider, stretch=1)
        self._pitch_label = QLabel("1.0")
        self._pitch_label.setObjectName("pitchLabel")
        self._pitch_label.setFixedSize(56, 32)
        self._pitch_label.setAlignment(Qt.AlignCenter)
        pitch_layout.addWidget(self._pitch_label)
        self._pitch_slider.valueChanged.connect(self._on_pitch_changed)
        sliders_layout.addRow("音高:", pitch_layout)

        volume_layout = QHBoxLayout()
        volume_layout.setContentsMargins(0, 0, 0, 0)
        volume_layout.setSpacing(12)
        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setRange(1, 20)
        self._volume_slider.setValue(10)
        self._volume_slider.setObjectName("volumeSlider")
        volume_layout.addWidget(self._volume_slider, stretch=1)
        self._volume_label = QLabel("1.0")
        self._volume_label.setObjectName("volumeLabel")
        self._volume_label.setFixedSize(56, 32)
        self._volume_label.setAlignment(Qt.AlignCenter)
        volume_layout.addWidget(self._volume_label)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        sliders_layout.addRow("音量:", volume_layout)

        silence_layout = QHBoxLayout()
        silence_layout.setContentsMargins(0, 0, 0, 0)
        silence_layout.setSpacing(12)
        self._silence_slider = QSlider(Qt.Horizontal)
        self._silence_slider.setRange(0, 20)
        self._silence_slider.setValue(1)
        self._silence_slider.setObjectName("silenceSlider")
        silence_layout.addWidget(self._silence_slider, stretch=1)
        self._silence_label = QLabel("0.1")
        self._silence_label.setObjectName("silenceLabel")
        self._silence_label.setFixedSize(56, 32)
        self._silence_label.setAlignment(Qt.AlignCenter)
        silence_layout.addWidget(self._silence_label)
        self._silence_slider.valueChanged.connect(self._on_silence_changed)
        sliders_layout.addRow("句尾静音时长(秒):", silence_layout)

        params_layout.addLayout(sliders_layout)

        layout.addWidget(params_group)
        layout.addStretch()

    def _on_rate_changed(self, value: int) -> None:
        self._rate_label.setText(f"{value / 10:.1f}")

    def _on_pitch_changed(self, value: int) -> None:
        self._pitch_label.setText(f"{value / 10:.1f}")

    def _on_volume_changed(self, value: int) -> None:
        self._volume_label.setText(f"{value / 10:.1f}")

    def _on_silence_changed(self, value: int) -> None:
        self._silence_label.setText(f"{value / 10:.1f}")

    def get_settings(self) -> dict:
        return {
            "tts_engine": self._tts_combo.currentData(),
            "voice_type": self._voice_combo.currentData(),
            "voice_name": self._voice_combo.currentText(),
            "emotion": self._style_combo.currentData(),
            "emotion_name": self._style_combo.currentText(),
            "language": self._lang_combo.currentData(),
            "language_name": self._lang_combo.currentText(),
            "rate": self._rate_slider.value() / 10,
            "pitch": self._pitch_slider.value() / 10,
            "volume": self._volume_slider.value() / 10,
            "silence": self._silence_slider.value() / 10,
        }