import json
import logging
import os
import re
from typing import List, Optional

logger = logging.getLogger("clip_synth.subtitle_service")


class SubtitleService:
    @staticmethod
    def _is_punctuation(char: str) -> bool:
        """判断是否为标点符号"""
        if not char:
            return False
        punct_set = set("，。！？、；：""''（）【】《》——…·～〝〟,.!?;:()[]{}""''<>")
        return char in punct_set

    @staticmethod
    def _is_latin_word(word: str) -> bool:
        """判断是否为拉丁字母组成的单词（英文等）"""
        if not word:
            return False
        if re.match(r'^[a-zA-Z]+$', word):
            return True
        if re.match(r"^[a-zA-Z]+['-][a-zA-Z]+$", word):
            return True
        return False

    @staticmethod
    def _normalize_word(word: str) -> str:
        """标准化单词用于比较：转小写、去两端标点"""
        return word.strip('.,!?;:()"\'，。！？；：""''（）【】《》…— ').lower()

    @staticmethod
    def _infer_granularity(words: List[dict]) -> str:
        """推断TTS返回的粒度: 'char'（单字）或 'word'（单词）"""
        if not words:
            return "word"
        char_count = sum(1 for w in words if len(w.get("word", "")) == 1)
        return "char" if char_count > len(words) * 0.5 else "word"

    @staticmethod
    def _parse_reference(reference_text: str, granularity: str) -> List[dict]:
        """将解说文案解析为条目序列"""
        punct_set = set("，。！？、；：""''（）【】《》——…·～〝〟,.!?;:()[]{}""''<>")
        entries = []
        if granularity == "word":
            for token in reference_text.split():
                # 拆分连字符连接的单词，如 "cause-and-effect" → ["cause", "and", "effect"]
                parts = token.split('-')
                for i, part in enumerate(parts):
                    clean = part.strip('.,!?;:()"\'，。！？；：""''（）【】《》…— ')
                    if not clean:
                        continue
                    should_break = False
                    if i == len(parts) - 1:
                        trailing = part[len(clean):] if len(part) > len(clean) else ""
                        should_break = bool(trailing)
                    entries.append({
                        "clean": clean,
                        "should_break": should_break,
                    })
        else:
            for ch in reference_text:
                if ch in punct_set or ch == " ":
                    if entries:
                        entries[-1]["should_break"] = True
                else:
                    entries.append({
                        "clean": ch,
                        "should_break": False,
                    })
        return entries

    @staticmethod
    def merge_words_to_sentences(words: List[dict], max_pause: float = 0.3, reference_text: str = "") -> List[dict]:
        """
        将TTS返回的字序列合并为字幕句子。
        当提供解说文案时：
          - 以文案为唯一标准，对比TTS字符是否在文案中出现
          - 只有匹配文案的字符才保留，多余字符（如sp、sil等）自动丢弃
          - 按文案中的标点符号断句
        无解说文案时退化为原有行为（按max_pause断句）。
        """
        if not words:
            return []

        granularity = SubtitleService._infer_granularity(words) if reference_text else None
        ref_entries = SubtitleService._parse_reference(reference_text, granularity) if reference_text and granularity else None

        logger.debug("merge_words_to_sentences | granularity=%s | ref_entries=%d | reference_text=%s | tts_words=%s",
                     granularity, len(ref_entries) if ref_entries else 0,
                     reference_text[:100] if reference_text else "",
                     [w.get("word", "") for w in words[:20]])

        special_markers = {"sp", "spn", "sil", "silb", "sile", "silence", "pause", "pau", "breath", "#", "<sil>", "[sil]"}

        # CJK/泰语专用路径：不逐字匹配文案，而是按文案空格比例在TTS序列中断句
        if ref_entries is not None and any(ord(ch) > 0x0E00 for ch in reference_text):
            paragraphs = [p for p in reference_text.split() if p.strip()]
            if len(paragraphs) > 1:
                ref_char_total = sum(len(p) for p in paragraphs)
                break_char_indices = []
                char_accum = 0
                for p in paragraphs[:-1]:
                    char_accum += len(p)
                    break_char_indices.append(char_accum)

                valid_words = [(i, w) for i, w in enumerate(words) if w.get("word", "") and w["word"].lower() not in special_markers]
                total_tts = len(valid_words)

                if total_tts > 0:
                    sentences = []
                    start_idx = 0
                    for break_char_idx in break_char_indices:
                        tts_break = round(break_char_idx / ref_char_total * total_tts)
                        if tts_break <= start_idx:
                            tts_break = start_idx + 1
                        if tts_break > total_tts:
                            tts_break = total_tts
                        segment = valid_words[start_idx:tts_break]
                        if segment:
                            sentences.append({
                                "text": "".join(w[1]["word"] for w in segment),
                                "start": segment[0][1]["start"],
                                "end": segment[-1][1]["end"],
                            })
                        start_idx = tts_break
                    if start_idx < total_tts:
                        segment = valid_words[start_idx:]
                        sentences.append({
                            "text": "".join(w[1]["word"] for w in segment),
                            "start": segment[0][1]["start"],
                            "end": segment[-1][1]["end"],
                        })

                    logger.debug("merge_words_to_sentences | CJK/Thai按空格断句: %d段 -> %d条字幕", len(paragraphs), len(sentences))
                    return sentences

        ref_idx = 0
        max_lookahead = 3

        sentences = []
        current_chars = []
        current_start = None
        current_end = None
        prev_was_latin = False
        dropped_count = 0

        for word in words:
            char = word.get("word", "")
            start = word.get("start", 0.0)
            end = word.get("end", 0.0)

            if not char:
                continue

            should_break = False
            word_matched = False

            if ref_entries is not None:
                tts_norm = SubtitleService._normalize_word(char)
                for offset in range(max_lookahead + 1):
                    idx = ref_idx + offset
                    if idx >= len(ref_entries):
                        break
                    entry = ref_entries[idx]
                    ref_norm = entry["clean"].lower()
                    if tts_norm == ref_norm:
                        should_break = entry["should_break"]
                        ref_idx = idx + 1
                        word_matched = True
                        break
                    # 处理撇号缩写词：TTS 的 "Ryan" 匹配文案的 "Ryan's"
                    if tts_norm + "'" == ref_norm[:len(tts_norm) + 1]:
                        should_break = entry["should_break"]
                        ref_idx = idx + 1
                        word_matched = True
                        break
                if not word_matched:
                    dropped_count += 1
                    if dropped_count <= 5:
                        logger.debug("merge_words_to_sentences | 丢弃字符=%s tts_norm=%s ref_idx=%d", char, tts_norm, ref_idx)
                    continue
            else:
                if char == " ":
                    if current_chars:
                        sentences.append({
                            'text': ''.join(current_chars),
                            'start': current_start,
                            'end': current_end or end,
                        })
                        current_chars = []
                        current_start = None
                        current_end = None
                        prev_was_latin = False
                    continue
                if SubtitleService._is_punctuation(char):
                    if current_chars:
                        sentences.append({
                            'text': ''.join(current_chars),
                            'start': current_start,
                            'end': current_end or end,
                        })
                        current_chars = []
                        current_start = None
                        current_end = None
                        prev_was_latin = False
                    continue
                if char.lower() in {"sp", "spn", "sil", "silb", "sile", "silence", "pause", "pau", "breath", "#", "<sil>", "[sil]"}:
                    continue

            is_latin = SubtitleService._is_latin_word(char)
            is_long_pause = current_end is not None and (start - current_end) > max_pause

            if is_long_pause and current_chars:
                sentences.append({
                    'text': ''.join(current_chars),
                    'start': current_start,
                    'end': current_end
                })
                current_chars = []
                current_start = start
                current_end = end
                prev_was_latin = False

            if current_start is None:
                current_start = start
            current_end = end

            if current_chars and prev_was_latin and is_latin:
                current_chars.append(' ')
            current_chars.append(char)
            prev_was_latin = is_latin

            if should_break:
                sentences.append({
                    'text': ''.join(current_chars),
                    'start': current_start,
                    'end': end
                })
                current_chars = []
                current_start = None
                current_end = None
                prev_was_latin = False

        if current_chars:
            sentences.append({
                'text': ''.join(current_chars),
                'start': current_start,
                'end': current_end
            })

        logger.debug("merge_words_to_sentences | 结果: %d条字幕, 丢弃%d字符, sentences=%s",
                     len(sentences), dropped_count, [s["text"][:30] for s in sentences])

        return sentences

    @staticmethod
    def generate_srt(sentences: List[dict], clip_start_time: float = 0.0) -> str:
        """
        生成SRT字幕内容
        :param sentences: [{"text": "句子", "start": 0.0, "end": 0.5}, ...]
        :param clip_start_time: 片段在视频中的开始时间(秒)
        :return: SRT格式字符串
        """
        srt_lines = []
        for i, sent in enumerate(sentences, 1):
            video_start = clip_start_time + (sent.get("start", 0.0) or 0.0)
            video_end = clip_start_time + (sent.get("end", 0.0) or 0.0)

            srt_lines.append(f"{i}")
            srt_lines.append(f"{SubtitleService._to_srt_time(video_start)} --> {SubtitleService._to_srt_time(video_end)}")
            srt_lines.append(sent.get("text", ""))
            srt_lines.append("")

        return '\n'.join(srt_lines)

    @staticmethod
    def _to_srt_time(seconds: float) -> str:
        """将秒转换为SRT时间格式 HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @staticmethod
    def _from_srt_time(srt_time: str) -> float:
        """将SRT时间格式转换为秒"""
        # 格式: HH:MM:SS,mmm
        try:
            parts = srt_time.strip().split(':')
            if len(parts) == 3:
                hours = int(parts[0])
                minutes = int(parts[1])
                sec_part = parts[2].split(',')
                secs = int(sec_part[0])
                millis = int(sec_part[1]) if len(sec_part) > 1 else 0
                return hours * 3600 + minutes * 60 + secs + millis / 1000
        except Exception as e:
            logger.error(f"解析SRT时间失败: {srt_time}, 错误: {e}")
        return 0.0

    @staticmethod
    def parse_srt(srt_content: str) -> List[dict]:
        """
        解析SRT字幕内容
        :param srt_content: SRT格式字符串
        :return: [{"text": "句子", "start": 0.0, "end": 0.5}, ...]
        """
        sentences = []
        lines = srt_content.strip().split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            
            # 尝试解析序号
            if line.isdigit():
                try:
                    # 序号行
                    i += 1
                    if i >= len(lines):
                        break
                    
                    # 时间行
                    time_line = lines[i].strip()
                    if '--> ' in time_line:
                        start_str, end_str = time_line.split('--> ')
                        start_time = SubtitleService._from_srt_time(start_str)
                        end_time = SubtitleService._from_srt_time(end_str)
                        i += 1
                        
                        # 字幕文本（可能跨多行）
                        text_lines = []
                        while i < len(lines):
                            text_line = lines[i].strip()
                            if text_line.isdigit() or (i + 1 < len(lines) and '-->' in lines[i + 1]):
                                break
                            if text_line:
                                text_lines.append(text_line)
                            i += 1
                        
                        text = '\n'.join(text_lines)
                        if text:
                            sentences.append({
                                "text": text,
                                "start": start_time,
                                "end": end_time,
                            })
                    else:
                        i += 1
                except Exception as e:
                    logger.error(f"解析SRT失败: {e}")
                    i += 1
            else:
                i += 1
        
        logger.debug(f"parse_srt | 解析到 {len(sentences)} 条字幕")
        return sentences

    @staticmethod
    def save_srt(content: str, file_path: str) -> bool:
        """保存SRT文件"""
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"SRT字幕文件保存成功: {file_path}")
            return True
        except Exception as e:
            logger.error(f"保存SRT文件失败: {e}")
            return False

    @staticmethod
    def generate_ass(
        sentences: List[dict],
        clip_start_time: float = 0.0,
        font: str = "Microsoft YaHei",
        font_size: int = 24,
        font_color: str = "#FFFFFF",
        bg_color: str = "#000000",
        bg_opacity: int = 50,
        position: str = "bottom",
        video_width: int = 1920,
        video_height: int = 1080,
    ) -> str:
        """
        生成ASS字幕内容，使用\\pos标签在每个Dialogue行中精确定位
        :param sentences: [{"text": "句子", "start": 0.0, "end": 0.5}, ...]
        :param clip_start_time: 片段在视频中的开始时间(秒)
        :param font: 字体
        :param font_size: 字体大小
        :param font_color: 字体颜色
        :param bg_color: 背景颜色
        :param bg_opacity: 背景透明度(0-100)
        :param position: 位置 (top, middle, bottom, custom:x:y)
        :param video_width: 视频宽度
        :param video_height: 视频高度
        :return: ASS格式字符串
        """
        def rgb_to_bgr(rgb: str) -> str:
            rgb = rgb.lstrip("#")
            if len(rgb) == 6:
                return f"{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"
            return rgb

        def to_ass_time(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds % 1) * 100)
            return f"{h}:{m:02d}:{s:02d}.{ms:02d}"

        def get_center_pos(pos_str: str, vw: int, vh: int) -> tuple:
            """返回 (center_x, center_y) 像素坐标"""
            if pos_str == "top":
                return (vw // 2, int(vh * 0.1))
            elif pos_str == "middle":
                return (vw // 2, vh // 2)
            elif pos_str == "bottom":
                return (vw // 2, int(vh * 0.9))
            elif pos_str.startswith("custom:"):
                parts = pos_str.split(":")
                if len(parts) >= 3:
                    x_ratio = float(parts[1])
                    y_ratio = float(parts[2])
                    return (int(vw * x_ratio), int(vh * y_ratio))
                return (vw // 2, int(vh * 0.9))
            else:
                return (vw // 2, int(vh * 0.9))

        color_bgr = rgb_to_bgr(font_color)
        bg_bgr = rgb_to_bgr(bg_color)
        font_alpha = "FF"
        bg_alpha_hex = f"{255 - int((bg_opacity / 100.0) * 255):02X}"

        center_x, center_y = get_center_pos(position, video_width, video_height)

        ass_lines = [
            "[Script Info]",
            "Title: Generated Subtitle",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,{font},{font_size},&H{font_alpha}{color_bgr},&H{font_alpha}{color_bgr},&H00{bg_bgr},&H{bg_alpha_hex}{bg_bgr},0,0,0,0,100,100,0,0,3,1,0,5,0,0,0,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        for sent in sentences:
            video_start = clip_start_time + (sent.get("start", 0.0) or 0.0)
            video_end = clip_start_time + (sent.get("end", 0.0) or 0.0)
            text = sent.get("text", "").replace("\n", " ")
            start_str = to_ass_time(video_start)
            end_str = to_ass_time(video_end)
            ass_lines.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{{\\pos({center_x},{center_y})}}{text}")

        return "\n".join(ass_lines)

    @staticmethod
    def save_ass(content: str, file_path: str) -> bool:
        """保存ASS文件"""
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"ASS字幕文件保存成功: {file_path}")
            return True
        except Exception as e:
            logger.error(f"保存ASS文件失败: {e}")
            return False

    @staticmethod
    def _get_font_file_path(font_name: str) -> str:
        """将字体名解析为 Windows 字体文件路径"""
        font_map = {
            "Microsoft YaHei": "msyh.ttc",
            "SimHei": "simhei.ttf",
            "SimSun": "simsun.ttc",
            "KaiTi": "simkai.ttf",
            "Arial": "arial.ttf",
            "DengXian": "dengxian.ttf",
            "FangSong": "simfang.ttf",
        }
        win_dir = os.environ.get("WINDIR", "C:\\Windows")
        if font_name in font_map:
            candidate = os.path.join(win_dir, "Fonts", font_map[font_name])
            if os.path.exists(candidate):
                return candidate
        import winreg
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
            for i in range(winreg.QueryInfoKey(key)[1]):
                name, value, _ = winreg.EnumValue(key, i)
                if font_name.lower() in name.lower():
                    candidate = os.path.join(win_dir, "Fonts", value)
                    if os.path.exists(candidate):
                        return candidate
        except Exception:
            pass
        return os.path.join(win_dir, "Fonts", "msyh.ttc")

    @staticmethod
    def _hex_to_drawtext_color(hex_color: str, alpha: float = 1.0) -> str:
        """将 #RRGGBB 转为 drawtext 可识别的颜色格式"""
        hex_color = hex_color.lstrip("#")
        if len(hex_color) != 6:
            return "White"
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        return f"0x{r:02X}{g:02X}{b:02X}@{alpha:.2f}"

    @staticmethod
    def _get_center_pos(position: str, vw: int, vh: int) -> tuple:
        """返回 (center_x, center_y) 像素坐标"""
        if position == "top":
            return (vw // 2, int(vh * 0.1))
        elif position == "middle":
            return (vw // 2, vh // 2)
        elif position == "bottom":
            return (vw // 2, int(vh * 0.9))
        elif position.startswith("custom:"):
            parts = position.split(":")
            if len(parts) >= 3:
                x_ratio = float(parts[1])
                y_ratio = float(parts[2])
                return (int(vw * x_ratio), int(vh * y_ratio))
            return (vw // 2, int(vh * 0.9))
        else:
            return (vw // 2, int(vh * 0.9))

    @staticmethod
    def build_drawtext_filter(
        sentences: List[dict],
        clip_start_time: float = 0.0,
        font: str = "Microsoft YaHei",
        font_size: int = 24,
        font_color: str = "#FFFFFF",
        bg_color: str = "#000000",
        bg_opacity: int = 50,
        position: str = "bottom",
        video_width: int = 1920,
        video_height: int = 1080,
    ) -> str:
        """
        构建 ffmpeg drawtext 滤镜，像水印文字一样直接叠加字幕
        :param sentences: [{"text": "句子", "start": 0.0, "end": 0.5}, ...]
        :param clip_start_time: 片段在视频中的开始时间(秒)
        :param font: 字体名称
        :param font_size: 字体大小
        :param font_color: 字体颜色 (#RRGGBB)
        :param bg_color: 背景颜色 (#RRGGBB)
        :param bg_opacity: 背景透明度(0-100)
        :param position: 位置 (top, middle, bottom, custom:x:y)
        :param video_width: 视频宽度
        :param video_height: 视频高度
        :return: drawtext 滤镜字符串
        """
        if not sentences:
            return ""

        font_path = SubtitleService._get_font_file_path(font)
        font_path = font_path.replace("\\", "/")
        font_path_escaped = font_path[0] + "\\:" + font_path[2:] if len(font_path) > 2 and font_path[1] == ":" else font_path
        bg_alpha = bg_opacity / 100.0
        font_color_str = SubtitleService._hex_to_drawtext_color(font_color, 1.0)
        bg_color_str = SubtitleService._hex_to_drawtext_color(bg_color, bg_alpha)
        center_x, center_y = SubtitleService._get_center_pos(position, video_width, video_height)

        filter_parts = []
        for sent in sentences:
            text = sent.get("text", "")
            if not text:
                continue
            start = clip_start_time + (sent.get("start", 0.0) or 0.0)
            end = clip_start_time + (sent.get("end", 0.0) or 0.0)

            text_escaped = (
                text
                .replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace(":", "\\:")
                .replace(",", "\\,")
            )

            filter_str = (
                f"drawtext="
                f"text='{text_escaped}'"
                f":fontfile='{font_path_escaped}'"
                f":fontsize={font_size}"
                f":fontcolor={font_color_str}"
                f":box=1"
                f":boxcolor={bg_color_str}"
                f":boxborderw=10"
                f":x=(w-text_w)/2"
                f":y={center_y}-text_h/2"
                f":enable='between(t,{start},{end})'"
            )
            filter_parts.append(filter_str)

        return ",".join(filter_parts)

    @staticmethod
    def parse_frontend(frontend_str: str) -> List[dict]:
        """解析豆包API返回的frontend字符串"""
        try:
            if not frontend_str:
                return []
            frontend = json.loads(frontend_str)
            return frontend.get("words", [])
        except json.JSONDecodeError as e:
            logger.error(f"解析frontend失败: {e}")
            return []

    @staticmethod
    def build_ffmpeg_subtitle_filter(
        subtitle_path: str,
        font: str = "Microsoft YaHei",
        font_size: int = 24,
        font_color: str = "#FFFFFF",
        bg_color: str = "#000000",
        bg_opacity: int = 50,
        position: str = "bottom",
        video_width: int = 1920,
        video_height: int = 1080,
    ) -> str:
        """
        构建ffmpeg字幕滤镜参数
        :param subtitle_path: SRT字幕文件路径
        :param font: 字体
        :param font_size: 字体大小
        :param font_color: 字体颜色
        :param bg_color: 背景颜色
        :param bg_opacity: 背景透明度(0-100)
        :param position: 位置 (top, middle, bottom, custom:x:y)
        :param video_width: 视频宽度
        :param video_height: 视频高度
        :return: 滤镜字符串
        """
        alpha = bg_opacity / 100.0

        # ASS Alignment: 5=中中，配合 \pos(x,y) 精确坐标
        if position == "top":
            center_x = video_width // 2
            center_y = int(video_height * 0.1)
        elif position == "middle":
            center_x = video_width // 2
            center_y = video_height // 2
        elif position == "bottom":
            center_x = video_width // 2
            center_y = int(video_height * 0.9)
        elif position and position.startswith("custom:"):
            parts = position.split(":")
            x_ratio = float(parts[1]) if len(parts) >= 3 else 0.5
            y_ratio = float(parts[2]) if len(parts) >= 3 else 0.9
            center_x = int(video_width * x_ratio)
            center_y = int(video_height * y_ratio)
        else:
            center_x = video_width // 2
            center_y = int(video_height * 0.9)

        def _to_ass_bgr(hex_color: str) -> str:
            rgb = hex_color.lstrip("#")
            if len(rgb) == 6:
                return f"{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"
            return rgb

        color_bgr = _to_ass_bgr(font_color)
        bg_bgr = _to_ass_bgr(bg_color)
        # ASS格式alpha: 00=完全不透明, FF=完全透明
        # 用户opacity=100表示完全不透明，对应ASS alpha=00
        # 用户opacity=0表示完全透明，对应ASS alpha=FF
        bg_alpha = int((1.0 - alpha) * 255)
        bg_alpha_hex = f"{bg_alpha:02X}"

        style_parts = [
            f"FontName={font}",
            f"FontSize={font_size}",
            f"PrimaryColour=&HFF{color_bgr}",
            f"SecondaryColour=&HFF{color_bgr}",
            f"OutlineColour=&H00{bg_bgr}",
            f"BackColour=&H{bg_alpha_hex}{bg_bgr}",
            "BorderStyle=3",
            "Outline=1",
            "Shadow=0",
            "Alignment=5",
            "MarginV=0",
            "MarginL=0",
            "MarginR=0",
        ]
        style_str = ",".join(style_parts)
        style_str = style_str.replace(",", "\\,")

        safe_path = subtitle_path.replace("\\", "/")
        filter_str = f"subtitles={safe_path}:force_style={style_str}"

        return filter_str