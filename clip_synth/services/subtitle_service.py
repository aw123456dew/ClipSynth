import json
import logging
import os
from typing import Dict, List, Optional

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
    def merge_words_to_sentences(words: List[dict], max_chars: int = 20, max_pause: float = 0.3) -> List[dict]:
        """
        将单个字合并为句子，根据标点符号断句并去除标点
        :param words: [{"word": "字", "start": 0.0, "end": 0.1}, ...]
        :param max_chars: 单行字幕最大字数
        :param max_pause: 超过此间隔(秒)视为句子断开
        :return: [{"text": "句子", "start": 0.0, "end": 0.5}, ...]
        """
        if not words:
            return []

        sentences = []
        current_chars = []
        current_start = None
        current_end = None

        for word in words:
            char = word.get("word", "")
            start = word.get("start", 0.0)
            end = word.get("end", 0.0)

            if not char:
                continue

            # 标点符号作为断句点，不加入文本
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
                continue

            # 检查是否需要断开
            is_long_pause = current_end is not None and (start - current_end) > max_pause
            current_text_length = len(current_chars)
            is_too_long = current_text_length > 0 and (current_text_length + len(char) > max_chars)

            if (is_long_pause or is_too_long) and current_chars:
                sentences.append({
                    'text': ''.join(current_chars),
                    'start': current_start,
                    'end': current_end
                })
                current_chars = []
                current_start = start
                current_end = end
            else:
                if current_start is None:
                    current_start = start
                current_end = end

            current_chars.append(char)

        if current_chars:
            sentences.append({
                'text': ''.join(current_chars),
                'start': current_start,
                'end': current_end
            })

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