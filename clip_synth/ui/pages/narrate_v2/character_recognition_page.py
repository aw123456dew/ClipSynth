import json
import logging
from collections import defaultdict

from PySide6.QtCore import Qt, QByteArray, QMimeData, Signal, QTimer
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from clip_synth.ui.pages.narrate_v2.video_preview_dialog import VideoPreviewDialog

logger = logging.getLogger("clip_synth.narrate_v2")

UTTERANCE_MIME = "application/x-clipsynth-utterance"

PRESET_ROLES = [
    ("女性", ["女人", "女孩", "少女", "渣女", "小美", "美女", "心机女", "老阿姨"]),
    ("男性", ["男人", "总裁", "小叔", "渣男", "心机男", "男孩", "大壮", "帅哥", "小伙"]),
    ("家庭", ["母亲", "婆婆", "岳母", "保姆", "父亲", "岳父", "公公", "宝宝", "萌宝"]),
]


class RoleAliasPopup(QFrame):
    def __init__(self, input_widget, parent=None):
        super().__init__(parent)
        self._input_widget = input_widget
        self.setObjectName("roleAliasPopup")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(6)

        for category, roles in PRESET_ROLES:
            cat_label = QLabel(category)
            cat_label.setStyleSheet("color: #64748b; font-size: 11px; font-weight: 600;")
            main_layout.addWidget(cat_label)

            row = QHBoxLayout()
            row.setSpacing(6)
            row.setContentsMargins(0, 0, 0, 0)
            for role in roles:
                btn = QPushButton(role)
                btn.setObjectName("roleAliasBtn")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda checked, r=role: self._select_role(r))
                row.addWidget(btn)
            row.addStretch()
            main_layout.addLayout(row)

    def _select_role(self, role: str):
        self._input_widget.setText(role)
        self._input_widget.setFocus()
        self.close()


class RoleAliasInput(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup = None
        self.setReadOnly(False)
        self.setFocusPolicy(Qt.StrongFocus)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.setFocus()
        self.selectAll()
        QTimer.singleShot(0, self._show_popup)

    def _show_popup(self):
        if self._popup is not None:
            try:
                self._popup.close()
            except RuntimeError:
                pass
            self._popup = None

        self._popup = RoleAliasPopup(self, None)
        self._popup.destroyed.connect(self._on_popup_destroyed)
        self._popup.adjustSize()

        pos = self.mapToGlobal(self.rect().bottomLeft())
        screen = self.screen()
        if screen:
            screen_geo = screen.availableGeometry()
            popup_right = pos.x() + self._popup.width()
            if popup_right > screen_geo.right():
                pos.setX(max(screen_geo.left(), screen_geo.right() - self._popup.width()))
            popup_bottom = pos.y() + self._popup.height()
            if popup_bottom > screen_geo.bottom():
                pos.setY(self.mapToGlobal(self.rect().topLeft()).y() - self._popup.height())

        self._popup.move(pos)
        self._popup.show()

    def _on_popup_destroyed(self):
        self._popup = None

    def focusOutEvent(self, event):
        QTimer.singleShot(100, self._check_popup)
        super().focusOutEvent(event)

    def _check_popup(self):
        if self._popup is not None and not self.hasFocus():
            pw = self._popup
            if pw is not None and pw.isVisible():
                try:
                    pw.close()
                except RuntimeError:
                    pass
            self._popup = None


class EditableTextEdit(QLineEdit):
    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class SpeakerTextItem(QFrame):
    def __init__(self, text: str, start_ms: int, end_ms: int, merged_video_path: str, parent=None):
        super().__init__(parent)
        self._start_ms = start_ms
        self._end_ms = end_ms
        self._merged_video_path = merged_video_path
        self._text = text
        self._setup_ui()

    def _setup_ui(self):
        self.setObjectName("speakerTextItem")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 8, 12, 8)
        layout.setSpacing(8)

        drag_icon = QLabel("⠿")
        drag_icon.setStyleSheet("color: #475569; font-size: 16px; padding: 0 4px;")
        drag_icon.setFixedWidth(24)
        drag_icon.setAlignment(Qt.AlignCenter)
        layout.addWidget(drag_icon)

        time_label = QLabel(f"[{self._start_ms // 1000}.{self._start_ms % 1000:03d}s - {self._end_ms // 1000}.{self._end_ms % 1000:03d}s]")
        time_label.setStyleSheet("color: #64748b; font-size: 12px; min-width: 120px;")
        layout.addWidget(time_label)

        self._text_edit = EditableTextEdit(self._text)
        self._text_edit.setObjectName("speakerTextEdit")
        self._text_edit.setReadOnly(True)
        self._text_edit.double_clicked.connect(self._on_text_double_click)
        self._text_edit.editingFinished.connect(self._on_text_edited)
        layout.addWidget(self._text_edit, stretch=1)

        preview_btn = QPushButton("预览")
        preview_btn.setObjectName("segmentPreviewBtn")
        preview_btn.setCursor(Qt.PointingHandCursor)
        preview_btn.clicked.connect(self._on_preview)
        layout.addWidget(preview_btn)

    def _on_text_double_click(self):
        self._text_edit.setReadOnly(False)
        self._text_edit.setStyleSheet("color: #e2e8f0; font-size: 13px; background: #1a1f2e; border: 1px solid #6366f1; border-radius: 4px; padding: 2px 6px;")
        self._text_edit.selectAll()

    def _on_text_edited(self):
        self._text_edit.setReadOnly(True)
        self._text_edit.setStyleSheet("color: #e2e8f0; font-size: 13px; background: transparent; border: none; padding: 2px 4px;")
        self._text = self._text_edit.text().strip()

    def _on_preview(self):
        dialog = VideoPreviewDialog(self._merged_video_path, self._start_ms, self._end_ms)
        dialog.exec()


class SpeakerTextList(QListWidget):
    def __init__(self, speaker_id: str, page, parent=None):
        super().__init__(parent)
        self._speaker_id = speaker_id
        self._page = page
        self.setObjectName("speakerTextList")
        self.setDragEnabled(True)
        self.setAcceptDrops(False)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setFocusPolicy(Qt.NoFocus)

    def mimeTypes(self):
        return [UTTERANCE_MIME]

    def mimeData(self, items):
        if not items:
            return None
        item = items[0]
        data = item.data(Qt.UserRole)
        if data is None:
            return None
        mime = QMimeData()
        payload = {"utterance": data, "speaker_id": self._speaker_id}
        mime.setData(UTTERANCE_MIME, QByteArray(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
        return mime


class SpeakerCard(QFrame):
    def __init__(self, speaker_id: str, utterances: list, merged_video_path: str, page, parent=None):
        super().__init__(parent)
        self._speaker_id = speaker_id
        self._utterances = utterances
        self._merged_video_path = merged_video_path
        self._page = page
        self._expanded = False
        self._setup_ui()

    def _setup_ui(self):
        self.setObjectName("speakerCard")
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QFrame()
        header.setObjectName("speakerCardHeader")
        header.setCursor(Qt.PointingHandCursor)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)

        self._expand_arrow = QLabel("▶")
        self._expand_arrow.setStyleSheet("color: #64748b; font-size: 12px;")
        header_layout.addWidget(self._expand_arrow)

        avatar = QLabel("👤")
        avatar.setStyleSheet("font-size: 24px;")
        header_layout.addWidget(avatar)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        alias_row = QHBoxLayout()
        alias_row.setSpacing(8)

        speaker_label = QLabel(f"说话人 {self._speaker_id}")
        speaker_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        alias_row.addWidget(speaker_label)

        alias_label = QLabel("别称：")
        alias_label.setStyleSheet("color: #64748b; font-size: 12px;")
        alias_row.addWidget(alias_label)

        self._alias_input = RoleAliasInput()
        self._alias_input.setObjectName("speakerAliasInput")
        self._alias_input.setPlaceholderText(f"说话人{self._speaker_id}")
        self._alias_input.setMaxLength(20)
        self._alias_input.setMinimumWidth(100)
        self._alias_input.setMaximumWidth(200)
        self._alias_input.textChanged.connect(lambda: self._page.utterance_moved.emit())
        alias_row.addWidget(self._alias_input)

        alias_row.addStretch()
        info_layout.addLayout(alias_row)

        self._count_label = QLabel(f"{len(self._utterances)} 条台词")
        self._count_label.setStyleSheet("color: #475569; font-size: 11px;")
        info_layout.addWidget(self._count_label)

        header_layout.addLayout(info_layout, stretch=1)
        header.mousePressEvent = lambda event: self._toggle_expand()
        layout.addWidget(header)

        self._text_list = SpeakerTextList(self._speaker_id, self._page)
        self._text_list.setMinimumHeight(min(len(self._utterances) * 48, 300))
        self._text_list.hide()

        for utt in self._utterances:
            self._add_utterance_item(utt)

        layout.addWidget(self._text_list)

    def _add_utterance_item(self, utt: dict):
        item_widget = SpeakerTextItem(
            utt["text"], utt["start_time"], utt["end_time"], self._merged_video_path,
        )
        list_item = QListWidgetItem()
        list_item.setSizeHint(item_widget.sizeHint())
        list_item.setData(Qt.UserRole, utt)

        insert_idx = self._text_list.count()
        for i in range(self._text_list.count()):
            existing = self._text_list.item(i)
            if existing:
                existing_utt = existing.data(Qt.UserRole)
                if existing_utt and utt["start_time"] < existing_utt.get("start_time", 0):
                    insert_idx = i
                    break

        self._text_list.insertItem(insert_idx, list_item)
        self._text_list.setItemWidget(list_item, item_widget)

    def _toggle_expand(self):
        self._expanded = not self._expanded
        self._text_list.setVisible(self._expanded)
        self._expand_arrow.setText("▼" if self._expanded else "▶")

    def update_count(self):
        count = self._text_list.count()
        self._count_label.setText(f"{count} 条台词")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasFormat(UTTERANCE_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        if not event.mimeData().hasFormat(UTTERANCE_MIME):
            return

        raw = bytes(event.mimeData().data(UTTERANCE_MIME)).decode("utf-8")
        payload = json.loads(raw)
        utterance = payload["utterance"]
        source_speaker = payload["speaker_id"]

        if source_speaker == self._speaker_id:
            return

        self._page.move_utterance(utterance, source_speaker, self._speaker_id)
        event.acceptProposedAction()

    @property
    def alias(self) -> str:
        text = self._alias_input.text().strip()
        return text if text else f"说话人{self._speaker_id}"


class CharacterRecognitionPage(QFrame):
    utterance_moved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recognition_data = None
        self._merged_video_path = ""
        self._speaker_cards: list[SpeakerCard] = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(24)

        title = QLabel("人物识别")
        title.setObjectName("aiStyleTitle")
        layout.addWidget(title)

        desc = QLabel("识别视频中的人物角色，为解说文案提供角色信息。点击「预览」可播放对应片段。")
        desc.setObjectName("dialogFieldHint")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("mixScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._scroll_content = QWidget()
        self._scroll_content.setObjectName("mixScrollContent")
        self._speaker_layout = QVBoxLayout(self._scroll_content)
        self._speaker_layout.setContentsMargins(0, 0, 0, 0)
        self._speaker_layout.setSpacing(16)
        self._speaker_layout.setAlignment(Qt.AlignTop)

        scroll_area.setWidget(self._scroll_content)
        layout.addWidget(scroll_area, stretch=1)

    def move_utterance(self, utterance: dict, source_speaker: str, target_speaker: str):
        source_card = self._find_card(source_speaker)
        target_card = self._find_card(target_speaker)

        if not source_card or not target_card:
            return

        for i in range(source_card._text_list.count()):
            item = source_card._text_list.item(i)
            if item and item.data(Qt.UserRole) == utterance:
                widget = source_card._text_list.itemWidget(item)
                source_card._text_list.takeItem(i)
                if widget:
                    widget.setParent(None)
                    widget.deleteLater()
                source_card.update_count()
                break

        target_card._add_utterance_item(utterance)
        target_card.update_count()

        if source_card._text_list.count() == 0:
            idx = self._speaker_layout.indexOf(source_card)
            if idx >= 0:
                self._speaker_layout.takeAt(idx)
                source_card.setParent(None)
                source_card.deleteLater()
                self._speaker_cards = [c for c in self._speaker_cards if c is not source_card]

        self.utterance_moved.emit()

    def _find_card(self, speaker_id: str):
        for card in self._speaker_cards:
            if card._speaker_id == speaker_id:
                return card
        return None

    def load_recognition_data(self, data: dict):
        self._recognition_data = data
        utterances = data.get("utterances", [])
        self._merged_video_path = data.get("merged_video_path", "")

        while self._speaker_layout.count():
            item = self._speaker_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        self._speaker_cards.clear()

        non_empty = [u for u in utterances if u.get("text", "").strip()]
        speaker_groups = defaultdict(list)
        for utt in non_empty:
            speaker = utt.get("speaker", "0") or "0"
            speaker_groups[speaker].append(utt)

        sorted_speakers = sorted(speaker_groups.keys(), key=lambda x: int(x) if x.isdigit() else 0)

        for speaker_id in sorted_speakers:
            card = SpeakerCard(speaker_id, speaker_groups[speaker_id], self._merged_video_path, self)
            self._speaker_layout.addWidget(card)
            self._speaker_cards.append(card)

    def get_speaker_aliases(self) -> dict:
        return {card._speaker_id: card.alias for card in self._speaker_cards}

    def get_utterances(self) -> list:
        result = []
        for card in self._speaker_cards:
            for i in range(card._text_list.count()):
                item = card._text_list.item(i)
                utt = dict(item.data(Qt.UserRole))
                utt["speaker"] = card._speaker_id
                result.append(utt)
        result.sort(key=lambda u: u.get("start_time", 0))
        return result
