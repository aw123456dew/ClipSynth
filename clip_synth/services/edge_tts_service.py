"""Edge-TTS 语音合成服务（免费，无需 API Key）"""

import logging
import os
import subprocess
from pathlib import Path

import edge_tts

logger = logging.getLogger("clip_synth.edge_tts")


def _split_text_by_length(text: str, max_chars: int = 2000) -> list[str]:
    """按最大字符数分割文本"""
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    current = ""
    for char in text:
        current += char
        if len(current) >= max_chars and char in "。！？.!?\n":
            parts.append(current)
            current = ""
    if current:
        parts.append(current)
    return parts


def _parse_sentence_boundary(msg: dict) -> dict | None:
    """从 SentenceBoundary 消息中提取句子级时间戳"""
    try:
        offset = msg.get("offset", 0)
        duration = msg.get("duration", 0)
        text = msg.get("text", "")
        if not text or not text.strip():
            return None
        clean = text.strip()
        if not any(c.isalpha() for c in clean):
            return None
        start = offset / 10_000_000
        end = (offset + duration) / 10_000_000
        return {"word": clean, "start": round(start, 3), "end": round(end, 3)}
    except Exception:
        pass
    return None


def _param_to_edge(value: float, param_type: str) -> str:
    """将 UI 滑块值转换为 edge-tts 参数字符串"""
    if param_type == "rate":
        pct = int((value - 1.0) * 100)
        return f"{pct:+d}%"
    elif param_type == "pitch":
        hz = int((value - 1.0) * 100)
        return f"{hz:+d}Hz"
    elif param_type == "volume":
        pct = int((value - 1.0) * 100)
        return f"{pct:+d}%"
    return "+0%"


def _create_silence_audio(duration: float, output_path: str) -> None:
    """用 ffmpeg 生成一段纯静音音频"""
    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", str(duration),
            "-c:a", "libmp3lame", "-b:a", "128k",
            output_path,
        ],
        capture_output=True, text=True, timeout=30, **kwargs,
    )


def _run_ffmpeg(args: list[str], timeout: int = 120) -> bool:
    """通用 ffmpeg 调用"""
    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, **kwargs,
        )
        if result.returncode != 0:
            logger.error("ffmpeg 失败: %s", result.stderr)
            return False
        return True
    except Exception as e:
        logger.error("ffmpeg 异常: %s", e)
        return False


def apply_semitone_shift(input_path: str, output_path: str, semitones: int) -> bool:
    """用 rubberband 对音频做半音移调，不改变语速。semitones=0 时只做 rename。"""
    if semitones == 0:
        os.rename(input_path, output_path)
        return True
    ratio = 2 ** (semitones / 12)
    return _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", input_path,
        "-af", f"rubberband=pitch={ratio:.6f}",
        "-c:a", "libmp3lame", "-b:a", "128k",
        output_path,
    ])


class EdgeTTSService:
    """Edge-TTS 语音合成服务"""

    @property
    def is_configured(self) -> bool:
        return True

    @staticmethod
    def list_voices() -> list[dict]:
        """动态获取全部配音员列表"""
        import asyncio
        voices = asyncio.run(edge_tts.list_voices())
        result = []
        for v in voices:
            result.append({
                "id": v["ShortName"],
                "name": v["FriendlyName"],
                "locale": v["Locale"],
                "gender": v.get("Gender", ""),
            })
        return result

    def synthesize(
        self,
        text: str,
        voice: str,
        output_path: str,
        rate: float = 1.0,
        pitch: float = 1.0,
        volume: float = 1.0,
        silence_duration: float = 0.2,
        semitone: int = 0,
    ) -> tuple[bool, str, list[dict]]:
        """
        单段合成输出到文件。
        纯文本合成 → SentenceBoundary时间戳 → ffmpeg在句尾插入静音 → 调整时间戳。

        Returns:
            (success, error_msg, timestamps)
        """
        try:
            rate_str = _param_to_edge(rate, "rate")
            pitch_str = _param_to_edge(pitch, "pitch")
            volume_str = _param_to_edge(volume, "volume")

            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

            # Step 1: 纯文本合成，获取句子级时间戳
            communicate = edge_tts.Communicate(
                text, voice,
                rate=rate_str,
                pitch=pitch_str,
                volume=volume_str,
                boundary="SentenceBoundary",
            )

            raw_sentences: list[dict] = []
            temp_raw = output_path + ".raw.mp3"
            with open(temp_raw, "wb") as f:
                for chunk in communicate.stream_sync():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "SentenceBoundary":
                        ts = _parse_sentence_boundary(chunk)
                        if ts:
                            raw_sentences.append(ts)

            if not os.path.getsize(temp_raw):
                return False, "生成的音频为空", []

            # Step 2: 在每句末尾插入静音 → 得到 pause_out
            pause_out = output_path + ".pause.mp3"
            if silence_duration > 0.001 and len(raw_sentences) > 1:
                adjusted_sentences = self._insert_sentence_pauses(
                    temp_raw, raw_sentences, pause_out, silence_duration,
                )
                os.unlink(temp_raw)
            else:
                os.rename(temp_raw, pause_out)
                adjusted_sentences = raw_sentences

            # Step 3: 半音移调后处理
            if semitone != 0:
                apply_semitone_shift(pause_out, output_path, semitone)
                os.unlink(pause_out)
            else:
                os.rename(pause_out, output_path)

            return True, "", adjusted_sentences

        except Exception as e:
            logger.error("Edge-TTS 合成失败: %s", e, exc_info=True)
            return False, str(e), []

    @staticmethod
    def _insert_sentence_pauses(
        input_path: str,
        sentences: list[dict],
        output_path: str,
        pause_duration: float,
    ) -> list[dict]:
        """
        在每句末尾插入静音。
        按时间戳切分每句 → 句尾追加静音片段 → concat 合并。
        """
        adjusted: list[dict] = []
        accumulated_pause = 0.0

        for i, sent in enumerate(sentences):
            start = sent["start"] + accumulated_pause
            end = sent["end"] + accumulated_pause
            if i > 0:
                end += pause_duration
                accumulated_pause += pause_duration
            adjusted.append({
                "word": sent["word"],
                "start": round(start, 3),
                "end": round(end, 3),
            })

        temp_dir = Path(output_path).parent / ".edge_pause_temp"
        temp_dir.mkdir(exist_ok=True)
        temp_files: list[str] = []

        try:
            for i, sent in enumerate(sentences):
                seg_start = sent["start"]
                seg_end = sent["end"] if i + 1 < len(sentences) else None
                seg_file = str(temp_dir / f"seg_{i:04d}.mp3")
                seg_cmd = [
                    "ffmpeg", "-y", "-i", input_path,
                    "-ss", f"{seg_start:.3f}",
                ]
                if seg_end is not None:
                    seg_cmd += ["-to", f"{seg_end:.3f}"]
                seg_cmd += ["-c", "copy", seg_file]
                _run_ffmpeg(seg_cmd, timeout=60)
                temp_files.append(seg_file)

                if i < len(sentences) - 1 and pause_duration > 0.001:
                    silence_file = str(temp_dir / f"silence_{i:04d}.mp3")
                    _create_silence_audio(pause_duration, silence_file)
                    temp_files.append(silence_file)

            concat_file = str(temp_dir / "concat.txt")
            with open(concat_file, "w", encoding="utf-8") as f:
                for tf in temp_files:
                    abs_path = Path(tf).resolve().as_posix()
                    f.write(f"file '{abs_path}'\n")

            _run_ffmpeg([
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-c", "copy",
                output_path,
            ], timeout=300)

            logger.info(
                "句尾停顿处理完成: 句子数=%d, 停顿=%.1fs",
                len(sentences), pause_duration,
            )
            return adjusted

        finally:
            for f in temp_files:
                try:
                    os.unlink(f)
                except Exception:
                    pass
            try:
                import shutil
                shutil.rmtree(str(temp_dir), ignore_errors=True)
            except Exception:
                pass

    def synthesize_long(
        self,
        text: str,
        voice: str,
        output_path: str,
        rate: float = 1.0,
        pitch: float = 1.0,
        volume: float = 1.0,
        silence_duration: float = 0.2,
        semitone: int = 0,
    ) -> tuple[bool, list[dict]]:
        """
        长文本处理：分段合成 → 段落间插入静音 → 拼接 → 输出文件。

        Returns:
            (success, timestamps)
        """
        parts = _split_text_by_length(text, max_chars=2000)
        if len(parts) == 1:
            success, _, timestamps = self.synthesize(
                text, voice, output_path,
                rate, pitch, volume, silence_duration, semitone,
            )
            return success, timestamps

        logger.info("Edge-TTS 长文本分段: %d 段", len(parts))

        temp_dir = os.path.join(os.path.dirname(output_path), ".edge_tts_temp")
        os.makedirs(temp_dir, exist_ok=True)

        audio_files: list[str] = []
        all_timestamps: list[dict] = []
        time_offset = 0.0

        try:
            for i, part in enumerate(parts):
                part_path = os.path.join(temp_dir, f"part_{i:04d}.mp3")
                success, _, part_ts = self.synthesize(
                    part, voice, part_path,
                    rate, pitch, volume, silence_duration, semitone,
                )

                if not success:
                    logger.error("长文本第 %d 段合成失败", i + 1)
                    for f in audio_files:
                        try:
                            os.remove(f)
                        except Exception:
                            pass
                    return False, []

                for ts in part_ts:
                    ts["start"] = round(ts["start"] + time_offset, 3)
                    ts["end"] = round(ts["end"] + time_offset, 3)
                all_timestamps.extend(part_ts)

                audio_files.append(part_path)

                if part_ts:
                    time_offset = part_ts[-1]["end"] + silence_duration
                else:
                    from clip_synth.services.narrate_export_service import _get_media_duration
                    time_offset += _get_media_duration(part_path) + silence_duration

                if i < len(parts) - 1 and silence_duration > 0.001:
                    silence_path = os.path.join(temp_dir, f"silence_{i:04d}.mp3")
                    _create_silence_audio(silence_duration, silence_path)
                    if os.path.getsize(silence_path) > 0:
                        audio_files.append(silence_path)

            if not self._merge_audio_files(audio_files, output_path):
                return False, []

            logger.info("Edge-TTS 长文本合成成功: %s", output_path)
            return True, all_timestamps

        finally:
            for f in audio_files:
                try:
                    os.remove(f)
                except Exception:
                    pass
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass

    @staticmethod
    def _merge_audio_files(input_files: list[str], output_path: str) -> bool:
        """用 ffmpeg concat 合并多个音频文件"""
        concat_file = os.path.join(os.path.dirname(output_path), ".edge_concat.txt")
        try:
            with open(concat_file, "w", encoding="utf-8") as f:
                for af in input_files:
                    abs_path = Path(af).resolve().as_posix()
                    f.write(f"file '{abs_path}'\n")

            return _run_ffmpeg(
                [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0",
                    "-i", concat_file,
                    "-c", "copy",
                    output_path,
                ],
                timeout=300,
            )
        except Exception as e:
            logger.error("合并音频异常: %s", e)
            return False
        finally:
            try:
                os.unlink(concat_file)
            except Exception:
                pass
