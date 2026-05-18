import json
import logging
import httpx
from openai import OpenAI

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("clip_synth.narrate_v2")


def _create_client(api_key: str, base_url: str) -> OpenAI:
    http_client = httpx.Client(
        timeout=httpx.Timeout(
            connect=30.0,
            read=None,
            write=None,
            pool=None,
        ),
    )
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=0,
    )


class SegmentationWorker(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        system_prompt: str,
        user_prompt: str,
        api_key: str,
        base_url: str,
        model_name: str,
        parent=None,
    ):
        super().__init__(parent)
        self._system_prompt = system_prompt
        self._user_prompt = user_prompt
        self._api_key = api_key
        self._base_url = base_url
        self._model_name = model_name
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        logger.info("SegmentationWorker 启动: model=%s", self._model_name)
        try:
            client = _create_client(self._api_key, self._base_url)

            messages = []
            if self._system_prompt:
                messages.append({"role": "system", "content": self._system_prompt})
            messages.append({"role": "user", "content": self._user_prompt})

            logger.info("发送片段分割请求...")
            response = client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                temperature=0.3,
                stream=False,
            )
            content = response.choices[0].message.content or ""
            logger.info("片段分割响应长度: %d", len(content))

            segments = self._parse_segments(content)
            if not segments:
                self.error.emit("片段分割结果解析失败，请重试")
                return

            logger.info("片段分割完成: %d 个片段", len(segments))
            self.finished.emit(segments)

        except Exception as e:
            logger.exception("片段分割失败")
            self.error.emit(str(e))

    def _parse_segments(self, text: str) -> list:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []
        segments = data.get("segments", [])
        if not isinstance(segments, list):
            return []
        return [s for s in segments if isinstance(s, dict) and "start_time" in s and "end_time" in s]


class ScriptGenerationWorker(QThread):
    chunk = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        system_prompt: str,
        user_prompt: str,
        api_key: str,
        base_url: str,
        model_name: str,
        temperature: float = 0.7,
        parent=None,
    ):
        super().__init__(parent)
        self._system_prompt = system_prompt
        self._user_prompt = user_prompt
        self._api_key = api_key
        self._base_url = base_url
        self._model_name = model_name
        self._temperature = temperature
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        logger.info("ScriptWorker 启动: model=%s", self._model_name)
        try:
            client = _create_client(self._api_key, self._base_url)

            messages = []
            if self._system_prompt:
                messages.append({"role": "system", "content": self._system_prompt})
            messages.append({"role": "user", "content": self._user_prompt})

            logger.info("发送解说文案生成请求...")
            response = client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                temperature=self._temperature,
                stream=True,
            )

            collected = []
            chunk_count = 0
            for chunk_data in response:
                if self._cancelled:
                    logger.info("Worker 被取消")
                    return

                if chunk_data.choices and len(chunk_data.choices) > 0:
                    delta = chunk_data.choices[0].delta
                    if delta and delta.content:
                        content = delta.content
                        collected.append(content)
                        self.chunk.emit(content)
                        chunk_count += 1
                        if chunk_count == 1:
                            logger.info("收到第一个 chunk: %s...", content[:50])

            full_text = "".join(collected)
            logger.info("流式完成: %d 个 chunk, %d 字", chunk_count, len(full_text))
            self.finished.emit(full_text)

        except Exception as e:
            logger.exception("解说文案生成失败")
            self.error.emit(str(e))


class PolishWorker(QThread):
    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        system_prompt: str,
        user_prompt: str,
        api_key: str,
        base_url: str,
        model_name: str,
        parent=None,
    ):
        super().__init__(parent)
        self._system_prompt = system_prompt
        self._user_prompt = user_prompt
        self._api_key = api_key
        self._base_url = base_url
        self._model_name = model_name
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        logger.info("PolishWorker 启动: model=%s", self._model_name)
        try:
            client = _create_client(self._api_key, self._base_url)

            messages = []
            if self._system_prompt:
                messages.append({"role": "system", "content": self._system_prompt})
            messages.append({"role": "user", "content": self._user_prompt})

            logger.info("发送润色请求...")
            response = client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                temperature=0.3,
                stream=False,
            )
            content = response.choices[0].message.content or ""
            logger.info("润色响应长度: %d", len(content))

            data = self._parse_result(content)
            if data.get("pass"):
                logger.info("润色检测通过，无需修改")
                self.finished.emit("")
                return

            rewritten = data.get("rewritten", "")
            if rewritten:
                logger.info("润色修正完成: %d 字符", len(rewritten))
                self.finished.emit(rewritten)
            else:
                logger.info("润色返回了 pass=false 但无 rewritten，保持原样")
                self.finished.emit("")

        except Exception as e:
            logger.exception("润色失败")
            self.error.emit(str(e))

    @staticmethod
    def _parse_result(text: str) -> dict:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {"pass": True}
        try:
            import json
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return {"pass": True}
