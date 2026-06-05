import logging
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from clip_synth.models.novel_mix_project_state import NovelMixProjectState

logger = logging.getLogger("clip_synth.novel_mix_material")

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"}


def _count_videos(folder: str) -> int:
    count = 0
    try:
        for root, dirs, files in os.walk(folder):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in VIDEO_EXTENSIONS:
                    count += 1
    except Exception as e:
        logger.error("扫描文件夹视频数量失败 %s: %s", folder, e)
    return count


class _ParamGroup(QFrame):
    def __init__(self, title: str, label_min: str, label_max: str,
                 min_val: float = 0.0, max_val: float = 10.0,
                 default_min: float = 1.0, default_max: float = 3.0,
                 decimals: int = 1, single_step: float = 0.1, parent=None):
        super().__init__(parent)
        self.setObjectName("mixParamGroup")
        self.setMinimumHeight(80)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("mixParamTitle")
        layout.addWidget(title_label)

        row = QHBoxLayout()
        row.setSpacing(16)

        min_label = QLabel(label_min)
        min_label.setObjectName("mixParamLabel")
        min_label.setFixedWidth(70)
        row.addWidget(min_label)

        self._min_spin = QDoubleSpinBox()
        self._min_spin.setObjectName("mixParamSpin")
        self._min_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self._min_spin.setRange(min_val, max_val)
        self._min_spin.setValue(default_min)
        self._min_spin.setDecimals(decimals)
        self._min_spin.setSingleStep(single_step)
        self._min_spin.setFixedWidth(120)
        self._min_spin.setFixedHeight(32)
        row.addWidget(self._min_spin)

        sep = QLabel("~")
        sep.setObjectName("mixParamSep")
        row.addWidget(sep)

        max_label = QLabel(label_max)
        max_label.setObjectName("mixParamLabel")
        max_label.setFixedWidth(70)
        row.addWidget(max_label)

        self._max_spin = QDoubleSpinBox()
        self._max_spin.setObjectName("mixParamSpin")
        self._max_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self._max_spin.setRange(min_val, max_val)
        self._max_spin.setValue(default_max)
        self._max_spin.setDecimals(decimals)
        self._max_spin.setSingleStep(single_step)
        self._max_spin.setFixedWidth(120)
        self._max_spin.setFixedHeight(32)
        row.addWidget(self._max_spin)

        row.addStretch()
        layout.addLayout(row)

    @property
    def min_value(self) -> float:
        return self._min_spin.value()

    @min_value.setter
    def min_value(self, v: float) -> None:
        self._min_spin.setValue(v)

    @property
    def max_value(self) -> float:
        return self._max_spin.value()

    @max_value.setter
    def max_value(self, v: float) -> None:
        self._max_spin.setValue(v)

    def set_values(self, min_v: float, max_v: float) -> None:
        self._min_spin.setValue(min_v)
        self._max_spin.setValue(max_v)


class _MixParamsPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(24)

        self._speed_group = _ParamGroup(
            title="片段随机加速",
            label_min="最慢",
            label_max="最快",
            min_val=0.5, max_val=5.0,
            default_min=1.0, default_max=2.0,
            decimals=1, single_step=0.1,
        )
        layout.addWidget(self._speed_group)

        self._zoom_group = _ParamGroup(
            title="素材画面随机放大",
            label_min="最小倍率",
            label_max="最大倍率",
            min_val=1.0, max_val=3.0,
            default_min=1.0, default_max=1.5,
            decimals=2, single_step=0.05,
        )
        layout.addWidget(self._zoom_group)

        # 随机抽帧（范围）
        self._drop_frame_group = _ParamGroup(
            title="素材随机抽帧",
            label_min="最少",
            label_max="最多",
            min_val=0, max_val=1000,
            default_min=0, default_max=0,
            decimals=0, single_step=1,
        )
        self._drop_frame_group.setToolTip("最少/最多抽帧数，均为0则不抽帧")

        layout.addWidget(self._drop_frame_group)

        layout.addStretch()

    def get_params(self) -> dict:
        return {
            "speed_min": self._speed_group.min_value,
            "speed_max": self._speed_group.max_value,
            "zoom_min": self._zoom_group.min_value,
            "zoom_max": self._zoom_group.max_value,
            "drop_frames_min": self._drop_frame_group.min_value,
            "drop_frames_max": self._drop_frame_group.max_value,
        }

    def set_params(self, params: dict) -> None:
        self._speed_group.set_values(
            params.get("speed_min", 1.0),
            params.get("speed_max", 2.0),
        )
        self._zoom_group.set_values(
            params.get("zoom_min", 1.0),
            params.get("zoom_max", 1.5),
        )
        self._drop_frame_group.set_values(
            params.get("drop_frames_min", 0),
            params.get("drop_frames_max", 0),
        )


class NovelMixMaterialPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("novelMixMaterialPage")
        self._setup_ui()

    def _show_warning(self, message: str) -> None:
        dialog = QDialog(self.window())
        dialog.setWindowTitle("提示")
        dialog.setFixedSize(420, 160)
        dialog.setObjectName("confirmDialog")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        msg_label = QLabel(message)
        msg_label.setObjectName("dialogTitle")
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("dialogConfirmBtn")
        ok_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)
        dialog.exec()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title_bar = QFrame()
        title_bar_layout = QVBoxLayout(title_bar)
        title_bar_layout.setContentsMargins(32, 24, 32, 8)
        title_bar_layout.setSpacing(8)

        title = QLabel("选择素材文件夹")
        title.setObjectName("wizardStepTitle")
        title_bar_layout.addWidget(title)

        desc = QLabel("分别选择视频开头素材和混剪素材所在的文件夹，并调整混剪参数")
        desc.setObjectName("exportInfo")
        desc.setWordWrap(True)
        title_bar_layout.addWidget(desc)

        layout.addWidget(title_bar)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("mixMaterialScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_content.setObjectName("mixMaterialScrollContent")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(32, 12, 32, 24)
        scroll_layout.setSpacing(16)

        # --- Opening folder row ---
        opening_row = QHBoxLayout()
        opening_row.setSpacing(12)

        opening_label = QLabel("选择视频开头素材：")
        opening_label.setObjectName("mixFolderLabel")
        opening_row.addWidget(opening_label)

        self._opening_path_label = QLabel("未选择")
        self._opening_path_label.setObjectName("mixFolderPath")
        self._opening_path_label.setWordWrap(True)
        opening_row.addWidget(self._opening_path_label, stretch=1)

        self._opening_btn = QPushButton("选择文件夹")
        self._opening_btn.setObjectName("mixFolderBtn")
        self._opening_btn.setCursor(Qt.PointingHandCursor)
        self._opening_btn.clicked.connect(lambda: self._on_select_folder("opening"))
        opening_row.addWidget(self._opening_btn)

        scroll_layout.addLayout(opening_row)

        # --- Mix folder row ---
        mix_row = QHBoxLayout()
        mix_row.setSpacing(12)

        mix_label = QLabel("选择混剪素材：    ")
        mix_label.setObjectName("mixFolderLabel")
        mix_row.addWidget(mix_label)

        self._mix_path_label = QLabel("未选择")
        self._mix_path_label.setObjectName("mixFolderPath")
        self._mix_path_label.setWordWrap(True)
        mix_row.addWidget(self._mix_path_label, stretch=1)

        self._mix_btn = QPushButton("选择文件夹")
        self._mix_btn.setObjectName("mixFolderBtn")
        self._mix_btn.setCursor(Qt.PointingHandCursor)
        self._mix_btn.clicked.connect(lambda: self._on_select_folder("mix"))
        mix_row.addWidget(self._mix_btn)

        scroll_layout.addLayout(mix_row)

        # --- Cover folder row ---
        cover_row = QHBoxLayout()
        cover_row.setSpacing(12)

        cover_label = QLabel("视频封面目录（可选）：")
        cover_label.setObjectName("mixFolderLabel")
        cover_row.addWidget(cover_label)

        self._cover_path_label = QLabel("未选择（使用视频第一帧）")
        self._cover_path_label.setObjectName("mixFolderPath")
        self._cover_path_label.setWordWrap(True)
        cover_row.addWidget(self._cover_path_label, stretch=1)

        self._cover_btn = QPushButton("选择文件夹")
        self._cover_btn.setObjectName("mixFolderBtn")
        self._cover_btn.setCursor(Qt.PointingHandCursor)
        self._cover_btn.clicked.connect(lambda: self._on_select_folder("cover"))
        cover_row.addWidget(self._cover_btn)

        scroll_layout.addLayout(cover_row)

        # --- Separator ---
        sep = QFrame()
        sep.setObjectName("mixParamSeparator")
        sep.setFixedHeight(1)
        scroll_layout.addWidget(sep)

        # --- Tab widget with parameter pages ---
        tab_label = QLabel("混剪参数设置")
        tab_label.setObjectName("mixParamSectionTitle")
        scroll_layout.addWidget(tab_label)

        self._tab_widget = QTabWidget()
        self._tab_widget.setObjectName("mixParamTab")

        self._opening_params_page = _MixParamsPage()
        self._mix_params_page = _MixParamsPage()

        self._tab_widget.addTab(self._opening_params_page, "开头素材参数")
        self._tab_widget.addTab(self._mix_params_page, "混剪素材参数")

        scroll_layout.addWidget(self._tab_widget, stretch=1)

        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area, stretch=1)

        self._opening_folder = ""
        self._mix_folder = ""
        self._cover_folder = ""

    def _on_select_folder(self, target: str) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not folder:
            return
        if target == "opening":
            self._opening_folder = folder
            self._opening_path_label.setText(folder)
        elif target == "mix":
            self._mix_folder = folder
            self._mix_path_label.setText(folder)
        elif target == "cover":
            self._cover_folder = folder
            self._cover_path_label.setText(folder)

    def validate(self) -> bool:
        if not self._opening_folder:
            self._show_warning("请先选择视频开头素材文件夹")
            return False
        if not self._mix_folder:
            self._show_warning("请先选择混剪素材文件夹")
            return False

        opening_videos = _count_videos(self._opening_folder)
        if opening_videos == 0:
            self._show_warning(
                f"视频开头素材文件夹中没有找到视频文件：\n{self._opening_folder}"
            )
            return False

        mix_videos = _count_videos(self._mix_folder)
        if mix_videos == 0:
            self._show_warning(
                f"混剪素材文件夹中没有找到视频文件：\n{self._mix_folder}"
            )
            return False

        return True

    def save(self, project: NovelMixProjectState) -> None:
        project.opening_folder = self._opening_folder
        project.mix_folder = self._mix_folder
        project.extra_data["opening_params"] = self._opening_params_page.get_params()
        project.extra_data["mix_params"] = self._mix_params_page.get_params()
        project.extra_data["cover_dir"] = self._cover_folder

    def restore(self, project: NovelMixProjectState) -> None:
        self._opening_folder = project.opening_folder
        self._mix_folder = project.mix_folder
        self._cover_folder = project.extra_data.get("cover_dir", "")
        if self._opening_folder:
            self._opening_path_label.setText(self._opening_folder)
        if self._mix_folder:
            self._mix_path_label.setText(self._mix_folder)
        if self._cover_folder:
            self._cover_path_label.setText(self._cover_folder)

        opening_params = project.extra_data.get("opening_params", {})
        self._opening_params_page.set_params(opening_params)

        mix_params = project.extra_data.get("mix_params", {})
        self._mix_params_page.set_params(mix_params)
