import asyncio
import base64
import logging
from pathlib import Path

from openai import AsyncOpenAI, OpenAI

logger = logging.getLogger("clip_synth.ai")


class AIModelConfig:
    def __init__(
        self,
        model_name: str = "",
        api_key: str = "",
        base_url: str = "",
    ):
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = self._normalize_base_url(base_url)

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        url = url.strip().rstrip("/")
        if not url:
            return ""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        if not url.endswith("/v1"):
            url = url + "/v1"
        return url

    @property
    def is_configured(self) -> bool:
        return bool(self.model_name and self.api_key and self.base_url)

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "api_key": self.api_key,
            "base_url": self.base_url,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AIModelConfig":
        return cls(
            model_name=data.get("model_name", ""),
            api_key=data.get("api_key", ""),
            base_url=data.get("base_url", ""),
        )


MAX_CONCURRENT_REQUESTS = 3


class AIService:
    def __init__(self, config: AIModelConfig):
        self._config = config
        self._client: OpenAI | None = None
        self._async_client: AsyncOpenAI | None = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    def _ensure_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self._config.api_key,
                base_url=self._config.base_url,
                timeout=None,
                max_retries=0,
            )
        return self._client

    def _ensure_async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=self._config.api_key,
                base_url=self._config.base_url,
                timeout=None,
                max_retries=0,
            )
        return self._async_client

    def test_connection(self) -> tuple[bool, str]:
        try:
            logger.info(
                "发送 chat completion 请求: model=%s, base_url=%s",
                self._config.model_name, self._config.base_url,
            )
            client = OpenAI(
                api_key=self._config.api_key,
                base_url=self._config.base_url,
                timeout=None,
                max_retries=1,
            )
            response = client.chat.completions.create(
                model=self._config.model_name,
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=50,
                temperature=0.1,
            )
            logger.info("请求成功, model=%s, choices=%s", response.model, len(response.choices))
            if response.choices:
                msg = response.choices[0].message
                logger.info("message: role=%s, content=%s, finish_reason=%s", msg.role, msg.content, response.choices[0].finish_reason)  # noqa: E501
                if msg.content:
                    content = msg.content.strip()
                    logger.info("返回内容=%s", content)
                    return True, f"连接成功: {content}"
                return True, "连接成功"
            return False, "模型返回内容为空，请检查模型名称是否正确"
        except Exception as e:
            error_msg = str(e)
            logger.error("请求失败: %s", error_msg)
            if "authentication" in error_msg.lower() or "api_key" in error_msg.lower():
                return False, "认证失败，请检查 API Key 是否正确"
            if "not found" in error_msg.lower() or "404" in error_msg:
                return False, "模型不存在，请检查模型名称是否正确"
            if "rate limit" in error_msg.lower():
                return False, "超出速率限制，请稍后重试"
            if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                return False, "请求超时，请检查网络连接或 API 地址"
            if "connection" in error_msg.lower():
                return False, f"连接错误: {error_msg}"
            return False, error_msg

    async def test_connection_async(self) -> tuple[bool, str]:
        try:
            client = self._ensure_async_client()
            response = await client.chat.completions.create(
                model=self._config.model_name,
                messages=[{"role": "user", "content": "Hello"}],
            )
            content = response.choices[0].message.content
            if not content or not content.strip():
                return False, "模型返回内容为空，请检查模型名称是否正确"
            return True, f"连接成功: {content.strip()}"
        except Exception as e:
            return False, str(e)

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        async with self._semaphore:
            client = self._ensure_async_client()
            kwargs = dict(
                model=self._config.model_name,
                messages=messages,
                temperature=temperature,
                timeout=timeout,
            )
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            response = await client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    async def generate_text_with_images(
        self,
        prompt: str,
        image_paths: list[str],
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float | None = None,
    ) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        content: list[dict] = [{"type": "text", "text": prompt}]

        for image_path in image_paths:
            img_path = Path(image_path)
            if not img_path.exists():
                logger.warning("图片文件不存在，跳过: %s", image_path)
                continue

            with open(img_path, "rb") as f:
                base64_image = base64.b64encode(f.read()).decode("utf-8")

            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_image}",
                },
            })

        messages.append({"role": "user", "content": content})

        async with self._semaphore:
            client = self._ensure_async_client()
            response = await client.chat.completions.create(
                model=self._config.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        return response.choices[0].message.content or ""

    async def close(self):
        if self._async_client is not None:
            try:
                await self._async_client.close()
            except Exception:
                pass
            self._async_client = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    @property
    def config(self) -> AIModelConfig:
        return self._config

    @config.setter
    def config(self, value: AIModelConfig) -> None:
        self._config = value
        self._client = None
        self._async_client = None
