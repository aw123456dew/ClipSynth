import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from clip_synth.models.novel_comic_project_state import (
    NovelComicChapterState,
    NovelComicProjectState,
)
from clip_synth.services.novel_comic_state_service import NovelComicStateService

logger = logging.getLogger("clip_synth.novel_comic_chapter")


class NovelComicChapterInputDialog(QDialog):
    def __init__(self, episode_num: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._episode_num = episode_num
        self.setWindowTitle(f"第{episode_num}集 - 输入小说文本")
        self.setFixedSize(560, 420)
        self.setObjectName("chapterInputDialog")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title_label = QLabel(f"第{self._episode_num}集")
        title_label.setObjectName("dialogTitle")
        layout.addWidget(title_label)

        hint_label = QLabel("请输入本集小说文本内容：")
        hint_label.setObjectName("dialogFieldLabel")
        layout.addWidget(hint_label)

        self._text_edit = QTextEdit()
        self._text_edit.setObjectName("chapterTextEdit")
        self._text_edit.setPlaceholderText("在此粘贴或输入小说文本...")
        layout.addWidget(self._text_edit, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._confirm_btn = QPushButton("确定")
        self._confirm_btn.setObjectName("dialogConfirmBtn")
        self._confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(self._confirm_btn)

        layout.addLayout(btn_row)

    @property
    def chapter_text(self) -> str:
        return self._text_edit.toPlainText().strip()

    def set_text(self, text: str) -> None:
        self._text_edit.setPlainText(text)


class NovelComicChapterCard(QFrame):
    generate_comic = Signal(int)
    generate_video = Signal(int)
    edit_chapter = Signal(int)

    def __init__(
        self,
        episode_num: int,
        chapter: NovelComicChapterState,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._episode_num = episode_num
        self._chapter = chapter
        self.setObjectName("chapterCard")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        episode_label = QLabel(f"第{self._episode_num}集")
        episode_label.setObjectName("chapterEpisodeLabel")
        episode_label.setFixedWidth(60)
        layout.addWidget(episode_label)

        preview_text = self._chapter.text[:80] + "..." if len(self._chapter.text) > 80 else self._chapter.text
        text_label = QLabel(preview_text)
        text_label.setObjectName("chapterTextLabel")
        text_label.setWordWrap(True)
        text_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        text_label.setCursor(Qt.PointingHandCursor)
        text_label.mouseDoubleClickEvent = lambda e: self.edit_chapter.emit(self._episode_num)
        layout.addWidget(text_label, stretch=1)

        self._gen_btn = QPushButton("生成漫画")
        self._gen_btn.setObjectName("chapterGenBtn")
        self._gen_btn.setCursor(Qt.PointingHandCursor)
        self._gen_btn.clicked.connect(lambda: self.generate_comic.emit(self._episode_num))
        layout.addWidget(self._gen_btn)

        self._gen_video_btn = QPushButton("生成漫画视频")
        self._gen_video_btn.setObjectName("chapterGenVideoBtn")
        self._gen_video_btn.setCursor(Qt.PointingHandCursor)
        self._gen_video_btn.clicked.connect(lambda: self.generate_video.emit(self._episode_num))
        layout.addWidget(self._gen_video_btn)


class NovelComicChapterPage(QFrame):
    back_to_list = Signal()
    open_generate_page = Signal(str, int)
    open_comic_video_dub = Signal(str, int)
    open_comic_video_image = Signal(str, int, str)

    def __init__(
        self,
        project: NovelComicProjectState,
        state_service: NovelComicStateService,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._project = project
        self._state_service = state_service
        self._chapter_cards: list[NovelComicChapterCard] = []
        self.setObjectName("novelComicChapterPage")
        self._first_enter = len(self._project.chapters) == 0
        self._setup_ui()

        if self._first_enter:
            self._show_first_chapter_dialog()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QFrame()
        toolbar.setObjectName("mixToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(24, 16, 24, 16)

        back_btn = QPushButton("\u2190 返回")
        back_btn.setObjectName("chapterBackBtn")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self._on_back)
        toolbar_layout.addWidget(back_btn)

        title_label = QLabel(self._project.name)
        title_label.setObjectName("mixTitle")
        toolbar_layout.addWidget(title_label)

        toolbar_layout.addStretch()

        self._add_chapter_btn = QPushButton("新增章节")
        self._add_chapter_btn.setObjectName("mixNewProjectBtn")
        self._add_chapter_btn.clicked.connect(self._on_add_chapter)
        toolbar_layout.addWidget(self._add_chapter_btn)

        layout.addWidget(toolbar)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("chapterScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_content.setObjectName("chapterScrollContent")
        self._chapters_layout = QVBoxLayout(scroll_content)
        self._chapters_layout.setContentsMargins(24, 16, 24, 16)
        self._chapters_layout.setSpacing(8)
        self._chapters_layout.setAlignment(Qt.AlignTop)

        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area, stretch=1)

        self._refresh_chapter_list()

    def _refresh_chapter_list(self) -> None:
        while self._chapters_layout.count():
            item = self._chapters_layout.takeAt(0)
            if item:
                widget = item.widget()
                if widget:
                    widget.setParent(None)
                    widget.deleteLater()

        self._chapter_cards.clear()

        if not self._project.chapters:
            empty_label = QLabel("暂无章节，点击「新增章节」开始添加")
            empty_label.setObjectName("chapterEmptyLabel")
            empty_label.setAlignment(Qt.AlignCenter)
            self._chapters_layout.addWidget(empty_label)
            return

        for i, chapter in enumerate(self._project.chapters):
            card = NovelComicChapterCard(
                episode_num=i + 1,
                chapter=chapter,
            )
            card.generate_comic.connect(self._on_generate_comic)
            card.generate_video.connect(self._on_generate_video)
            card.edit_chapter.connect(self._on_edit_chapter)
            self._chapter_cards.append(card)
            self._chapters_layout.addWidget(card)

    def _show_first_chapter_dialog(self) -> None:
        dialog = NovelComicChapterInputDialog(1, self.window())
        if dialog.exec() == QDialog.Accepted:
            text = dialog.chapter_text
            if text:
                self._project.chapters.append(NovelComicChapterState(text=text))
                self._state_service.save_project(self._project)
                self._first_enter = False
                self._refresh_chapter_list()

    def _on_add_chapter(self) -> None:
        next_episode = len(self._project.chapters) + 1
        dialog = NovelComicChapterInputDialog(next_episode, self.window())
        if dialog.exec() == QDialog.Accepted:
            text = dialog.chapter_text
            if text:
                self._project.chapters.append(NovelComicChapterState(text=text))
                self._state_service.save_project(self._project)
                self._refresh_chapter_list()

    def _on_edit_chapter(self, episode_num: int) -> None:
        idx = episode_num - 1
        if idx < 0 or idx >= len(self._project.chapters):
            return
        dialog = NovelComicChapterInputDialog(episode_num, self.window())
        dialog.set_text(self._project.chapters[idx].text)
        dialog.setWindowTitle(f"编辑第{episode_num}集")
        if dialog.exec() == QDialog.Accepted:
            new_text = dialog.chapter_text
            if new_text:
                self._project.chapters[idx].text = new_text
                self._state_service.save_project(self._project)
                self._refresh_chapter_list()

    def _on_generate_comic(self, episode_num: int) -> None:
        self.open_generate_page.emit(self._project.id, episode_num)

    def _on_generate_video(self, episode_num: int) -> None:
        project = self._state_service.load_project(self._project.id)
        cache_key = f"comic_video_dub_done_ep{episode_num}"
        text_key = f"comic_video_dubbed_text_ep{episode_num}"
        if project and project.extra_data.get(cache_key):
            dubbed_text = project.extra_data.get(text_key, "")
            self.open_comic_video_image.emit(self._project.id, episode_num, dubbed_text)
            return

        from clip_synth.ui.pages.comic_video_page import _ComicVideoDubModeDialog
        dialog = _ComicVideoDubModeDialog(self.window())
        if dialog.exec() != QDialog.Accepted:
            return
        if dialog.selected_mode == "system":
            self.open_comic_video_dub.emit(self._project.id, episode_num)

    def _on_back(self) -> None:
        self.back_to_list.emit()
