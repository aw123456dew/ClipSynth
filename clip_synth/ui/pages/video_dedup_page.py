import os
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

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

        self._add_video_btn = QPushButton("添加视频")
        self._add_video_btn.setObjectName("dedupAddVideoBtn")
        self._add_video_btn.clicked.connect(self._on_add_video)
        toolbar_layout.addWidget(self._add_video_btn)

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

        cb = QCheckBox("随机抽帧")
        cb.setObjectName("dedupGroupCheckBox")
        group_layout.addWidget(cb)

        param_row = QHBoxLayout()
        param_row.setSpacing(6)

        label = QLabel("每")
        label.setObjectName("dedupParamLabel")
        param_row.addWidget(label)

        spin = QSpinBox()
        spin.setObjectName("dedupSpinBox")
        spin.setRange(1, 60)
        spin.setValue(5)
        spin.setSuffix("")
        _set_spin_arrows(spin)
        param_row.addWidget(spin)

        sep = QLabel("~")
        sep.setObjectName("dedupParamSep")
        param_row.addWidget(sep)

        spin2 = QSpinBox()
        spin2.setObjectName("dedupSpinBox")
        spin2.setRange(1, 60)
        spin2.setValue(10)
        _set_spin_arrows(spin2)
        param_row.addWidget(spin2)

        label2 = QLabel("秒，抽一帧")
        label2.setObjectName("dedupParamLabel")
        param_row.addWidget(label2)

        param_row.addStretch()
        group_layout.addLayout(param_row)

        self._bind_checkbox(cb, group_layout)

        return group

    def _build_bitrate_group(self) -> QFrame:
        group = QFrame()
        group.setObjectName("dedupOptionGroup")

        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setSpacing(10)

        cb = QCheckBox("码率调整")
        cb.setObjectName("dedupGroupCheckBox")
        group_layout.addWidget(cb)

        param_row = QHBoxLayout()
        param_row.setSpacing(6)

        label = QLabel("倍率")
        label.setObjectName("dedupParamLabel")
        param_row.addWidget(label)

        spin = QDoubleSpinBox()
        spin.setObjectName("dedupDoubleSpinBox")
        spin.setRange(0.01, 9.99)
        spin.setDecimals(2)
        spin.setSingleStep(0.01)
        spin.setValue(1.02)
        _set_spin_arrows(spin)
        param_row.addWidget(spin)

        sep = QLabel("~")
        sep.setObjectName("dedupParamSep")
        param_row.addWidget(sep)

        spin2 = QDoubleSpinBox()
        spin2.setObjectName("dedupDoubleSpinBox")
        spin2.setRange(0.01, 9.99)
        spin2.setDecimals(2)
        spin2.setSingleStep(0.01)
        spin2.setValue(1.95)
        _set_spin_arrows(spin2)
        param_row.addWidget(spin2)

        param_row.addStretch()
        group_layout.addLayout(param_row)

        self._bind_checkbox(cb, group_layout)

        return group

    def _build_image_adjust_group(self) -> QFrame:
        group = QFrame()
        group.setObjectName("dedupOptionGroup")

        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(12, 12, 12, 12)
        group_layout.setSpacing(10)

        cb = QCheckBox("画面调整")
        cb.setObjectName("dedupGroupCheckBox")
        group_layout.addWidget(cb)

        adjust_items = [
            "亮度",
            "锐化",
            "对比度",
            "降噪",
            "饱和度",
            "翻转",
        ]

        grid = QVBoxLayout()
        grid.setSpacing(8)

        row_layout: QHBoxLayout | None = None
        for i, label_text in enumerate(adjust_items):
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

            spin = QDoubleSpinBox()
            spin.setObjectName("dedupDoubleSpinBox")
            spin.setRange(0.01, 9.99)
            spin.setDecimals(2)
            spin.setSingleStep(0.01)
            spin.setValue(1.02)
            spin.setFixedWidth(80)
            _set_spin_arrows(spin)
            item_row.addWidget(spin)

            sep = QLabel("~")
            sep.setObjectName("dedupParamSep")
            item_row.addWidget(sep)

            spin2 = QDoubleSpinBox()
            spin2.setObjectName("dedupDoubleSpinBox")
            spin2.setRange(0.01, 9.99)
            spin2.setDecimals(2)
            spin2.setSingleStep(0.01)
            spin2.setValue(1.30)
            spin2.setFixedWidth(80)
            _set_spin_arrows(spin2)
            item_row.addWidget(spin2)

            item_row.addStretch()
            row_layout.addLayout(item_row, stretch=1)

            if i % 2 == 0 and i == len(adjust_items) - 1:
                empty = QFrame()
                empty.setFixedWidth(0)
                row_layout.addWidget(empty, stretch=1)

            if i % 2 == 1:
                row_layout.addStretch()

        group_layout.addLayout(grid)

        self._bind_checkbox(cb, group_layout)

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
            name = file_path.split("/")[-1].split("\\")[-1]
            item = self._create_video_item(name)
            self._video_list_layout.insertWidget(
                self._video_list_layout.count() - 1, item
            )
