import logging
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from clip_synth.tools.video_dedup_tool import DedupWorker

logger = logging.getLogger(__name__)

_ARROW_DIR: str | None = None


def _get_arrow_dir() -> str:
    global _ARROW_DIR
    if _ARROW_DIR is None:
        _ARROW_DIR = tempfile.mkdtemp(prefix="spin_arrows_")
        for char, name in [("+", "up"), ("\u2212", "down")]:
            pm = QPixmap(14, 14)
            pm.fill(Qt.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.TextAntialiasing)
            painter.setPen(Qt.gray)
            font = painter.font()
            font.setPointSize(12)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(pm.rect(), Qt.AlignCenter, char)
            painter.end()
            pm.save(os.path.join(_ARROW_DIR, f"{name}.png"))
    return _ARROW_DIR


def _set_spin_arrows(spin: QSpinBox | QDoubleSpinBox) -> None:
    d = _get_arrow_dir()
    up_path = os.path.join(d, "up.png").replace("\\", "/")
    down_path = os.path.join(d, "down.png").replace("\\", "/")
    spin.setStyleSheet(f"""
        QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
            image: url({up_path});
            width: 14px;
            height: 14px;
        }}
        QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
            image: url({down_path});
            width: 14px;
            height: 14px;
        }}
    """)


class VideoDedupPage(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("videoDedupPage")
        self._video_list_layout: QVBoxLayout | None = None
        self._video_paths: list[str] = []
        self._worker: DedupWorker | None = None
        self._progress_dialog: QProgressDialog | None = None
        self._frame_extract_cb: QCheckBox | None = None
        self._frame_extract_spin_min: QSpinBox | None = None
        self._frame_extract_spin_max: QSpinBox | None = None
        self._bitrate_cb: QCheckBox | None = None
        self._bitrate_spin_min: QDoubleSpinBox | None = None
        self._bitrate_spin_max: QDoubleSpinBox | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._build_toolbar(layout)
        self._build_body(layout)

    def _build_toolbar(self, parent_layout: QVBoxLayout) -> None:
        toolbar = QFrame()
        toolbar.setObjectName("dedupToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(24, 16, 24, 16)

        title_label = QLabel("视频去重")
        title_label.setObjectName("dedupTitle")
        toolbar_layout.addWidget(title_label)

        toolbar_layout.addStretch()

        self._start_process_btn = QPushButton("开始处理")
        self._start_process_btn.setObjectName("dedupStartProcessBtn")
        self._start_process_btn.clicked.connect(self._on_start_process)
        toolbar_layout.addWidget(self._start_process_btn)

        parent_layout.addWidget(toolbar)

    def _build_body(self, parent_layout: QVBoxLayout) -> None:
        body = QHBoxLayout()
        body.setContentsMargins(24, 16, 24, 16)
        body.setSpacing(16)

        self._build_left_panel(body)
        self._build_right_panel(body)

        parent_layout.addLayout(body, stretch=1)

    def _build_left_panel(self, parent_layout: QHBoxLayout) -> None:
        left_panel = QFrame()
        left_panel.setObjectName("dedupLeftPanel")
        left_panel.setFixedWidth(280)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self._add_video_btn = QPushButton("添加视频")
        self._add_video_btn.setObjectName("dedupAddVideoBtn")
        self._add_video_btn.clicked.connect(self._on_add_video)
        left_layout.addWidget(self._add_video_btn)

        scroll = QScrollArea()
        scroll.setObjectName("dedupVideoScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_content.setObjectName("dedupVideoListContent")
        self._video_list_layout = QVBoxLayout(scroll_content)
        self._video_list_layout.setContentsMargins(0, 0, 0, 0)
        self._video_list_layout.setSpacing(8)
        self._video_list_layout.setAlignment(Qt.AlignTop)

        self._video_list_layout.addStretch()
        scroll.setWidget(scroll_content)
        left_layout.addWidget(scroll, stretch=1)

        parent_layout.addWidget(left_panel)

    def _create_video_item(self, name: str) -> QFrame:
        item = QFrame()
        item.setObjectName("dedupVideoItem")
        item.setFixedHeight(48)
        item_layout = QHBoxLayout(item)
        item_layout.setContentsMargins(12, 0, 12, 0)

        label = QLabel(name)
        label.setObjectName("dedupVideoItemLabel")
        item_layout.addWidget(label)

        return item

    def _build_right_panel(self, parent_layout: QHBoxLayout) -> None:
        right_panel = QFrame()
        right_panel.setObjectName("dedupRightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("dedupRightScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        scroll_content = QWidget()
        scroll_content.setObjectName("dedupRightScrollContent")
        content_layout = QVBoxLayout(scroll_content)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(10)
        content_layout.setAlignment(Qt.AlignTop)

        header = QLabel("去重选项")
        header.setObjectName("dedupOptionsHeader")
        content_layout.addWidget(header)

        options_row = QHBoxLayout()
        options_row.setSpacing(10)
        options_row.addWidget(self._build_frame_extract_group(), stretch=1)
        options_row.addWidget(self._build_bitrate_group(), stretch=1)
        content_layout.addLayout(options_row)

        content_layout.addWidget(self._build_image_adjust_group())

        content_layout.addWidget(self._build_advanced_options_group())

        content_layout.addWidget(self._build_crop_group())

        content_layout.addWidget(self._build_scale_group())

        content_layout.addWidget(self._build_move_group())

        content_layout.addStretch()

        scroll.setWidget(scroll_content)
        right_layout.addWidget(scroll, stretch=1)

        parent_layout.addWidget(right_panel, stretch=1)

    @staticmethod
    def _bind_checkbox(cb: QCheckBox, layout: QVBoxLayout) -> None:
        children: list[QWidget] = []

        def _collect(layout_: QVBoxLayout | QHBoxLayout, depth: int = 0) -> None:
            if depth > 6:
                return
            for i in range(layout_.count()):
                item = layout_.itemAt(i)
                if item is None:
                    continue
                w = item.widget()
                if w is not None and w is not cb:
                    children.append(w)
                    continue
                sub = item.layout()
                if sub is not None:
                    _collect(sub, depth + 1)

        _collect(layout)

        def _on_toggled(checked: bool) -> None:
            for w in children:
                w.setEnabled(checked)

        cb.toggled.connect(_on_toggled)
        _on_toggled(cb.isChecked())

    def _build_frame_extract_group(self) -> QFrame:
        group = QFrame()
        group.setObjectName("dedupOptionGroup")

        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setSpacing(10)

        self._frame_extract_cb = QCheckBox("随机抽帧")
        self._frame_extract_cb.setObjectName("dedupGroupCheckBox")
        group_layout.addWidget(self._frame_extract_cb)

        param_row = QHBoxLayout()
        param_row.setSpacing(6)

        label = QLabel("每")
        label.setObjectName("dedupParamLabel")
        param_row.addWidget(label)

        self._frame_extract_spin_min = QSpinBox()
        self._frame_extract_spin_min.setObjectName("dedupSpinBox")
        self._frame_extract_spin_min.setRange(1, 60)
        self._frame_extract_spin_min.setValue(5)
        self._frame_extract_spin_min.setSuffix("")
        _set_spin_arrows(self._frame_extract_spin_min)
        param_row.addWidget(self._frame_extract_spin_min)

        sep = QLabel("~")
        sep.setObjectName("dedupParamSep")
        param_row.addWidget(sep)

        self._frame_extract_spin_max = QSpinBox()
        self._frame_extract_spin_max.setObjectName("dedupSpinBox")
        self._frame_extract_spin_max.setRange(1, 60)
        self._frame_extract_spin_max.setValue(10)
        _set_spin_arrows(self._frame_extract_spin_max)
        param_row.addWidget(self._frame_extract_spin_max)

        label2 = QLabel("帧，抽一帧")
        label2.setObjectName("dedupParamLabel")
        param_row.addWidget(label2)

        param_row.addStretch()
        group_layout.addLayout(param_row)

        self._bind_checkbox(self._frame_extract_cb, group_layout)

        return group

    def _build_bitrate_group(self) -> QFrame:
        group = QFrame()
        group.setObjectName("dedupOptionGroup")

        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setSpacing(10)

        self._bitrate_cb = QCheckBox("码率调整")
        self._bitrate_cb.setObjectName("dedupGroupCheckBox")
        group_layout.addWidget(self._bitrate_cb)

        param_row = QHBoxLayout()
        param_row.setSpacing(6)

        label = QLabel("倍率")
        label.setObjectName("dedupParamLabel")
        param_row.addWidget(label)

        self._bitrate_spin_min = QDoubleSpinBox()
        self._bitrate_spin_min.setObjectName("dedupDoubleSpinBox")
        self._bitrate_spin_min.setRange(0.01, 9.99)
        self._bitrate_spin_min.setDecimals(2)
        self._bitrate_spin_min.setSingleStep(0.01)
        self._bitrate_spin_min.setValue(1.02)
        _set_spin_arrows(self._bitrate_spin_min)
        param_row.addWidget(self._bitrate_spin_min)

        sep = QLabel("~")
        sep.setObjectName("dedupParamSep")
        param_row.addWidget(sep)

        self._bitrate_spin_max = QDoubleSpinBox()
        self._bitrate_spin_max.setObjectName("dedupDoubleSpinBox")
        self._bitrate_spin_max.setRange(0.01, 9.99)
        self._bitrate_spin_max.setDecimals(2)
        self._bitrate_spin_max.setSingleStep(0.01)
        self._bitrate_spin_max.setValue(1.95)
        _set_spin_arrows(self._bitrate_spin_max)
        param_row.addWidget(self._bitrate_spin_max)

        param_row.addStretch()
        group_layout.addLayout(param_row)

        self._bind_checkbox(self._bitrate_cb, group_layout)

        return group

    def _build_image_adjust_group(self) -> QFrame:
        group = QFrame()
        group.setObjectName("dedupOptionGroup")

        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setSpacing(10)

        self._image_adjust_cb = QCheckBox("画面调整")
        self._image_adjust_cb.setObjectName("dedupGroupCheckBox")
        group_layout.addWidget(self._image_adjust_cb)

        self._adjust_spins: dict[str, tuple[QDoubleSpinBox | QSpinBox, QDoubleSpinBox | QSpinBox]] = {}

        adjust_items = [
            ("亮度", True),
            ("锐化", True),
            ("对比度", True),
            ("降噪", True),
            ("饱和度", True),
            ("翻转", False),
        ]

        grid = QVBoxLayout()
        grid.setSpacing(8)

        row_layout: QHBoxLayout | None = None
        for i, (label_text, is_double) in enumerate(adjust_items):
            if i % 2 == 0:
                row_layout = QHBoxLayout()
                row_layout.setSpacing(12)
                grid.addLayout(row_layout)

            item_row = QHBoxLayout()
            item_row.setSpacing(4)

            item_label = QLabel(label_text)
            item_label.setObjectName("dedupParamLabel")
            item_label.setFixedWidth(48)
            item_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item_row.addWidget(item_label)

            if is_double:
                spin_min = QDoubleSpinBox()
                spin_min.setObjectName("dedupDoubleSpinBox")
                spin_min.setRange(0.01, 9.99)
                spin_min.setDecimals(2)
                spin_min.setSingleStep(0.01)
                spin_min.setValue(1.02)
                spin_min.setFixedWidth(80)
                _set_spin_arrows(spin_min)
                item_row.addWidget(spin_min)

                sep = QLabel("~")
                sep.setObjectName("dedupParamSep")
                item_row.addWidget(sep)

                spin_max = QDoubleSpinBox()
                spin_max.setObjectName("dedupDoubleSpinBox")
                spin_max.setRange(0.01, 9.99)
                spin_max.setDecimals(2)
                spin_max.setSingleStep(0.01)
                spin_max.setValue(1.30)
                spin_max.setFixedWidth(80)
                _set_spin_arrows(spin_max)
                item_row.addWidget(spin_max)
            else:
                spin_min = QSpinBox()
                spin_min.setObjectName("dedupSpinBox")
                spin_min.setRange(-360, 360)
                spin_min.setValue(-90)
                spin_min.setFixedWidth(80)
                _set_spin_arrows(spin_min)
                item_row.addWidget(spin_min)

                sep = QLabel("~")
                sep.setObjectName("dedupParamSep")
                item_row.addWidget(sep)

                spin_max = QSpinBox()
                spin_max.setObjectName("dedupSpinBox")
                spin_max.setRange(-360, 360)
                spin_max.setValue(90)
                spin_max.setFixedWidth(80)
                _set_spin_arrows(spin_max)
                item_row.addWidget(spin_max)

            item_row.addStretch()
            row_layout.addLayout(item_row, stretch=1)
            self._adjust_spins[label_text] = (spin_min, spin_max)

            if i % 2 == 0 and i == len(adjust_items) - 1:
                empty = QFrame()
                empty.setFixedWidth(0)
                row_layout.addWidget(empty, stretch=1)

            if i % 2 == 1:
                row_layout.addStretch()

        group_layout.addLayout(grid)

        self._bind_checkbox(self._image_adjust_cb, group_layout)

        return group

    def _build_advanced_options_group(self) -> QFrame:
        group = QFrame()
        group.setObjectName("dedupOptionGroup")

        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setSpacing(10)

        items = [
            "二进制清洗与元数据剥离",
            "随机信号注入",
            "深度伪造与身份伪装",
            "数字指纹调整",
            "随机镜像",
            "随机加速",
        ]

        self._advanced_cbs: dict[str, QCheckBox] = {}

        grid = QVBoxLayout()
        grid.setSpacing(8)

        row_layout: QHBoxLayout | None = None
        for i, text in enumerate(items):
            if i % 2 == 0:
                row_layout = QHBoxLayout()
                row_layout.setSpacing(12)
                grid.addLayout(row_layout)

            cb = QCheckBox(text)
            cb.setObjectName("dedupAdvancedCheckBox")
            row_layout.addWidget(cb, stretch=1)
            self._advanced_cbs[text] = cb

            if i % 2 == 1:
                row_layout.addStretch()

        group_layout.addLayout(grid)

        return group

    def _build_crop_group(self) -> QFrame:
        group = QFrame()
        group.setObjectName("dedupOptionGroup")

        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setSpacing(10)

        crop_cb = QCheckBox("画面裁剪")
        crop_cb.setObjectName("dedupGroupCheckBox")
        self._crop_cb = crop_cb
        group_layout.addWidget(crop_cb)

        crop_inputs_layout = QHBoxLayout()
        crop_inputs_layout.setSpacing(8)

        crop_labels = ["上", "下", "左", "右"]
        self._crop_spins = {}
        for label_text in crop_labels:
            sub_layout = QVBoxLayout()
            sub_layout.setSpacing(2)
            label = QLabel(label_text)
            label.setObjectName("dedupCropLabel")
            label.setAlignment(Qt.AlignCenter)
            sub_layout.addWidget(label)
            spin = QSpinBox()
            spin.setObjectName("dedupCropSpinBox")
            spin.setRange(0, 9999)
            spin.setValue(0)
            spin.setFixedWidth(72)
            _set_spin_arrows(spin)
            sub_layout.addWidget(spin)
            crop_inputs_layout.addLayout(sub_layout)
            self._crop_spins[label_text] = spin

        self._crop_inputs_widget = QFrame()
        self._crop_inputs_widget.setObjectName("dedupCropInputs")
        self._crop_inputs_widget.setLayout(crop_inputs_layout)
        group_layout.addWidget(self._crop_inputs_widget)

        self._bind_checkbox(crop_cb, group_layout)

        return group

    def _build_scale_group(self) -> QFrame:
        group = QFrame()
        group.setObjectName("dedupOptionGroup")

        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setSpacing(10)

        cb = QCheckBox("动态缩放")
        cb.setObjectName("dedupGroupCheckBox")
        self._scale_cb = cb
        group_layout.addWidget(cb)

        param_row = QHBoxLayout()
        param_row.setSpacing(6)

        label = QLabel("倍率")
        label.setObjectName("dedupParamLabel")
        param_row.addWidget(label)

        self._scale_spin_min = QDoubleSpinBox()
        self._scale_spin_min.setObjectName("dedupDoubleSpinBox")
        self._scale_spin_min.setRange(0.01, 9.99)
        self._scale_spin_min.setDecimals(2)
        self._scale_spin_min.setSingleStep(0.01)
        self._scale_spin_min.setValue(1.0)
        self._scale_spin_min.setFixedWidth(80)
        _set_spin_arrows(self._scale_spin_min)
        param_row.addWidget(self._scale_spin_min)

        sep = QLabel("~")
        sep.setObjectName("dedupParamSep")
        param_row.addWidget(sep)

        self._scale_spin_max = QDoubleSpinBox()
        self._scale_spin_max.setObjectName("dedupDoubleSpinBox")
        self._scale_spin_max.setRange(0.01, 9.99)
        self._scale_spin_max.setDecimals(2)
        self._scale_spin_max.setSingleStep(0.01)
        self._scale_spin_max.setValue(1.5)
        self._scale_spin_max.setFixedWidth(80)
        _set_spin_arrows(self._scale_spin_max)
        param_row.addWidget(self._scale_spin_max)

        param_row.addStretch()
        group_layout.addLayout(param_row)

        self._bind_checkbox(cb, group_layout)

        return group

    def _build_move_group(self) -> QFrame:
        group = QFrame()
        group.setObjectName("dedupOptionGroup")

        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setSpacing(10)

        cb = QCheckBox("画面移动")
        cb.setObjectName("dedupGroupCheckBox")
        self._move_cb = cb
        group_layout.addWidget(cb)

        move_inputs_layout = QHBoxLayout()
        move_inputs_layout.setSpacing(8)

        move_labels = ["上", "下", "左", "右"]
        self._move_spins = {}
        for label_text in move_labels:
            sub_layout = QVBoxLayout()
            sub_layout.setSpacing(2)
            label = QLabel(label_text)
            label.setObjectName("dedupCropLabel")
            label.setAlignment(Qt.AlignCenter)
            sub_layout.addWidget(label)
            spin = QSpinBox()
            spin.setObjectName("dedupCropSpinBox")
            spin.setRange(0, 9999)
            spin.setValue(0)
            spin.setFixedWidth(72)
            _set_spin_arrows(spin)
            sub_layout.addWidget(spin)
            move_inputs_layout.addLayout(sub_layout)
            self._move_spins[label_text] = spin

        move_widget = QFrame()
        move_widget.setObjectName("dedupCropInputs")
        move_widget.setLayout(move_inputs_layout)
        group_layout.addWidget(move_widget)

        self._bind_checkbox(cb, group_layout)

        return group

    def _get_frame_extract_params(self) -> tuple[bool, int, int]:
        if hasattr(self, '_frame_extract_cb'):
            return (
                self._frame_extract_cb.isChecked(),
                self._frame_extract_spin_min.value(),
                self._frame_extract_spin_max.value(),
            )
        return False, 5, 10

    def _get_bitrate_params(self) -> tuple[bool, float, float]:
        if hasattr(self, '_bitrate_cb'):
            return (
                self._bitrate_cb.isChecked(),
                self._bitrate_spin_min.value(),
                self._bitrate_spin_max.value(),
            )
        return False, 1.02, 1.95

    def _get_image_adjust_params(self) -> dict:
        params = {
            "enabled": False,
            "brightness": (1.0, 1.0),
            "sharpness": (1.0, 1.0),
            "contrast": (1.0, 1.0),
            "denoise": (1.0, 1.0),
            "saturation": (1.0, 1.0),
            "rotate": (0, 0),
        }
        if not hasattr(self, '_image_adjust_cb') or not self._image_adjust_cb.isChecked():
            return params

        params["enabled"] = True
        for name, (spin_min, spin_max) in self._adjust_spins.items():
            if name == "亮度":
                params["brightness"] = (spin_min.value(), spin_max.value())
            elif name == "锐化":
                params["sharpness"] = (spin_min.value(), spin_max.value())
            elif name == "对比度":
                params["contrast"] = (spin_min.value(), spin_max.value())
            elif name == "降噪":
                params["denoise"] = (spin_min.value(), spin_max.value())
            elif name == "饱和度":
                params["saturation"] = (spin_min.value(), spin_max.value())
            elif name == "翻转":
                params["rotate"] = (spin_min.value(), spin_max.value())
        return params

    def _get_crop_params(self) -> dict:
        params = {
            "enabled": False,
            "top": 0, "bottom": 0, "left": 0, "right": 0,
        }
        if hasattr(self, '_crop_cb') and self._crop_cb.isChecked():
            params["enabled"] = True
            params["top"] = self._crop_spins["上"].value()
            params["bottom"] = self._crop_spins["下"].value()
            params["left"] = self._crop_spins["左"].value()
            params["right"] = self._crop_spins["右"].value()
        return params

    def _get_scale_params(self) -> dict:
        params = {
            "enabled": False,
            "min_ratio": 1.0, "max_ratio": 1.0,
        }
        if hasattr(self, '_scale_cb') and self._scale_cb.isChecked():
            params["enabled"] = True
            params["min_ratio"] = self._scale_spin_min.value()
            params["max_ratio"] = self._scale_spin_max.value()
        return params

    def _get_move_params(self) -> dict:
        params = {
            "enabled": False,
            "top": 0, "bottom": 0, "left": 0, "right": 0,
        }
        if hasattr(self, '_move_cb') and self._move_cb.isChecked():
            params["enabled"] = True
            params["top"] = self._move_spins["上"].value()
            params["bottom"] = self._move_spins["下"].value()
            params["left"] = self._move_spins["左"].value()
            params["right"] = self._move_spins["右"].value()
        return params

    def _get_advanced_params(self) -> dict:
        params = {
            "metadata_clean": False,
            "noise_inject": False,
            "deepfake_spoof": False,
            "fingerprint_check": False,
            "random_mirror": False,
            "random_speed": False,
        }
        if hasattr(self, '_advanced_cbs'):
            for text, cb in self._advanced_cbs.items():
                if text == "二进制清洗与元数据剥离":
                    params["metadata_clean"] = cb.isChecked()
                elif text == "随机信号注入":
                    params["noise_inject"] = cb.isChecked()
                elif text == "深度伪造与身份伪装":
                    params["deepfake_spoof"] = cb.isChecked()
                elif text == "数字指纹调整":
                    params["fingerprint_check"] = cb.isChecked()
                elif text == "随机镜像":
                    params["random_mirror"] = cb.isChecked()
                elif text == "随机加速":
                    params["random_speed"] = cb.isChecked()
        return params

    def _has_any_option_selected(self) -> bool:
        checkboxes = self.findChildren(QCheckBox)
        for cb in checkboxes:
            if cb.isChecked():
                return True
        return False

    def _on_start_process(self) -> None:
        if not self._video_paths:
            QMessageBox.warning(self, "提示", "请先添加视频文件")
            return

        if not self._has_any_option_selected():
            QMessageBox.warning(self, "提示", "请选择至少一项去重选项")
            return

        frame_enabled, frame_min, frame_max = self._get_frame_extract_params()
        bitrate_enabled, bitrate_min, bitrate_max = self._get_bitrate_params()
        image_adjust_params = self._get_image_adjust_params()
        crop_params = self._get_crop_params()
        scale_params = self._get_scale_params()
        move_params = self._get_move_params()
        advanced_params = self._get_advanced_params()

        any_advanced = any(advanced_params.values())
        if not frame_enabled and not bitrate_enabled and not image_adjust_params["enabled"] and not any_advanced and not crop_params["enabled"] and not scale_params["enabled"] and not move_params["enabled"]:
            QMessageBox.warning(self, "提示", "请选择至少一项去重选项")
            return

        output_dir = str(Path.home() / "Desktop" / "FrameCut_Dedup")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        self._progress_dialog = QProgressDialog("正在处理视频...", "取消", 0, 100, self)
        self._progress_dialog.setWindowTitle("视频去重处理")
        self._progress_dialog.setWindowModality(Qt.WindowModal)
        self._progress_dialog.setAutoClose(True)
        self._progress_dialog.setAutoReset(True)
        self._progress_dialog.canceled.connect(self._on_process_cancelled)

        self._start_process_btn.setEnabled(False)

        self._worker = DedupWorker(
            video_paths=self._video_paths,
            output_dir=output_dir,
            frame_extract_enabled=frame_enabled,
            frame_min_frames=frame_min,
            frame_max_frames=frame_max,
            bitrate_enabled=bitrate_enabled,
            bitrate_min=bitrate_min,
            bitrate_max=bitrate_max,
            image_adjust_params=image_adjust_params,
            crop_params=crop_params,
            scale_params=scale_params,
            move_params=move_params,
            advanced_params=advanced_params,
        )
        self._worker.progress.connect(self._on_process_progress)
        self._worker.finished.connect(self._on_process_finished)
        self._worker.error.connect(self._on_process_error)
        self._worker.start()

        self._progress_dialog.show()

        logger.info(
            "开始去重处理: %d 个视频, 随机抽帧=%s, 码率调整=%s, 画面调整=%s, 裁剪=%s, 缩放=%s, 移动=%s, 高级选项=%s",
            len(self._video_paths), frame_enabled, bitrate_enabled,
            image_adjust_params["enabled"], crop_params["enabled"],
            scale_params["enabled"], move_params["enabled"], advanced_params,
        )

    def _on_process_progress(self, pct: int, message: str) -> None:
        self._progress_dialog.setValue(pct)
        self._progress_dialog.setLabelText(message)
        logger.info("[%d%%] %s", pct, message)

    def _on_process_finished(self, output_dir: str) -> None:
        self._progress_dialog.close()
        self._start_process_btn.setEnabled(True)

        QMessageBox.information(self, "完成", "视频去重处理完成！")

        QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
        logger.info("去重处理完成，打开输出目录: %s", output_dir)

    def _on_process_error(self, error_msg: str) -> None:
        self._progress_dialog.close()
        self._start_process_btn.setEnabled(True)
        QMessageBox.critical(self, "处理失败", f"视频去重处理失败:\n{error_msg}")
        logger.error("去重处理失败: %s", error_msg)

    def _on_process_cancelled(self) -> None:
        if self._worker:
            self._worker.cancel()
        self._start_process_btn.setEnabled(True)
        logger.info("用户取消了去重处理")

    def _on_add_video(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mkv *.mov *.wmv)",
        )
        if not files:
            return
        for file_path in files:
            if file_path in self._video_paths:
                continue
            self._video_paths.append(file_path)
            name = file_path.split("/")[-1].split("\\")[-1]
            item = self._create_video_item(name)
            self._video_list_layout.insertWidget(
                self._video_list_layout.count() - 1, item
            )
