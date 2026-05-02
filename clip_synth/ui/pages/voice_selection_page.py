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
        self._voice_combo.addItems([
            "温暖女声",
            "活力女声",
            "知性女声",
            "沉稳男声",
            "阳光男声",
            "可爱童声",
        ])
        self._voice_combo.setObjectName("voiceCombo")
        self._voice_combo.setMinimumHeight(36)
        voice_layout.addWidget(self._voice_combo)
        combo_row.addLayout(voice_layout, stretch=1)

        style_layout = QVBoxLayout()
        style_label = QLabel("风格/情感")
        style_label.setObjectName("paramLabel")
        style_layout.addWidget(style_label)
        self._style_combo = QComboBox()
        self._style_combo.addItems([
            "默认",
            "亲切",
            "严肃",
            "欢快",
            "悲伤",
            "激动",
        ])
        self._style_combo.setObjectName("styleCombo")
        self._style_combo.setMinimumHeight(36)
        style_layout.addWidget(self._style_combo)
        combo_row.addLayout(style_layout, stretch=1)

        lang_layout = QVBoxLayout()
        lang_label = QLabel("语种")
        lang_label.setObjectName("paramLabel")
        lang_layout.addWidget(lang_label)
        self._lang_combo = QComboBox()
        self._lang_combo.addItems([
            "中文",
            "英文",
            "日文",
            "韩文",
            "泰文",
        ])
        self._lang_combo.setObjectName("langCombo")
        self._lang_combo.setMinimumHeight(36)
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
            "voice": self._voice_combo.currentText(),
            "style": self._style_combo.currentText(),
            "language": self._lang_combo.currentText(),
            "rate": self._rate_slider.value() / 10,
            "pitch": self._pitch_slider.value() / 10,
            "volume": self._volume_slider.value() / 10,
            "silence": self._silence_slider.value() / 10,
        }