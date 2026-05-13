import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger("clip_synth.subtitle_recognition")

SUBMIT_URL = "https://openspeech.bytedance.com/api/v1/vc/submit"
QUERY_URL = "https://openspeech.bytedance.com/api/v1/vc/query"

LANGUAGE_CODE_MAP = {
    "zh": "zh-CN",
    "en": "en-US",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "fr": "fr-FR",
    "es": "es-MX",
    "ru": "ru-RU",
    "yue": "yue",
    "wuu": "wuu",
    "ug": "ug",
}


@dataclass
class RecognitionConfig:
    app_id: str = ""
    token: str = ""
    uid: str = "FrameCut"
    language: str = "zh-CN"
    enable_itn: bool = True
    enable_punc: bool = False
    enable_ddc: bool = True
    with_speaker_info: bool = True
    max_lines: int = 1
    words_per_line: int = 46
    polling_interval: float = 3.0
    max_wait_time: float = 300.0


@dataclass
class WordInfo:
    text: str
    start_time: int
    end_time: int


@dataclass
class Utterance:
    text: str
    start_time: int
    end_time: int
    speaker: str = ""
    words: list[WordInfo] = field(default_factory=list)


@dataclass
class RecognitionResult:
    success: bool
    text: str = ""
    utterances: list[Utterance] = field(default_factory=list)
    task_id: str = ""
    error_code: int = 0
    error_message: str = ""


class SubtitleRecognitionService:
    def __init__(self, config: RecognitionConfig):
        self._config = config

    def recognize(self, audio_bytes: bytes, **overrides) -> RecognitionResult:
        for key, value in overrides.items():
            if hasattr(self._config, key):
                setattr(self._config, key, value)

        task_id = self._submit_task(audio_bytes)
        if not task_id:
            return RecognitionResult(
                success=False,
                error_message="提交任务失败",
            )

        result = self._poll_result(task_id)
        return result

    def _submit_task(self, audio_bytes: bytes) -> Optional[str]:
        params = {
            "appid": self._config.app_id,
            "language": self._config.language,
            "use_itn": "True" if self._config.enable_itn else "False",
            "with_speaker_info": "True" if self._config.with_speaker_info else "False",
            "caption_type": "speech",
            "max_lines": str(self._config.max_lines),
            "words_per_line": str(self._config.words_per_line),
        }

        if self._config.enable_punc:
            params["use_punc"] = "True"

        if self._config.enable_ddc:
            params["use_ddc"] = "True"

        headers = {
            "Content-Type": "audio/wav",
            "Authorization": f"Bearer;{self._config.token}",
        }

        try:
            logger.info("提交音视频字幕生成任务: language=%s", self._config.language)
            resp = requests.post(
                SUBMIT_URL,
                params=params,
                data=audio_bytes,
                headers=headers,
                timeout=None,
            )
            logger.info("VC 提交响应状态码: %s", resp.status_code)
            logger.info("VC 提交响应内容: %s", resp.text[:2000])
            resp_data = resp.json()
            code = resp_data.get("code")
            task_id = resp_data.get("id")

            if code == 0 and task_id:
                logger.info("任务提交成功: task_id=%s", task_id)
                return task_id

            message = resp_data.get("message", "未知错误")
            logger.error("任务提交失败: code=%s, message=%s", code, message)
            return None

        except requests.RequestException as e:
            logger.error("任务提交请求异常: %s", str(e))
            return None
        except (ValueError, KeyError) as e:
            logger.error("任务提交响应解析失败: %s", str(e))
            return None

    def _query_task(self, task_id: str) -> dict:
        params = {
            "appid": self._config.app_id,
            "id": task_id,
        }

        headers = {
            "Authorization": f"Bearer;{self._config.token}",
        }

        try:
            resp = requests.get(
                QUERY_URL,
                params=params,
                headers=headers,
                timeout=None,
            )
            return resp.json()
        except requests.RequestException as e:
            logger.error("查询结果请求异常: %s", str(e))
            return {}
        except ValueError as e:
            logger.error("查询结果响应解析失败: %s", str(e))
            return {}

    def _poll_result(self, task_id: str) -> RecognitionResult:
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed > self._config.max_wait_time:
                logger.error("轮询超时: task_id=%s, elapsed=%.1fs", task_id, elapsed)
                return RecognitionResult(
                    success=False,
                    task_id=task_id,
                    error_message="识别超时",
                )

            time.sleep(self._config.polling_interval)
            resp_data = self._query_task(task_id)

            code = resp_data.get("code")
            message = resp_data.get("message", "")

            logger.debug("查询结果: task_id=%s, code=%s, message=%s", task_id, code, message)

            if code is None:
                continue

            if code == 2000:
                continue

            if code == 0:
                utterances_raw = resp_data.get("utterances", [])
                utterances = self._parse_utterances(utterances_raw)
                text = "".join(u.text for u in utterances)

                logger.info("识别完成: task_id=%s, 文本长度=%d, 分句数=%d",
                            task_id, len(text), len(utterances))

                return RecognitionResult(
                    success=True,
                    text=text,
                    utterances=utterances,
                    task_id=task_id,
                )

            logger.error("识别失败: code=%s, message=%s", code, message)
            return RecognitionResult(
                success=False,
                task_id=task_id,
                error_code=code,
                error_message=message,
            )

    def _parse_utterances(self, utterances_raw: list) -> list[Utterance]:
        utterances = []
        for utt in utterances_raw:
            text = utt.get("text", "")
            start_time = utt.get("start_time", 0)
            end_time = utt.get("end_time", 0)

            attribute = utt.get("attribute", {}) or {}
            speaker = attribute.get("speaker", "")

            words_raw = utt.get("words", []) or []
            words = []
            for w in words_raw:
                w_attribute = w.get("attribute", {}) or {}
                w_speaker = w_attribute.get("speaker", speaker)
                words.append(WordInfo(
                    text=w.get("text", ""),
                    start_time=w.get("start_time", 0),
                    end_time=w.get("end_time", 0),
                ))
                if w_speaker and not speaker:
                    speaker = w_speaker

            utterances.append(Utterance(
                text=text,
                start_time=start_time,
                end_time=end_time,
                speaker=speaker,
                words=words,
            ))

        return utterances
