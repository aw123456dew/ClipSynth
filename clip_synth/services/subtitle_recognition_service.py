import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


@dataclass
class TencentASRConfig:
    secret_id: str = ""
    secret_key: str = ""
    region: str = "ap-guangzhou"
    engine_model_type: str = "16k_zh_en"
    channel_num: int = 1
    res_text_format: int = 3
    source_type: int = 1
    speaker_diarization: int = 1
    speaker_number: int = 0
    sentence_max_length: int = 0
    convert_num_mode: int = 1
    filter_dirty: int = 0
    filter_punc: int = 0
    filter_modal: int = 0
    polling_interval: float = 3.0
    max_wait_time: float = 600.0


BASIC_ONLY_ENGINES = frozenset({
    "16k_multi_lang", "16k_ja", "16k_ko", "16k_vi", "16k_ms",
    "16k_id", "16k_fil", "16k_th", "16k_pt", "16k_tr", "16k_ar",
    "16k_es", "16k_hi", "16k_fr", "16k_zh_medical", "16k_de",
})


def _sign_tc3(secret_id, secret_key, service, host, action, payload, region):
    algorithm = "TC3-HMAC-SHA256"
    now = datetime.now(timezone.utc)
    timestamp = int(now.timestamp())
    date_str = now.strftime("%Y-%m-%d")

    http_request_method = "POST"
    canonical_uri = "/"
    canonical_querystring = ""
    ct = "application/json; charset=utf-8"
    canonical_headers = f"content-type:{ct}\nhost:{host}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_request = f"{http_request_method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{hashed_payload}"

    credential_scope = f"{date_str}/{service}/tc3_request"
    hashed_canonical = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"{algorithm}\n{timestamp}\n{credential_scope}\n{hashed_canonical}"

    def _sign(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = _sign(("TC3" + secret_key).encode("utf-8"), date_str)
    secret_service = _sign(secret_date, service)
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    return {
        "Authorization": authorization,
        "Content-Type": ct,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": "2019-06-14",
        "X-TC-Region": region,
    }


class TencentASRService:
    HOST = "asr.tencentcloudapi.com"
    SERVICE = "asr"

    def __init__(self, config: TencentASRConfig):
        self._config = config

    def recognize(self, audio_bytes: bytes) -> RecognitionResult:
        task_id = self._create_task(audio_bytes)
        if not task_id:
            return RecognitionResult(
                success=False,
                error_message="提交任务失败",
            )
        return self._poll_result(task_id)

    def _create_task(self, audio_bytes: bytes) -> Optional[int]:
        data_base64 = base64.b64encode(audio_bytes).decode("utf-8")

        res_text_format = self._config.res_text_format
        if self._config.engine_model_type in BASIC_ONLY_ENGINES:
            res_text_format = 0

        body = {
            "EngineModelType": self._config.engine_model_type,
            "ChannelNum": self._config.channel_num,
            "ResTextFormat": res_text_format,
            "SourceType": self._config.source_type,
            "Data": data_base64,
            "DataLen": len(audio_bytes),
            "SpeakerDiarization": self._config.speaker_diarization,
            "SpeakerNumber": self._config.speaker_number,
            "ConvertNumMode": self._config.convert_num_mode,
            "FilterDirty": self._config.filter_dirty,
            "FilterPunc": self._config.filter_punc,
            "FilterModal": self._config.filter_modal,
        }

        payload = json.dumps(body)
        headers = _sign_tc3(
            self._config.secret_id,
            self._config.secret_key,
            self.SERVICE,
            self.HOST,
            "CreateRecTask",
            payload,
            self._config.region,
        )

        try:
            logger.info("腾讯ASR提交任务: engine=%s, data_len=%d", self._config.engine_model_type, len(audio_bytes))
            resp = requests.post(
                f"https://{self.HOST}/",
                data=payload,
                headers=headers,
                timeout=30,
            )
            logger.info("腾讯ASR提交响应: status=%s, body=%s", resp.status_code, resp.text[:2000])
            resp_data = resp.json()
            response = resp_data.get("Response", {})
            error = response.get("Error")
            if error:
                logger.error("腾讯ASR提交失败: %s", error)
                return None
            task_id = response.get("Data", {}).get("TaskId")
            if task_id:
                logger.info("腾讯ASR任务提交成功: task_id=%s", task_id)
                return task_id
            logger.error("腾讯ASR提交失败: 未获取到TaskId, 完整响应=%s", resp.text[:2000])
            return None
        except requests.RequestException as e:
            logger.error("腾讯ASR提交请求异常: %s", str(e))
            return None
        except (ValueError, KeyError) as e:
            logger.error("腾讯ASR提交响应解析失败: %s", str(e))
            return None

    def _query_task(self, task_id: int) -> dict:
        payload = json.dumps({"TaskId": task_id})
        headers = _sign_tc3(
            self._config.secret_id,
            self._config.secret_key,
            self.SERVICE,
            self.HOST,
            "DescribeTaskStatus",
            payload,
            self._config.region,
        )
        try:
            resp = requests.post(
                f"https://{self.HOST}/",
                data=payload,
                headers=headers,
                timeout=30,
            )
            return resp.json()
        except requests.RequestException as e:
            logger.error("腾讯ASR查询请求异常: %s", str(e))
            return {}
        except ValueError as e:
            logger.error("腾讯ASR查询响应解析失败: %s", str(e))
            return {}

    def _poll_result(self, task_id: int) -> RecognitionResult:
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed > self._config.max_wait_time:
                logger.error("腾讯ASR轮询超时: task_id=%s, elapsed=%.1fs", task_id, elapsed)
                return RecognitionResult(
                    success=False,
                    task_id=str(task_id),
                    error_message="识别超时",
                )

            time.sleep(self._config.polling_interval)
            resp_data = self._query_task(task_id)
            response = resp_data.get("Response", {})
            error = response.get("Error")
            if error:
                logger.error("腾讯ASR查询返回错误: %s", error)
                return RecognitionResult(
                    success=False,
                    task_id=str(task_id),
                    error_message=error.get("Message", "查询失败"),
                )

            data = response.get("Data", {})
            status = data.get("Status")
            status_str = data.get("StatusStr", "")

            logger.debug("腾讯ASR查询: task_id=%s, status=%s, status_str=%s", task_id, status, status_str)

            if status == 0:  # waiting
                continue
            elif status == 1:  # doing
                continue
            elif status == 2:  # success
                return self._parse_result(data, task_id)
            elif status == 3:  # failed
                return RecognitionResult(
                    success=False,
                    task_id=str(task_id),
                    error_message=data.get("ErrorMsg", "识别失败"),
                )

    def _parse_result(self, data: dict, task_id: int) -> RecognitionResult:
        result_detail = data.get("ResultDetail", []) or []
        full_text = data.get("Result", "")

        utterances = []
        for detail in result_detail:
            words_raw = detail.get("Words", []) or []
            words = []
            for w in words_raw:
                words.append(WordInfo(
                    text=w.get("Word", ""),
                    start_time=w.get("OffsetStartMs", 0),
                    end_time=w.get("OffsetEndMs", 0),
                ))

            speaker_id = detail.get("SpeakerId", 0)
            speaker = f"说话人{speaker_id}" if speaker_id > 0 else ""

            utterances.append(Utterance(
                text=detail.get("FinalSentence", ""),
                start_time=detail.get("StartMs", 0),
                end_time=detail.get("EndMs", 0),
                speaker=speaker,
                words=words,
            ))

        if not utterances and full_text:
            utterances.append(Utterance(
                text=full_text.strip(),
                start_time=0,
                end_time=int(data.get("AudioDuration", 0) * 1000),
                speaker="",
                words=[],
            ))

        logger.info("腾讯ASR识别完成: task_id=%s, 文本长度=%d, 分句数=%d",
                    task_id, len(full_text), len(utterances))

        return RecognitionResult(
            success=True,
            text=full_text,
            utterances=utterances,
            task_id=str(task_id),
        )


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
