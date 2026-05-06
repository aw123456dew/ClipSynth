import base64
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

import requests

logger = logging.getLogger("clip_synth.doubao_tts")


def split_text_by_length(text: str, max_bytes: int = 1000) -> List[str]:
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]

    sentences = []
    current_sentence = ""
    sentence_delimiters = ["。", "！", "？", ".", "!", "?", "；", ";", "，", ","]

    for char in text:
        current_sentence += char
        if len(current_sentence.encode("utf-8")) >= max_bytes * 0.8:
            last_delimiter_pos = -1
            for i, c in enumerate(current_sentence):
                if c in sentence_delimiters:
                    last_delimiter_pos = i

            if last_delimiter_pos > 0:
                sentence = current_sentence[: last_delimiter_pos + 1]
                sentences.append(sentence)
                current_sentence = current_sentence[last_delimiter_pos + 1:]
            else:
                sentences.append(current_sentence)
                current_sentence = ""

    if current_sentence:
        sentences.append(current_sentence)

    valid_sentences = []
    for sentence in sentences:
        if len(sentence.encode("utf-8")) > max_bytes:
            chars = list(sentence)
            chunk = ""
            for char in chars:
                if len((chunk + char).encode("utf-8")) > max_bytes:
                    valid_sentences.append(chunk)
                    chunk = char
                else:
                    chunk += char
            if chunk:
                valid_sentences.append(chunk)
        else:
            valid_sentences.append(sentence)

    return valid_sentences


def merge_audio_files(audio_files: List[str], output_file: str) -> bool:
    try:
        if not audio_files:
            logger.error("没有音频文件可合并")
            return False

        if len(audio_files) == 1:
            import shutil
            shutil.copy2(audio_files[0], output_file)
            return True

        try:
            from pydub import AudioSegment

            combined = AudioSegment.empty()
            for i, audio_file in enumerate(audio_files):
                if not os.path.exists(audio_file):
                    logger.error(f"音频文件不存在: {audio_file}")
                    return False
                audio = AudioSegment.from_file(audio_file)
                combined += audio
                if i < len(audio_files) - 1:
                    combined += AudioSegment.silent(duration=100)

            combined.export(output_file, format="mp3")
            logger.info(f"音频文件合并成功: {output_file}")
            return True

        except ImportError:
            logger.warning("pydub未安装，使用简单文件拼接")
            with open(output_file, "wb") as outfile:
                for audio_file in audio_files:
                    with open(audio_file, "rb") as infile:
                        outfile.write(infile.read())
            logger.info(f"音频文件简单合并成功: {output_file}")
            return True

    except Exception as e:
        logger.error(f"合并音频文件失败: {e}")
        return False


class DoubaoTTSWorker:
    def __init__(self, doubao_settings):
        self._settings = doubao_settings

    def tts_single(
        self,
        text: str,
        voice_type: str,
        output_path: str,
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: float = 1.0,
        silence_duration: float = 0.125,
        emotion: str = "",
        language: str = "cn",
    ) -> Tuple[bool, str]:
        success, error_msg, _ = self.tts_single_with_timestamps(
            text, voice_type, output_path, speed, pitch, volume,
            silence_duration, emotion, language
        )
        return success, error_msg

    def tts_single_with_timestamps(
        self,
        text: str,
        voice_type: str,
        output_path: str,
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: float = 1.0,
        silence_duration: float = 0.125,
        emotion: str = "",
        language: str = "cn",
    ) -> Tuple[bool, str, list]:
        if not self._settings.is_configured:
            logger.error("豆包语音 TTS 配置未完成")
            return False, "豆包语音 TTS 配置未完成", []

        appid = self._settings.app_id
        token = self._settings.token
        cluster = "volcano_tts"
        safe_speed = float(max(0.2, min(3.0, speed)))
        text = text.strip()
        reqid = str(uuid.uuid4())

        payload = {
            "app": {
                "appid": appid,
                "token": token,
                "cluster": cluster,
            },
            "user": {
                "uid": "FrameCut",
            },
            "audio": {
                "voice_type": voice_type,
                "encoding": "mp3",
                "rate": 24000,
                "speed_ratio": safe_speed,
                "volume_ratio": float(volume),
                "pitch_ratio": float(pitch),
            },
            "request": {
                "reqid": reqid,
                "text": text,
                "text_type": "plain",
                "operation": "query",
                "with_frontend": 1,
                "frontend_type": "unitTson",
            },
        }

        if silence_duration > 0:
            payload["audio"]["silence_duration"] = float(silence_duration)
        if emotion:
            payload["audio"]["emotion"] = emotion
        if language:
            payload["audio"]["language"] = language

        url = "https://openspeech.bytedance.com/api/v1/tts"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer;{token}",
        }

        for i in range(3):
            try:
                logger.info(f"豆包语音 TTS 请求 (第 {i+1} 次), 文本长度: {len(text)} 字符")
                response = requests.post(url, json=payload, headers=headers, timeout=60)

                if response.status_code == 200:
                    result = response.json()
                    if result.get("code") == 3000:
                        audio_data = result.get("data", "")
                        if audio_data:
                            audio_bytes = base64.b64decode(audio_data)
                            os.makedirs(os.path.dirname(output_path), exist_ok=True)
                            with open(output_path, "wb") as f:
                                f.write(audio_bytes)
                            
                            # 解析时间戳
                            timestamps = []
                            addition = result.get("addition", {})
                            if isinstance(addition, dict):
                                frontend_str = addition.get("frontend", "")
                                if frontend_str:
                                    try:
                                        frontend = json.loads(frontend_str)
                                        words = frontend.get("words", [])
                                        for word in words:
                                            timestamps.append({
                                                "word": word.get("word", ""),
                                                "start": word.get("start_time", 0.0),
                                                "end": word.get("end_time", 0.0)
                                            })
                                    except json.JSONDecodeError as e:
                                        logger.warning(f"解析frontend失败: {e}")
                            
                            logger.info(f"豆包语音 TTS 合成成功: {output_path}, 时间戳数量: {len(timestamps)}")
                            return True, "", timestamps
                        else:
                            logger.error("豆包语音 TTS 响应中无音频数据")
                            return False, "响应中无音频数据", []
                    else:
                        error_msg = result.get("message", "未知错误")
                        logger.error(f"豆包语音 TTS 失败: {error_msg}")
                        if "exceed max len limit" in error_msg.lower():
                            return False, "TEXT_TOO_LONG", []
                        return False, error_msg, []
                else:
                    error_msg = f"API 请求失败: {response.status_code}, {response.text}"
                    logger.error(f"豆包语音 TTS {error_msg}")
                    return False, error_msg, []

            except Exception as e:
                logger.error(f"豆包语音 TTS 错误: {str(e)}")
                if i < 2:
                    time.sleep(3)

        return False, "所有重试均失败", []

    def tts_with_timestamps(
        self,
        text: str,
        voice_type: str,
        output_path: str,
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: float = 1.0,
        silence_duration: float = 0.125,
        emotion: str = "",
        language: str = "cn",
    ) -> tuple:
        """
        生成配音并返回时间戳信息
        :return: (success: bool, timestamps: list)
        """
        text_bytes = len(text.encode("utf-8"))
        if text_bytes <= 1000:
            success, error_msg, timestamps = self.tts_single_with_timestamps(
                text, voice_type, output_path, speed, pitch, volume,
                silence_duration, emotion, language,
            )
            if success:
                return True, timestamps
            if error_msg == "TEXT_TOO_LONG":
                logger.warning("单次调用返回长度限制错误，尝试分割文本")
            else:
                logger.error(f"豆包语音 TTS 失败: {error_msg}")
                return False, []

        logger.info(f"文本过长 ({text_bytes} 字节)，开始分割处理")
        text_parts = split_text_by_length(text, max_bytes=1000)
        
        all_timestamps = []
        offset = 0.0

        temp_dir = os.path.join(os.path.dirname(output_path), "temp_doubaotts")
        os.makedirs(temp_dir, exist_ok=True)

        audio_files = []
        try:
            for i, part in enumerate(text_parts):
                logger.info(f"处理第 {i+1}/{len(text_parts)} 部分")
                temp_audio = os.path.join(temp_dir, f"part_{i+1}.mp3")

                success, error_msg, timestamps = self.tts_single_with_timestamps(
                    part, voice_type, temp_audio, speed, pitch, volume,
                    silence_duration, emotion, language,
                )

                if not success:
                    logger.error(f"第 {i+1} 部分TTS失败: {error_msg}")
                    for f in audio_files:
                        if os.path.exists(f):
                            os.remove(f)
                    return False, []

                # 调整时间戳偏移
                for ts in timestamps:
                    ts["start"] = ts.get("start", 0.0) + offset
                    ts["end"] = ts.get("end", 0.0) + offset
                all_timestamps.extend(timestamps)

                audio_files.append(temp_audio)
                # 计算偏移（音频时长 + 静音间隔）
                import subprocess
                try:
                    kwargs = {}
                    if hasattr(subprocess, "CREATE_NO_WINDOW"):
                        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                    result = subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", temp_audio],
                        capture_output=True, text=True, timeout=30, **kwargs
                    )
                    if result.returncode == 0:
                        offset += float(result.stdout.strip()) + silence_duration
                except Exception as e:
                    logger.warning(f"获取音频时长失败: {e}")
                    offset += 5.0  # 默认5秒

            logger.info(f"合并 {len(audio_files)} 个音频文件")
            if merge_audio_files(audio_files, output_path):
                logger.info(f"音频文件合并成功: {output_path}")
                return True, all_timestamps
            else:
                logger.error("音频文件合并失败")
                return False, []

        finally:
            for f in audio_files:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
            if os.path.exists(temp_dir):
                try:
                    os.rmdir(temp_dir)
                except Exception:
                    pass

    def tts(
        self,
        text: str,
        voice_type: str,
        output_path: str,
        speed: float = 1.0,
        pitch: float = 1.0,
        volume: float = 1.0,
        silence_duration: float = 0.125,
        emotion: str = "",
        language: str = "cn",
    ) -> bool:
        logger.info(f"豆包语音 TTS 开始处理，文本长度: {len(text)} 字符")

        text_bytes = len(text.encode("utf-8"))
        if text_bytes <= 1000:
            success, error_msg = self.tts_single(
                text, voice_type, output_path, speed, pitch, volume,
                silence_duration, emotion, language,
            )
            if success:
                return True
            if error_msg == "TEXT_TOO_LONG":
                logger.warning("单次调用返回长度限制错误，尝试分割文本")
            else:
                logger.error(f"豆包语音 TTS 失败: {error_msg}")
                return False

        logger.info(f"文本过长 ({text_bytes} 字节)，开始分割处理")
        text_parts = split_text_by_length(text, max_bytes=1000)
        logger.info(f"文本分割为 {len(text_parts)} 个部分")

        if len(text_parts) == 1:
            success, error_msg = self.tts_single(
                text_parts[0], voice_type, output_path, speed, pitch, volume,
                silence_duration, emotion, language,
            )
            return success

        temp_dir = os.path.join(os.path.dirname(output_path), "temp_doubaotts")
        os.makedirs(temp_dir, exist_ok=True)

        audio_files = []
        try:
            for i, part in enumerate(text_parts):
                logger.info(f"处理第 {i+1}/{len(text_parts)} 部分")
                temp_audio = os.path.join(temp_dir, f"part_{i+1}.mp3")

                success, error_msg = self.tts_single(
                    part, voice_type, temp_audio, speed, pitch, volume,
                    silence_duration, emotion, language,
                )

                if not success:
                    logger.error(f"第 {i+1} 部分TTS失败: {error_msg}")
                    for f in audio_files:
                        if os.path.exists(f):
                            os.remove(f)
                    return False

                audio_files.append(temp_audio)

            logger.info(f"合并 {len(audio_files)} 个音频文件")
            if merge_audio_files(audio_files, output_path):
                logger.info(f"音频文件合并成功: {output_path}")
                return True
            else:
                logger.error("音频文件合并失败")
                return False

        finally:
            for f in audio_files:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
            if os.path.exists(temp_dir):
                try:
                    os.rmdir(temp_dir)
                except Exception:
                    pass
