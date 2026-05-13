"""
分集解说文案生成 Worker

当合并视频超过 10 分钟时，将台词按视频文件（或按时长）分集，
逐集调用 AI 生成解说文案，并通过上集摘要保证跨集叙事连贯性。
"""
import json
import logging

import httpx
from openai import OpenAI
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("clip_synth.narrate_v2")

# 触发分集模式的阈值（秒）
EPISODE_THRESHOLD_SECONDS = 600  # 10 分钟

# 单集目标时长（秒），超过此时长则拆分
EPISODE_TARGET_SECONDS = 540  # 9 分钟


def _fix_json_string_literals(json_str: str) -> str:
    """
    修复 AI 生成的 JSON 中字符串值里的非法字符。
    主要处理：字符串内的裸换行符、回车符、制表符、全角空格。
    """
    result = []
    in_str = False
    escape = False
    for ch in json_str:
        if escape:
            result.append(ch)
            escape = False
            continue
        if ch == "\\" and in_str:
            result.append(ch)
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            result.append(ch)
            continue
        if in_str:
            # 字符串内的控制字符必须转义
            if ch == "\n":
                result.append("\\n")
            elif ch == "\r":
                result.append("\\r")
            elif ch == "\t":
                result.append("\\t")
            elif ch == "\u3000":  # 全角空格
                result.append(" ")
            else:
                result.append(ch)
        else:
            result.append(ch)
    return "".join(result)


def _create_client(api_key: str, base_url: str) -> OpenAI:
    http_client = httpx.Client(
        timeout=httpx.Timeout(connect=30.0, read=None, write=None, pool=None),
    )
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=0,
    )


def _ms_to_sec(ms: int) -> float:
    return ms / 1000.0


def _format_time(ms: int) -> str:
    total_sec = ms / 1000
    hours = int(total_sec // 3600)
    minutes = int((total_sec % 3600) // 60)
    seconds = int(total_sec % 60)
    millis = int(ms % 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def split_utterances_into_episodes(
    utterances: list,
    video_boundaries: list | None = None,
) -> list[list]:
    """
    将台词列表切分为多集。

    优先按 video_boundaries（视频文件边界，单位 ms）切分；
    若未提供，则按 EPISODE_TARGET_SECONDS 时长切分。

    Returns:
        list of episode_utterances，每个元素是该集的台词列表
    """
    if not utterances:
        return []

    if video_boundaries:
        # 按视频文件边界切分
        episodes = []
        current_episode = []
        boundary_idx = 0
        for utt in utterances:
            # 当台词开始时间超过当前边界时，开启新集
            while (
                boundary_idx < len(video_boundaries)
                and utt.get("start_time", 0) >= video_boundaries[boundary_idx]
            ):
                if current_episode:
                    episodes.append(current_episode)
                    current_episode = []
                boundary_idx += 1
            current_episode.append(utt)
        if current_episode:
            episodes.append(current_episode)
        return [ep for ep in episodes if ep]

    # 按时长切分
    episodes = []
    current_episode = []
    episode_start_ms = utterances[0].get("start_time", 0)

    for utt in utterances:
        current_duration = (_ms_to_sec(utt.get("end_time", 0)) -
                            _ms_to_sec(episode_start_ms))
        if current_duration > EPISODE_TARGET_SECONDS and current_episode:
            episodes.append(current_episode)
            current_episode = [utt]
            episode_start_ms = utt.get("start_time", 0)
        else:
            current_episode.append(utt)

    if current_episode:
        episodes.append(current_episode)

    return episodes


def get_total_duration_seconds(utterances: list) -> float:
    """计算台词列表的总时长（秒）"""
    if not utterances:
        return 0.0
    start = utterances[0].get("start_time", 0)
    end = utterances[-1].get("end_time", 0)
    return _ms_to_sec(end - start)


EPISODE_SUMMARY_SYSTEM_PROMPT = """你是一位短剧剧情分析师。请用100字以内总结本集的主要剧情，
包括：主要人物、核心冲突、关键转折。语言简洁，只陈述事实，不加评价。
直接输出摘要文字，不要任何前缀或格式。"""


class EpisodeScriptWorker(QThread):
    """
    分集解说文案生成 Worker。

    Signals:
        episode_started(int, int): 开始生成第 N 集（当前集索引, 总集数）
        episode_chunk(int, str): 第 N 集的流式文本 chunk
        episode_done(int, list): 第 N 集生成完成（集索引, segments 列表）
        episode_error(int, str): 第 N 集生成失败（集索引, 错误信息）
        all_done(list): 全部集生成完成，返回合并后的 segments 列表
        error(str): 全局错误
    """
    episode_started = Signal(int, int)
    episode_chunk = Signal(int, str)
    episode_done = Signal(int, list)
    episode_error = Signal(int, str)
    all_done = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        episodes: list[list],          # 每集的台词列表
        speaker_aliases: dict,
        segmentation_system_prompt: str,
        script_system_prompt: str,
        duration: str,
        perspective: str,
        extra_requirements: str,
        api_key: str,
        base_url: str,
        model_name: str,
        parent=None,
    ):
        super().__init__(parent)
        self._episodes = episodes
        self._speaker_aliases = speaker_aliases
        self._segmentation_system_prompt = segmentation_system_prompt
        self._script_system_prompt = script_system_prompt
        self._duration = duration
        self._perspective = perspective
        self._extra_requirements = extra_requirements
        self._api_key = api_key
        self._base_url = base_url
        self._model_name = model_name
        self._cancelled = False
        self._episode_summaries: list[str] = []  # 每集摘要，用于跨集连贯性

    def cancel(self):
        self._cancelled = True

    def run(self):
        total = len(self._episodes)
        logger.info("EpisodeScriptWorker 启动: %d 集", total)

        all_segments = []

        for ep_idx, ep_utterances in enumerate(self._episodes):
            if self._cancelled:
                return

            self.episode_started.emit(ep_idx, total)
            logger.info("开始生成第 %d/%d 集 (%d 条台词)", ep_idx + 1, total, len(ep_utterances))

            try:
                segments = self._generate_episode(ep_idx, ep_utterances)
            except Exception as e:
                logger.exception("第 %d 集生成失败", ep_idx + 1)
                self.episode_error.emit(ep_idx, str(e))
                return

            if self._cancelled:
                return

            all_segments.extend(segments)
            self.episode_done.emit(ep_idx, segments)

            # 生成本集摘要，供下一集使用
            if ep_idx < total - 1:
                try:
                    summary = self._generate_episode_summary(ep_utterances, segments)
                    self._episode_summaries.append(summary)
                    logger.info("第 %d 集摘要: %s", ep_idx + 1, summary[:80])
                except Exception as e:
                    logger.warning("第 %d 集摘要生成失败，跳过: %s", ep_idx + 1, e)
                    self._episode_summaries.append("")

        logger.info("全部 %d 集生成完成，共 %d 个片段", total, len(all_segments))
        self.all_done.emit(all_segments)

    def _generate_episode(self, ep_idx: int, utterances: list) -> list:
        """生成单集的解说文案，返回 segments 列表"""
        from clip_synth.ui.pages.narrate_v2.script_prompts import (
            build_segmentation_prompt,
            build_script_generation_prompt,
        )

        client = _create_client(self._api_key, self._base_url)

        # --- Step 1: 片段分割 ---
        seg_prompt = build_segmentation_prompt(
            utterances, self._speaker_aliases, self._duration,
        )
        logger.info("第 %d 集: 发送片段分割请求 (prompt=%d 字)", ep_idx + 1, len(seg_prompt))

        seg_response = client.chat.completions.create(
            model=self._model_name,
            messages=[
                {"role": "system", "content": self._segmentation_system_prompt},
                {"role": "user", "content": seg_prompt},
            ],
            temperature=0.3,
            stream=False,
        )
        seg_content = seg_response.choices[0].message.content or ""
        segments_raw = self._parse_segments(seg_content)
        if not segments_raw:
            raise ValueError(f"第 {ep_idx + 1} 集片段分割结果解析失败")
        logger.info("第 %d 集: 分割出 %d 个片段", ep_idx + 1, len(segments_raw))

        if self._cancelled:
            return []

        # --- Step 2: 解说文案生成 ---
        prev_summary = self._episode_summaries[-1] if self._episode_summaries else ""
        script_prompt = build_script_generation_prompt(
            utterances,
            self._speaker_aliases,
            segments_raw,
            self._perspective,
            self._extra_requirements,
            episode_index=ep_idx,
            episode_summary_prev=prev_summary,
        )
        logger.info("第 %d 集: 发送解说文案请求 (prompt=%d 字)", ep_idx + 1, len(script_prompt))

        stream = client.chat.completions.create(
            model=self._model_name,
            messages=[
                {"role": "system", "content": self._script_system_prompt},
                {"role": "user", "content": script_prompt},
            ],
            temperature=0.7,
            stream=True,
        )

        collected = []
        for chunk_data in stream:
            if self._cancelled:
                return []
            if chunk_data.choices and chunk_data.choices[0].delta.content:
                content = chunk_data.choices[0].delta.content
                collected.append(content)
                self.episode_chunk.emit(ep_idx, content)

        full_text = "".join(collected)
        logger.info("第 %d 集: 收到原始响应 %d 字", ep_idx + 1, len(full_text))

        segments = self._parse_script_segments(full_text)
        if not segments:
            logger.error("第 %d 集解说文案解析失败，原始响应前500字: %s", ep_idx + 1, full_text[:500])
            raise ValueError(f"第 {ep_idx + 1} 集解说文案解析失败，AI 返回内容无法识别为有效 JSON")

        logger.info("第 %d 集: 解析出 %d 个解说片段", ep_idx + 1, len(segments))
        return segments

    def _generate_episode_summary(self, utterances: list, segments: list) -> str:
        """生成本集剧情摘要，用于下一集的连贯性 prompt"""
        client = _create_client(self._api_key, self._base_url)

        # 提取本集的解说文案作为摘要素材
        scripts = [seg.get("script", "") for seg in segments if seg.get("script", "").strip()]
        script_text = "".join(scripts)[:1500]  # 限制长度

        summary_prompt = f"以下是本集的解说文案：\n\n{script_text}\n\n请用100字以内总结本集主要剧情。"

        response = client.chat.completions.create(
            model=self._model_name,
            messages=[
                {"role": "system", "content": EPISODE_SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": summary_prompt},
            ],
            temperature=0.3,
            stream=False,
        )
        return (response.choices[0].message.content or "").strip()

    @staticmethod
    def _parse_segments(text: str) -> list:
        """从 AI 响应中提取分割片段列表，兼容 markdown 代码块。"""
        import re
        text = text.strip()

        if "```" in text:
            md_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
            if md_match:
                text = md_match.group(1).strip()

        start = text.find("{")
        if start == -1:
            return []

        depth = 0
        in_str = False
        escape = False
        end = -1
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_str:
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end == -1:
            return []

        json_str = text[start:end + 1]
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            try:
                data = json.loads(_fix_json_string_literals(json_str))
            except json.JSONDecodeError:
                return []

        segments = data.get("segments", [])
        if not isinstance(segments, list):
            return []
        return [s for s in segments if isinstance(s, dict) and "start_time" in s and "end_time" in s]

    @staticmethod
    def _parse_script_segments(text: str) -> list:
        """从 AI 响应中提取 segments 列表，兼容 markdown 代码块和裸 JSON。"""
        import re
        text = text.strip()

        # 1. 剥离 markdown 代码块
        if "```" in text:
            md_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
            if md_match:
                text = md_match.group(1).strip()

        # 2. 找最外层 { ... }（括号计数，避免 script 内容里的花括号干扰）
        start = text.find("{")
        if start == -1:
            return []

        depth = 0
        in_str = False
        escape = False
        end = -1
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_str:
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end == -1:
            return []

        json_str = text[start:end + 1]

        # 3. 直接解析
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning("JSON 直接解析失败: %s，尝试修复", e)
            # 4. 修复：将字符串值内的裸换行符替换为 \n，全角空格替换为普通空格
            #    策略：在双引号字符串内，把 \n \r \t 等控制字符转义
            fixed = _fix_json_string_literals(json_str)
            try:
                data = json.loads(fixed)
            except json.JSONDecodeError as e2:
                logger.warning("JSON 修复后仍解析失败: %s", e2)
                return []

        segments = data.get("segments", [])
        if not isinstance(segments, list):
            return []
        return [seg for seg in segments if isinstance(seg, dict) and "script" in seg]
