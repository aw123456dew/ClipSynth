"""小说改写详情页面：导入小说 → 分割章节 → 查看章节内容"""

import logging
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from clip_synth.services.ai_service import AIModelConfig

from clip_synth.services.novel_rewrite_worker import NovelRewriteWorker

logger = logging.getLogger("clip_synth.novel_rewrite")


# ── 默认章节分割逻辑 ─────────────────────────────────────────


DEFAULT_CHAPTER_PATTERNS = [
    r"第[一二三四五六七八九十百千零\d]+[章节回]",
    r"第[一二三四五六七八九十百千零\d]+[章节回].*",
    r"第\s*\d+\s*[章节回]",
    r"序章|序言|前言|楔子|尾声|后记|番外",
    r"Chapter\s*\d+|CHAPTER\s*\d+",
    r"Vol\.?\s*\d+",
]


def split_chapters_default(text: str) -> list[dict[str, str]]:
    """默认分割逻辑：按常见章节标题模式拆分"""
    # 合并所有pattern
    combined = "|".join(f"({p})" for p in DEFAULT_CHAPTER_PATTERNS)
    pattern = re.compile(combined, re.MULTILINE)

    lines = text.split("\n")
    chapters: list[dict[str, str]] = []
    current_title = "前言"
    current_lines: list[str] = []

    def flush():
        nonlocal current_title, current_lines
        if current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                chapters.append({"title": current_title.strip(), "content": content})
        current_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            current_lines.append(line)
            continue
        if pattern.match(stripped):
            flush()
            current_title = stripped
        else:
            current_lines.append(line)

    flush()

    if not chapters:
        # 没匹配到任何章节标题，整本作为一章
        chapters.append({"title": "全文", "content": text.strip()})

    return chapters


def split_chapters_custom(text: str, pattern_str: str) -> list[dict[str, str]]:
    """自定义分割逻辑：使用用户提供的正则"""
    try:
        pattern = re.compile(pattern_str, re.MULTILINE)
    except re.error as e:
        raise ValueError(f"正则表达式无效: {e}")

    lines = text.split("\n")
    chapters: list[dict[str, str]] = []
    current_title = "开头"
    current_lines: list[str] = []

    def flush():
        nonlocal current_title, current_lines
        if current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                chapters.append({"title": current_title.strip(), "content": content})
        current_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            current_lines.append(line)
            continue
        if pattern.match(stripped):
            flush()
            current_title = stripped
        else:
            current_lines.append(line)

    flush()

    if not chapters:
        chapters.append({"title": "全文", "content": text.strip()})

    return chapters



class CustomSplitDialog(QDialog):
    """自定义分割逻辑输入对话框"""

    pattern_submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自定义章节分割规则")
        self.setFixedSize(520, 200)
        self.setObjectName("confirmDialog")
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("输入自定义分割规则（正则表达式）")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        desc = QLabel(
            "匹配到的行将作为章节标题。例如：<br>"
            "<code>第\\d+章</code> 匹配「第1章」「第12章」等<br>"
            "<code>第[一二三四五六七八九十百千零]+章</code> 匹配中文数字"
        )
        desc.setObjectName("exportInfo")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._pattern_input = QPlainTextEdit()
        self._pattern_input.setObjectName("genSettingsPrefixEdit")
        self._pattern_input.setFixedHeight(50)
        self._pattern_input.setPlaceholderText("输入正则表达式...")
        self._pattern_input.setPlainText("第\\d+[章节回]")
        layout.addWidget(self._pattern_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("dialogConfirmBtn")
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _on_ok(self) -> None:
        pattern = self._pattern_input.toPlainText().strip()
        if not pattern:
            QMessageBox.warning(self, "提示", "请输入正则表达式")
            return
        self.pattern_submitted.emit(pattern)
        self.accept()


class ChapterContentDialog(QDialog):
    """查看章节内容对话框"""

    def __init__(self, title: str, content: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"章节内容 - {title}")
        self.setFixedSize(700, 600)
        self.setObjectName("confirmDialog")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("dialogTitle")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)

        text_edit = QPlainTextEdit()
        text_edit.setObjectName("genSettingsPrefixEdit")
        text_edit.setPlainText(content)
        text_edit.setReadOnly(True)
        layout.addWidget(text_edit, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("dialogConfirmBtn")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


class _StartProcessDialog(QDialog):
    """开始处理对话框：输入主角名，启动AI改写"""

    def __init__(self, chapter_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("开始处理")
        self.setFixedSize(480, 260)
        self.setObjectName("confirmDialog")
        self._setup_ui(chapter_count)

    def _setup_ui(self, chapter_count: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        title = QLabel("开始AI改写")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        info = QLabel(
            f"共 {chapter_count} 章，将按≤8000字打包成批次，逐批使用AI改写为第一人称视角。<br>"
            "请在下方输入主角的姓名/称呼。"
        )
        info.setObjectName("exportInfo")
        info.setWordWrap(True)
        layout.addWidget(info)

        name_label = QLabel("主角姓名")
        name_label.setObjectName("dialogFieldLabel")
        layout.addWidget(name_label)

        self._name_input = QPlainTextEdit()
        self._name_input.setObjectName("genSettingsPrefixEdit")
        self._name_input.setFixedHeight(40)
        self._name_input.setPlaceholderText("输入主角姓名（如：陈凡）")
        layout.addWidget(self._name_input)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("dialogCancelBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._confirm_btn = QPushButton("开始处理")
        self._confirm_btn.setObjectName("dialogConfirmBtn")
        self._confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(self._confirm_btn)
        layout.addLayout(btn_row)

    def _on_confirm(self) -> None:
        name = self._name_input.toPlainText().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入主角姓名")
            return
        self._protagonist = name
        self.accept()

    @property
    def protagonist(self) -> str:
        return getattr(self, "_protagonist", "")


class NovelRewriteDetailPage(QFrame):
    back_to_list = Signal()

    def __init__(
        self,
        project_id: str,
        state_service,
        settings_service=None,
        parent=None,
    ):
        super().__init__(parent)
        self._project_id = project_id
        self._state_service = state_service
        self._settings_service = settings_service
        self._project = state_service.get_project(project_id)
        self._novel_text = ""
        self._chapters: list[dict[str, str]] = []
        self._chapter_cards: list[QFrame] = []
        self._rewrite_worker: NovelRewriteWorker | None = None
        self.setObjectName("novelComicChapterPage")
        self._setup_ui()
        self._load_state()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 工具栏 ──
        toolbar = QFrame()
        toolbar.setObjectName("mixToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(24, 16, 24, 16)

        back_btn = QPushButton("\u2190 返回项目列表")
        back_btn.setObjectName("chapterBackBtn")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self.back_to_list.emit)
        toolbar_layout.addWidget(back_btn)

        title_label = QLabel(self._project.name if self._project else "小说改写")
        title_label.setObjectName("mixTitle")
        toolbar_layout.addWidget(title_label)

        toolbar_layout.addStretch()

        self._import_btn = QPushButton("📂  导入小说")
        self._import_btn.setObjectName("comicGenActionBtn")
        self._import_btn.setCursor(Qt.PointingHandCursor)
        self._import_btn.clicked.connect(self._on_import_novel)
        toolbar_layout.addWidget(self._import_btn)

        self._auto_split_btn = QPushButton("🔀  默认分割章节")
        self._auto_split_btn.setObjectName("comicGenActionBtn")
        self._auto_split_btn.setCursor(Qt.PointingHandCursor)
        self._auto_split_btn.setEnabled(False)
        self._auto_split_btn.clicked.connect(self._on_auto_split)
        toolbar_layout.addWidget(self._auto_split_btn)

        self._custom_split_btn = QPushButton("⚙️  自定义分割")
        self._custom_split_btn.setObjectName("comicGenActionBtn")
        self._custom_split_btn.setCursor(Qt.PointingHandCursor)
        self._custom_split_btn.setEnabled(False)
        self._custom_split_btn.clicked.connect(self._on_custom_split)
        toolbar_layout.addWidget(self._custom_split_btn)

        self._process_btn = QPushButton("▶️  开始处理")
        self._process_btn.setObjectName("mixNewProjectBtn")
        self._process_btn.setCursor(Qt.PointingHandCursor)
        self._process_btn.setEnabled(False)
        self._process_btn.clicked.connect(self._on_start_process)
        toolbar_layout.addWidget(self._process_btn)

        self._export_btn = QPushButton("📤  导出")
        self._export_btn.setObjectName("comicGenActionBtn")
        self._export_btn.setCursor(Qt.PointingHandCursor)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._on_export_rewritten)
        toolbar_layout.addWidget(self._export_btn)

        layout.addWidget(toolbar)

        # ── 状态信息 ──
        self._status_label = QLabel("请先导入TXT小说文件")
        self._status_label.setObjectName("assetExtractStatus")
        self._status_label.setStyleSheet("color: #94a3b8; padding: 8px 24px;")
        layout.addWidget(self._status_label)

        # ── 章节列表 ──
        scroll_area = QScrollArea()
        scroll_area.setObjectName("mixScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        scroll_content.setObjectName("mixScrollContent")
        self._chapters_layout = QVBoxLayout(scroll_content)
        self._chapters_layout.setContentsMargins(24, 16, 24, 16)
        self._chapters_layout.setSpacing(8)
        self._chapters_layout.setAlignment(Qt.AlignTop)

        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area, stretch=1)

        # ── 全文预览 ──
        self._text_preview_label = QLabel("")
        self._text_preview_label.setObjectName("exportInfo")
        self._text_preview_label.setWordWrap(True)
        self._text_preview_label.setFixedHeight(60)
        self._text_preview_label.setStyleSheet("color: #64748b; padding: 8px 24px;")
        layout.addWidget(self._text_preview_label)

    def _load_state(self) -> None:
        if not self._project:
            return
        self._chapters = self._project.chapters
        if self._project.novel_path:
            self._novel_text = self._read_file(self._project.novel_path)
            self._refresh_chapter_list()
            self._update_status()

    def _read_file(self, path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            try:
                with open(path, "r", encoding="gbk") as f:
                    return f.read()
            except Exception as e:
                logger.error("读取文件失败: %s", e)
                return ""
        except Exception as e:
            logger.error("读取文件失败: %s", e)
            return ""

    def _on_import_novel(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择TXT小说文件", "", "文本文件 (*.txt);;所有文件 (*.*)",
        )
        if not path:
            return
        self._novel_text = self._read_file(path)
        if not self._novel_text:
            self._status_label.setText("文件为空或无法读取")
            self._status_label.setStyleSheet("color: #f87171; padding: 8px 24px;")
            return

        # 保存
        if self._project:
            self._project.novel_path = path
            self._project.chapters = []
            self._chapters = []
            self._state_service.save_project(self._project)

        self._auto_split_btn.setEnabled(True)
        self._custom_split_btn.setEnabled(True)

        # 预览前200字
        preview = self._novel_text[:200].replace("\n", " ")
        self._text_preview_label.setText(f"已导入：{Path(path).name} | 预览：{preview}...")

        self._update_status()
        self._refresh_chapter_list()

    def _on_auto_split(self) -> None:
        if not self._novel_text:
            return
        self._chapters = split_chapters_default(self._novel_text)
        self._save_chapters()
        self._refresh_chapter_list()
        self._update_status()

    def _on_custom_split(self) -> None:
        if not self._novel_text:
            return
        dialog = CustomSplitDialog(self.window())
        dialog.pattern_submitted.connect(self._apply_custom_split)
        dialog.exec()

    def _apply_custom_split(self, pattern: str) -> None:
        try:
            self._chapters = split_chapters_custom(self._novel_text, pattern)
            self._save_chapters()
            self._refresh_chapter_list()
            self._update_status()
        except ValueError as e:
            self._status_label.setText(f"分割失败: {e}")
            self._status_label.setStyleSheet("color: #f87171; padding: 8px 24px;")

    def _save_chapters(self) -> None:
        if self._project:
            self._project.chapters = self._chapters
            self._state_service.save_project(self._project)

    def _update_status(self) -> None:
        if self._chapters:
            total = len(self._chapters)
            total_chars = sum(len(c["content"]) for c in self._chapters)
            done_count = sum(1 for c in self._chapters if c.get("rewrite_status") == "done")
            self._status_label.setText(
                f"已分割 {total} 章，共 {total_chars} 字  |  点击卡片查看章节内容"
            )
            self._status_label.setStyleSheet("color: #4ade80; padding: 8px 24px;")
            self._process_btn.setEnabled(True)
            self._export_btn.setEnabled(done_count > 0)
        elif self._novel_text:
            self._status_label.setText(
                f"已导入小说（{len(self._novel_text)}字），请点击分割章节按钮"
            )
            self._status_label.setStyleSheet("color: #4fc3f7; padding: 8px 24px;")
        else:
            self._status_label.setText("请先导入TXT小说文件")
            self._status_label.setStyleSheet("color: #94a3b8; padding: 8px 24px;")

    def _refresh_chapter_list(self) -> None:
        while self._chapters_layout.count():
            item = self._chapters_layout.takeAt(0)
            if item:
                w = item.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
        self._chapter_cards.clear()

        if not self._chapters:
            empty_label = QLabel(
                "暂无章节数据\n请先导入小说，然后点击「默认分割章节」或「自定义分割」"
            )
            empty_label.setObjectName("chapterEmptyLabel")
            empty_label.setAlignment(Qt.AlignCenter)
            self._chapters_layout.addWidget(empty_label)
            return

        for i, ch in enumerate(self._chapters):
            card = self._build_chapter_card(i + 1, ch["title"], ch["content"])
            self._chapter_cards.append(card)
            self._chapters_layout.addWidget(card)

    def _build_chapter_card(self, index: int, title: str, content: str) -> QFrame:
        card = QFrame()
        card.setObjectName("storyboardCard")
        card.setFixedHeight(60)
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(16, 8, 16, 8)
        card_layout.setSpacing(8)

        idx_label = QLabel(f"#{index:02d}")
        idx_label.setObjectName("storyboardIndex")
        idx_label.setFixedWidth(40)
        card_layout.addWidget(idx_label)

        title_label = QLabel(title)
        title_label.setObjectName("storyboardText")
        title_label.setWordWrap(True)
        card_layout.addWidget(title_label, stretch=1)

        chars = len(content)
        info_label = QLabel(f"{chars}字")
        info_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        card_layout.addWidget(info_label)

        # 改写状态标记
        ch = self._chapters[index - 1] if index - 1 < len(self._chapters) else {}
        rewrite_status = ch.get("rewrite_status", "none")
        if rewrite_status == "done":
            status_label = QLabel("✔ 已改写")
            status_label.setStyleSheet("color: #4ade80; font-size: 12px;")
            card_layout.addWidget(status_label)
        elif rewrite_status == "processing":
            status_label = QLabel("↻ 处理中")
            status_label.setStyleSheet("color: #4fc3f7; font-size: 12px;")
            card_layout.addWidget(status_label)

        delete_btn = QPushButton("删除")
        delete_btn.setFixedSize(50, 28)
        delete_btn.setCursor(Qt.PointingHandCursor)
        delete_btn.setStyleSheet(
            "QPushButton { color: #f87171; background: transparent; border: 1px solid #f87171; "
            "border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background: #f87171; color: #fff; }"
        )
        delete_btn.clicked.connect(lambda checked, t=title, c=content: self._on_delete_chapter(t, c))
        card_layout.addWidget(delete_btn)

        arrow_label = QLabel("›")
        arrow_label.setStyleSheet("color: #64748b; font-size: 20px;")
        card_layout.addWidget(arrow_label)

        # 点击查看内容
        card.mousePressEvent = lambda event, t=title, c=content: self._on_view_chapter(t, c)

        return card

    def _on_delete_chapter(self, title: str, content: str) -> None:
        self._chapters = [ch for ch in self._chapters if not (ch["title"] == title and ch["content"] == content)]
        self._save_chapters()
        self._refresh_chapter_list()
        self._update_status()

    def _on_view_chapter(self, title: str, content: str) -> None:
        dialog = ChapterContentDialog(title, content, self.window())
        dialog.exec()

    # ── AI改写处理 ───────────────────────────────────────────

    def _on_start_process(self) -> None:
        if not self._chapters:
            return

        dialog = _StartProcessDialog(len(self._chapters), self.window())
        if dialog.exec() != QDialog.Accepted:
            return

        protagonist = dialog.protagonist
        if not protagonist:
            return

        # 获取AI配置
        if not self._settings_service:
            QMessageBox.warning(self, "提示", "系统配置不可用，请在系统设置中配置文案模型")
            return

        settings = self._settings_service.load()
        ai_config = AIModelConfig(
            model_name=settings.text_model.model_name,
            api_key=settings.text_model.api_key,
            base_url=settings.text_model.base_url,
        )
        if not ai_config.is_configured:
            QMessageBox.warning(self, "提示", "请先在系统配置中设置文案生成模型")
            return

        # 重置所有章节的改写状态
        for ch in self._chapters:
            ch["rewrite_status"] = "none"
            ch.pop("rewritten", None)

        self._process_btn.setEnabled(False)
        self._process_btn.setText("⏳ 处理中...")
        self._status_label.setStyleSheet("color: #4fc3f7; padding: 8px 24px;")

        # 输出文件路径：项目同名目录下的 项目名_改写版.txt
        state_dir = Path(self._state_service.data_dir)
        output_path = str(state_dir / f"{self._project_id}_改写版.txt")

        self._rewrite_worker = NovelRewriteWorker(
            self._chapters, protagonist, ai_config, output_path, self,
        )
        self._rewrite_worker.batch_appended.connect(self._on_batch_appended)
        self._rewrite_worker.progress.connect(self._on_rewrite_progress)
        self._rewrite_worker.finished.connect(self._on_rewrite_finished)
        self._rewrite_worker.error.connect(self._on_rewrite_error)
        self._rewrite_worker.start()

    def _on_batch_appended(self, current: int, total: int, preview: str, chapter_indices: list) -> None:
        self._status_label.setText(f"处理中 {current}/{total}... 已写入: {preview}")
        self._status_label.setStyleSheet("color: #4fc3f7; padding: 8px 24px;")
        # 只标记当前批次的章节
        for idx in chapter_indices:
            if idx < len(self._chapters):
                self._chapters[idx]["rewrite_status"] = "done"
        self._save_chapters()
        self._refresh_chapter_list()

    def _on_rewrite_progress(self, current: int, total: int, message: str) -> None:
        self._status_label.setText(message)
        self._status_label.setStyleSheet("color: #4fc3f7; padding: 8px 24px;")

    def _on_rewrite_finished(self) -> None:
        self._process_btn.setEnabled(True)
        self._process_btn.setText("▶️  开始处理")
        done_count = sum(1 for ch in self._chapters if ch.get("rewrite_status") == "done")
        self._export_btn.setEnabled(done_count > 0)
        self._status_label.setText(f"改写完成！共 {done_count}/{len(self._chapters)} 章已改写")
        self._status_label.setStyleSheet("color: #4ade80; padding: 8px 24px;")

    def _on_rewrite_error(self, error_msg: str) -> None:
        self._process_btn.setEnabled(True)
        self._process_btn.setText("▶️  开始处理")
        self._status_label.setText(f"改写失败: {error_msg}")
        self._status_label.setStyleSheet("color: #f87171; padding: 8px 24px;")
        logger.error("改写失败: %s", error_msg)

    # ── 导出改写结果 ─────────────────────────────────────────

    def _on_export_rewritten(self) -> None:
        """导出改写结果文件"""
        state_dir = Path(self._state_service.data_dir)
        output_path = state_dir / f"{self._project_id}_改写版.txt"
        if not output_path.exists():
            self._status_label.setText("没有已改写的内容可导出，请先点击「开始处理」")
            self._status_label.setStyleSheet("color: #f87171; padding: 8px 24px;")
            return

        default_name = f"{self._project.name}_改写版.txt" if self._project else "改写结果.txt"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "导出改写结果", default_name,
            "文本文件 (*.txt);;所有文件 (*.*)",
        )
        if not save_path:
            return

        try:
            import shutil
            shutil.copy2(str(output_path), save_path)
            self._status_label.setText(f"导出完成 → {save_path}")
            self._status_label.setStyleSheet("color: #4ade80; padding: 8px 24px;")
        except Exception as e:
            self._status_label.setText(f"导出失败: {e}")
            self._status_label.setStyleSheet("color: #f87171; padding: 8px 24px;")
