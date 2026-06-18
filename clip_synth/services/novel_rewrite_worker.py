"""小说改写后台处理线程：批次打包 → AI调用 → 追加写入输出文件"""

import json
import logging
from pathlib import Path

import httpx
from PySide6.QtCore import QThread, Signal

from clip_synth.services.ai_service import AIModelConfig

logger = logging.getLogger("clip_synth.novel_rewrite_worker")

REWRITE_SYSTEM_PROMPT = """你是一个顶级的文学改写助手。请将以下小说内容改写为适合**有声书配音**的「{protagonist}」第一人称视角文本。

## 核心目标
改写后的文本将以**听觉为主**，用户会用AI配音制作有声书/小说推文。**听起来自然流畅**比**看起来好看**更重要。

## 改写规则
1. 全文使用「我」代替「{protagonist}」，保持第一人称一贯性。
2. **精简环境描写**：删除冗余的环境、景物、天气、外貌等视觉描写，只保留推动剧情的必要信息。
3. **删除冗余内心独白**：删去过多的心理活动和内心戏，保留必要的情绪表达即可。
4. **屏幕消息/弹窗/文字通知类内容** → 改写为可朗读的描述，例如：
   - ❌ "苏念：哥你几点回来"（屏幕消息原文读不出来）
   - ✅ "苏念发消息问我几点回来"
5. 非主角的伏笔/暗线 → 用「事后回想」「当时我没察觉」「后来我才知道」等简短句式带过。
6. **以对话和动作为主**：保留原文对话内容，精简过渡性描述，让故事节奏更快。
7. 保持原文的剧情逻辑、情节走向、情感张力不变。
8. **绝对不要添加原本不存在的情节、人物或暗示**，只删不减。
9. **让故事更紧凑**：删减冗余修饰、拖沓过渡、重复描写，但不能丢失任何关键剧情信息。

## 主角已知称呼
{protagonist_aliases}

## 输出格式
你**必须**返回一个合法的JSON对象，格式如下：
```json
{{
  "content": "所有章节连贯的改写内容，连续输出不要分章...",
  "new_aliases": ["二狗子"]
}}
```
- `content`：所有章节的连贯改写内容，连续输出，不要分章节标记
- `new_aliases`：本章中新出现的主角称呼，没有则填空数组[]
- **严禁在JSON之外输出任何其他文字**
"""


def _batch_chapters(chapters: list[dict], max_chars: int = 8000) -> list[list[int]]:
    """将章节按最大字数打包成批次，返回批次中章节的索引列表"""
    batches: list[list[int]] = []
    current_batch: list[int] = []
    current_chars = 0

    for i, ch in enumerate(chapters):
        ch_len = len(ch.get("content", ""))
        if current_chars + ch_len <= max_chars:
            current_batch.append(i)
            current_chars += ch_len
        else:
            if current_batch:
                batches.append(current_batch)
            current_batch = [i]
            current_chars = ch_len

    if current_batch:
        batches.append(current_batch)

    return batches


def _build_batch_content(chapters: list[dict], batch_indices: list[int]) -> str:
    """构建批次内容，连续拼接不分割"""
    parts: list[str] = []
    for idx in batch_indices:
        ch = chapters[idx]
        parts.append(ch["content"])
    return "\n\n".join(parts)


def _parse_rewrite_json(text: str) -> tuple[str, list[str]]:
    """从AI返回的JSON中提取改写内容和新称呼"""
    json_str = text
    if "```json" in text:
        json_str = text.split("```json", 1)[1]
        if "```" in json_str:
            json_str = json_str.split("```", 1)[0]
    elif "```" in text:
        json_str = text.split("```", 1)[1]
        if "```" in json_str:
            json_str = json_str.split("```", 1)[0]

    # strict=False 允许 content 中包含未转义的控制字符
    data = json.loads(json_str.strip(), strict=False)
    content: str = data.get("content", "")
    new_aliases: list[str] = data.get("new_aliases", [])
    return content, new_aliases


def _call_ai(ai_config: AIModelConfig, system_prompt: str, user_content: str) -> str:
    """同步调用AI API，强制JSON输出格式"""
    base_url = ai_config.base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {ai_config.api_key}",
        "Content-Type": "application/json",
    }

    body = {
        "model": ai_config.model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
    }

    # OpenAI兼容模型支持强制JSON输出（Gemini不支持）
    if not ai_config.model_name.lower().startswith("gemini"):
        body["response_format"] = {"type": "json_object"}

    response = httpx.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json=body,
        timeout=httpx.Timeout(300.0),
    )
    response.raise_for_status()
    data = response.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    if not content:
        raise RuntimeError("AI返回内容为空")
    return content


class NovelRewriteWorker(QThread):
    progress = Signal(int, int, str)       # current_batch, total_batches, message
    batch_appended = Signal(int, int, str, list) # current_batch, total, content_preview, chapter_indices
    finished = Signal()
    error = Signal(str)

    def __init__(
        self,
        chapters: list[dict],
        protagonist: str,
        ai_config: AIModelConfig,
        output_path: str,
        parent=None,
    ):
        super().__init__(parent)
        self._chapters = chapters
        self._protagonist = protagonist
        self._ai_config = ai_config
        self._output_path = output_path
        self._aliases: list[str] = [protagonist]
        self._canceled = False

    def cancel(self) -> None:
        self._canceled = True

    def run(self) -> None:
        try:
            batches = _batch_chapters(self._chapters, max_chars=8000)
            total = len(batches)

            # 清空输出文件
            Path(self._output_path).write_text("", encoding="utf-8")

            for batch_idx, batch_indices in enumerate(batches):
                if self._canceled:
                    return

                self.progress.emit(batch_idx + 1, total, f"正在处理批次 {batch_idx + 1}/{total}...")

                batch_content = _build_batch_content(self._chapters, batch_indices)
                alias_hint = "、".join(self._aliases)

                system_prompt = REWRITE_SYSTEM_PROMPT.format(
                    protagonist=self._protagonist,
                    protagonist_aliases=alias_hint,
                )
                user_prompt = f"""【主角】{self._protagonist}
【主角已知称呼】{alias_hint}

【需要改写的章节内容】
{batch_content}"""

                # 重试机制：最多3次
                rewritten_content = ""
                last_error = None
                for attempt in range(3):
                    if self._canceled:
                        return
                    try:
                        result = _call_ai(self._ai_config, system_prompt, user_prompt)
                        rewritten_content, new_aliases = _parse_rewrite_json(result)
                        last_error = None
                        break  # 成功
                    except Exception as e:
                        last_error = e
                        logger.warning(
                            "批次 %d 第 %d 次尝试失败: %s", batch_idx + 1, attempt + 1, e,
                        )
                        if attempt < 2:
                            self.progress.emit(
                                batch_idx + 1, total,
                                f"批次 {batch_idx + 1}/{total} 失败，正在重试 ({attempt + 2}/3)...",
                            )

                if last_error:
                    self.error.emit(
                        f"批次 {batch_idx + 1} 连续3次失败: {last_error}"
                    )
                    return  # 3次都失败，停止所有处理

                # 积累新昵称
                for na in new_aliases:
                    if na and na not in self._aliases:
                        self._aliases.append(na)
                        logger.info("发现主角新称呼: %s", na)

                # 追加写入输出文件
                if rewritten_content:
                    with open(self._output_path, "a", encoding="utf-8") as f:
                        f.write(rewritten_content)
                        f.write("\n\n")

                self.batch_appended.emit(
                    batch_idx + 1, total,
                    rewritten_content[:60] + "..." if rewritten_content else "(空)",
                    batch_indices,
                )
                self.progress.emit(
                    batch_idx + 1, total,
                    f"批次 {batch_idx + 1}/{total} 完成，已识别昵称: {alias_hint}",
                )

            self.progress.emit(total, total, "改写完成")
            self.finished.emit()

        except Exception as e:
            logger.error("改写失败: %s", e, exc_info=True)
            self.error.emit(str(e))
