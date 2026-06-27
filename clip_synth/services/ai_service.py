import asyncio
import base64
import logging
import time
from pathlib import Path

from openai import AsyncOpenAI, OpenAI

logger = logging.getLogger("clip_synth.ai")


class AIModelConfig:
    def __init__(
        self,
        model_name: str = "",
        api_key: str = "",
        base_url: str = "",
        api_type: str = "openai",
        api_provider: str = "newapi",
    ):
        self.model_name = model_name
        self.api_key = api_key
        self.api_provider = api_provider
        if api_provider in ("toapi", "grasai"):
            self.base_url = base_url.strip().rstrip("/")
        elif api_type == "openai":
            self.base_url = self._normalize_base_url(base_url)
        else:
            self.base_url = base_url.strip().rstrip("/")
        self.api_type = api_type

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
            "api_type": self.api_type,
            "api_provider": self.api_provider,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AIModelConfig":
        return cls(
            model_name=data.get("model_name", ""),
            api_key=data.get("api_key", ""),
            base_url=data.get("base_url", ""),
            api_type=data.get("api_type", "openai"),
            api_provider=data.get("api_provider", "newapi"),
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
                extra_body={"chat_template_kwargs":{"thinking":False}},
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

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        reference_images: list[str] | None = None,
        mask_image: str | None = None,
        resolution: str | None = None,
        aspect_ratio: str | None = None,
    ) -> bytes:
        logger.info("当前供应商: %s", self._config.api_provider)
        if self._config.api_provider == "toapi":
            return self._generate_image_toapi(prompt, size, reference_images)
        if self._config.api_provider == "grasai":
            return self._generate_image_grasai(prompt, size, reference_images, resolution, aspect_ratio)
        if self._config.api_provider == "manxiaobai":
            return self._generate_image_manxiaobai(prompt, size, reference_images, resolution, aspect_ratio)
        if self._config.api_type == "gemini":
            return self._generate_image_gemini(prompt, size, reference_images)
        return self._generate_image_openai(prompt, size, reference_images, mask_image)

    def _is_gemini_model(self) -> bool:
        return self._config.model_name.lower().startswith("gemini")

    @staticmethod
    def _get_gemini_extra_body() -> dict:
        return {
            "safetySettings": [
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_HATE_SPEECH",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", 
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT", 
                "threshold": "BLOCK_NONE"
            }
        ]
        }

    def _generate_image_openai(
        self,
        prompt: str,
        size: str,
        reference_images: list[str] | None,
        mask_image: str | None = None,
    ) -> bytes:
        import base64
        import httpx

        ref_list: list[tuple[str, bytes]] = []
        if reference_images:
            logger.info("收到 %d 张参考图路径", len(reference_images))
            for path in reference_images:
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                    name = Path(path).stem
                    ref_list.append((name, data))
                    logger.info("参考图加载成功 %s -> %s.png (%d bytes)", path, name, len(data))
                except Exception as e:
                    logger.warning("读取参考图失败 %s: %s", path, e)
        else:
            logger.info("没有参考图，使用纯文生图")

        base_url = self._config.base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
        }

        if ref_list:
            logger.info("调用 /v1/images/edits (参考图=%d 张, size=%s)", len(ref_list), size)
            files: list = [
                ("image[]", (f"{name}.png", data, "image/png"))
                for name, data in ref_list
            ]
            # 添加遮罩（如果提供了mask_image）
            mask_data: bytes | None = None
            if mask_image:
                try:
                    with open(mask_image, "rb") as f:
                        mask_data = f.read()
                    files.append(("mask", ("mask.png", mask_data, "image/png")))
                    logger.info("已添加遮罩文件: %s", mask_image)
                except Exception as e:
                    logger.warning("读取遮罩文件失败 %s: %s", mask_image, e)
            with httpx.Client(timeout=httpx.Timeout(900, connect=30)) as http:
                try:
                    resp = http.post(
                        f"{base_url}/images/edits",
                        headers=headers,
                        data={"model": self._config.model_name, "prompt": prompt, "size": size},
                        files=files,
                    )
                    resp.raise_for_status()
                    result = resp.json()
                    logger.info("/v1/images/edits 成功（mask=%s）", "是" if mask_data else "否")
                except Exception as e1:
                    logger.warning("/v1/images/edits 失败: %s，回退 generations", e1)
                    resp = http.post(
                        f"{base_url}/images/generations",
                        headers=headers,
                        json={
                            "model": self._config.model_name,
                            "prompt": prompt,
                            "n": 1,
                            "size": size,
                        },
                    )
                    resp.raise_for_status()
                    result = resp.json()
        else:
            logger.info("调用 /v1/images/generations (纯文生图)")
            with httpx.Client(timeout=httpx.Timeout(900, connect=30)) as http:
                resp = http.post(
                    f"{base_url}/images/generations",
                    headers=headers,
                    json={
                        "model": self._config.model_name,
                        "prompt": prompt,
                        "n": 1,
                        "size": size,
                    },
                )
                resp.raise_for_status()
                result = resp.json()

        data_items = result.get("data", [])
        if not data_items:
            raise RuntimeError("图片生成未返回图像数据")

        item = data_items[0]
        if "b64_json" in item and item["b64_json"]:
            logger.debug("图片返回 b64_json, 长度=%d", len(item["b64_json"]))
            return base64.b64decode(item["b64_json"])

        image_url = item.get("url", "")
        if image_url:
            logger.debug("图片返回 URL, 开始下载: %s", image_url[:80])
            with httpx.Client(timeout=httpx.Timeout(900)) as http:
                dl_resp = http.get(image_url)
                dl_resp.raise_for_status()
                data = dl_resp.content
                logger.debug("图片下载完成, 大小=%d bytes", len(data))
                return data

        raise RuntimeError("图片生成未返回图像数据")

    def _generate_image_gemini(
        self,
        prompt: str,
        size: str,
        reference_images: list[str] | None,
    ) -> bytes:
        import base64
        import httpx

        if self._config.api_provider == "toapi" or self._config.api_provider == "grasai":
            return self._generate_image_gemini_toapi(prompt, size, reference_images)

        parts: list[dict] = []
        if reference_images:
            for path in reference_images:
                try:
                    with open(path, "rb") as f:
                        img_data = f.read()
                    parts.append({
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": base64.b64encode(img_data).decode("utf-8"),
                        },
                    })
                    logger.info("Gemini 参考图加载成功 %s (%d bytes)", path, len(img_data))
                except Exception as e:
                    logger.warning("Gemini 读取参考图失败 %s: %s", path, e)

        parts.append({"text": prompt})

        body: dict = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }

        model = self._config.model_name
        base = self._config.base_url.rstrip("/")
        url = f"{base}/models/{model}:generateContent"

        logger.info("调用 Gemini 生图: %s", url)
        with httpx.Client(timeout=httpx.Timeout(900)) as http:
            resp = http.post(
                url,
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            result = resp.json()

        candidates = result.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini 图片生成未返回数据")

        for candidate in candidates:
            content = candidate.get("content", {})
            for part in content.get("parts", []):
                if "inlineData" in part:
                    b64 = part["inlineData"].get("data", "")
                    if b64:
                        logger.debug("Gemini 返回 inlineData, 长度=%d", len(b64))
                        return base64.b64decode(b64)
                if "fileData" in part:
                    file_url = part["fileData"].get("fileUri", "")
                    if file_url:
                        logger.debug("Gemini 返回文件 URL, 开始下载: %s", file_url[:80])
                        with httpx.Client(timeout=httpx.Timeout(900)) as http2:
                            dl_resp = http2.get(file_url)
                            dl_resp.raise_for_status()
                            data = dl_resp.content
                            logger.debug("Gemini 图片下载完成, 大小=%d bytes", len(data))
                            return data

        raise RuntimeError("Gemini 图片生成未返回图像数据")

    def _generate_image_gemini_toapi(
        self,
        prompt: str,
        size: str,
        reference_images: list[str] | None,
    ) -> bytes:
        import time
        import httpx

        base_url = self._config.base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {self._config.api_key}"}

        ref_urls: list[str] = []
        if reference_images:
            logger.info("ToAPI Gemini 上传 %d 张参考图", len(reference_images))
            for path in reference_images:
                try:
                    with open(path, "rb") as f:
                        resp = httpx.post(
                            f"{base_url}/v1/uploads/images",
                            headers=headers,
                            files={"file": (Path(path).name, f.read(), "image/png")},
                            timeout=120,
                        )
                    body = resp.json()
                    if body.get("success") and body.get("data", {}).get("url"):
                        url = body["data"]["url"]
                        ref_urls.append(url)
                        logger.info("ToAPI Gemini 参考图上传成功 %s -> %s", path, url)
                    else:
                        logger.warning("ToAPI Gemini 上传失败 %s: %s", path, body)
                except Exception as e:
                    logger.warning("ToAPI Gemini 上传参考图异常 %s: %s", path, e)

        ratio = self._size_to_ratio(size)
        resolution = self._size_to_resolution(size)
        gen_body: dict = {
            "model": self._config.model_name,
            "prompt": prompt,
            "n": 1,
            "size": ratio,
            "metadata": {"resolution": resolution, "moderation": "low", "quality": "high"},
        }
        if ref_urls:
            gen_body["image_urls"] = [{"url": u} for u in ref_urls]

        logger.info("ToAPI Gemini 创建生图任务: ratio=%s resolution=%s", ratio, resolution)
        with httpx.Client(timeout=httpx.Timeout(120)) as http:
            resp = http.post(
                f"{base_url}/v1/images/generations",
                headers={**headers, "Content-Type": "application/json"},
                json=gen_body,
            )
            resp.raise_for_status()
            task_result = resp.json()

        task_id = task_result.get("id") or task_result.get("task_id")
        if not task_id:
            raise RuntimeError(f"ToAPI Gemini 创建任务失败，无 task_id: {task_result}")

        logger.info("ToAPI Gemini 任务已创建: %s，开始轮询", task_id)
        deadline = time.time() + 900
        while time.time() < deadline:
            time.sleep(3)
            with httpx.Client(timeout=httpx.Timeout(600)) as http:
                poll_resp = http.get(
                    f"{base_url}/v1/images/generations/{task_id}",
                    headers=headers,
                )
                poll_resp.raise_for_status()
                status_body = poll_resp.json()

            status = status_body.get("status")
            logger.debug("ToAPI Gemini 任务 %s 状态: %s progress=%s", task_id, status, status_body.get("progress"))

            if status == "completed":
                result_data = status_body.get("result", {})
                items = result_data.get("data", [])
                if items and items[0].get("url"):
                    image_url = items[0]["url"]
                    logger.info("ToAPI Gemini 生成完成, 下载图片: %s", image_url[:80])
                    with httpx.Client(timeout=httpx.Timeout(900)) as http:
                        dl_resp = http.get(image_url)
                        dl_resp.raise_for_status()
                        data = dl_resp.content
                        logger.info("ToAPI Gemini 图片下载完成, 大小=%d bytes", len(data))
                        return data
                raise RuntimeError(f"ToAPI Gemini 完成但无图片 URL: {status_body}")
            elif status == "failed":
                error_info = status_body.get("error", {})
                raise RuntimeError(
                    f"ToAPI Gemini 生成失败: {error_info.get('message') or status_body.get('fail_reason') or status_body}"
                )

        raise TimeoutError("ToAPI Gemini 生图任务超时 (900s)")

    @property
    def config(self) -> AIModelConfig:
        return self._config

    @config.setter
    def config(self, value: AIModelConfig) -> None:
        self._config = value
        self._client = None
        self._async_client = None

    def _size_to_ratio(self, size: str) -> str:
        import math
        parts = size.lower().replace("x", "×").split("×")
        if len(parts) != 2:
            return "1:1"
        try:
            w = int(parts[0].strip())
            h = int(parts[1].strip())
        except ValueError:
            return "1:1"
        if w <= 0 or h <= 0:
            return "1:1"
        g = math.gcd(w, h)
        return f"{w // g}:{h // g}"

    def _size_to_resolution(self, size: str) -> str:
        for r in ["4K", "2K", "1K"]:
            if r.lower() in size.lower():
                return r
        return "1K"

    def _generate_image_toapi(
        self,
        prompt: str,
        size: str,
        reference_images: list[str] | None,
    ) -> bytes:
        import time
        import httpx

        base_url = self._config.base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {self._config.api_key}"}

        ref_urls: list[str] = []
        if reference_images:
            logger.info("ToAPI 上传 %d 张参考图", len(reference_images))
            for path in reference_images:
                try:
                    with open(path, "rb") as f:
                        resp = httpx.post(
                            f"{base_url}/v1/uploads/images",
                            headers=headers,
                            files={"file": (Path(path).name, f.read(), "image/png")},
                            timeout=120,
                        )
                    body = resp.json()
                    if body.get("success") and body.get("data", {}).get("url"):
                        url = body["data"]["url"]
                        ref_urls.append(url)
                        logger.info("ToAPI 参考图上传成功 %s -> %s", path, url)
                    else:
                        logger.warning("ToAPI 上传失败 %s: %s", path, body)
                except Exception as e:
                    logger.warning("ToAPI 上传参考图异常 %s: %s", path, e)

        ratio = self._size_to_ratio(size)
        resolution = self._size_to_resolution(size)
        gen_body: dict = {
            "model": self._config.model_name,
            "prompt": prompt,
            "n": 1,
            "size": ratio,
            "resolution": resolution,
            "response_format": "url",
            "moderation": "low",
        }
        if ref_urls:
            gen_body["reference_images"] = ref_urls

        logger.info("ToAPI 创建生图任务: ratio=%s resolution=%s", ratio, resolution)
        with httpx.Client(timeout=httpx.Timeout(120)) as http:
            resp = http.post(
                f"{base_url}/v1/images/generations",
                headers={**headers, "Content-Type": "application/json"},
                json=gen_body,
            )
            resp.raise_for_status()
            task_result = resp.json()

        task_id = task_result.get("id") or task_result.get("task_id")
        if not task_id:
            raise RuntimeError(f"ToAPI 创建任务失败，无 task_id: {task_result}")

        logger.info("ToAPI 任务已创建: %s，开始轮询", task_id)
        deadline = time.time() + 900
        while time.time() < deadline:
            time.sleep(3)
            with httpx.Client(timeout=httpx.Timeout(600)) as http:
                poll_resp = http.get(
                    f"{base_url}/v1/images/generations/{task_id}",
                    headers=headers,
                )
                poll_resp.raise_for_status()
                status_body = poll_resp.json()

            status = status_body.get("status")
            logger.debug("ToAPI 任务 %s 状态: %s progress=%s", task_id, status, status_body.get("progress"))

            if status == "completed":
                result_data = status_body.get("result", {})
                items = result_data.get("data", [])
                if items and items[0].get("url"):
                    image_url = items[0]["url"]
                    logger.info("ToAPI 生成完成, 下载图片: %s", image_url[:80])
                    with httpx.Client(timeout=httpx.Timeout(900)) as http:
                        dl_resp = http.get(image_url)
                        dl_resp.raise_for_status()
                        data = dl_resp.content
                        logger.info("ToAPI 图片下载完成, 大小=%d bytes", len(data))
                        return data
                raise RuntimeError(f"ToAPI 完成但无图片 URL: {status_body}")
            elif status == "failed":
                error_info = status_body.get("error", {})
                raise RuntimeError(
                    f"ToAPI 生成失败: {error_info.get('message') or status_body.get('fail_reason') or status_body}"
                )

        raise TimeoutError("ToAPI 生图任务超时 (900s)")

    def chat_completion_with_history(
        self,
        chat_manager: "MultiRoundChatManager",
        system_prompt: str,
        user_content: str,
        temperature: float = 0.3,
        timeout: float | None = 900,
        response_format: dict | None = None,
    ) -> str:
        messages = chat_manager.get_full_messages(self._config, system_prompt, user_content)
        client = self._ensure_client()

        kwargs: dict = dict(
            model=self._config.model_name,
            messages=messages,
            temperature=temperature,
            timeout=timeout,
        )
        if response_format:
            kwargs["response_format"] = response_format

        if self._is_gemini_model():
            kwargs["extra_body"] = self._get_gemini_extra_body()


        response = client.chat.completions.create(**kwargs)
        assistant_msg = response.choices[0].message
        content = assistant_msg.content or ""
        messages.append(assistant_msg)
        chat_manager.save_exchange(user_content, assistant_msg)
        return content

    def _generate_image_grasai(
        self,
        prompt: str,
        size: str,
        reference_images: list[str] | None,
        resolution: str | None = None,
        aspect_ratio: str | None = None,
    ) -> bytes:
        import base64
        import time
        import httpx

        base_url = self._config.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        headers = {"Authorization": f"Bearer {self._config.api_key}"}

        urls: list[str] = []
        if reference_images:
            for path in reference_images:
                try:
                    with open(path, "rb") as f:
                        img_data = f.read()
                    ext = Path(path).suffix.lower()
                    mime = "image/png" if ext == ".png" else "image/jpeg"
                    b64 = base64.b64encode(img_data).decode("utf-8")
                    urls.append(f"data:{mime};base64,{b64}")
                except Exception as e:
                    logger.warning("Grasai 读取参考图失败 %s: %s", path, e)

        body: dict = {
            "model": self._config.model_name or "nano-banana-pro",
            "prompt": prompt,
            "aspectRatio": aspect_ratio or "1:1",
            "imageSize": resolution or "1K",
            "urls": urls,
            "webHook": "-1",
            "moderation": "low",
        }
        call_url = f"{base_url}/v1/draw/nano-banana"
        if self._config.api_type == 'openai':
            body['quality'] = "high"
            body["moderation"] = "low"
            call_url = f"{base_url}/v1/draw/completions"
        else:
            body['prompt'] += '。不要解释，直接根据我的描述生成图片。'

        logger.info("Grasai 提交生图任务: %s", call_url)
        with httpx.Client(timeout=httpx.Timeout(120)) as http:
            resp = http.post(
                call_url,
                headers={**headers, "Content-Type": "application/json"},
                json=body,
            )
            resp.raise_for_status()
            task_data = resp.json()

        task_id = task_data.get('data').get('id')
        if not task_id:
            raise RuntimeError(f"Grasai 创建任务失败，无 task_id: {task_data}")

        logger.info("Grasai 任务已创建: %s，开始轮询", task_id)
        deadline = time.time() + 900
        while time.time() < deadline:
            time.sleep(3)
            with httpx.Client(timeout=httpx.Timeout(600)) as http:
                poll_resp = http.post(
                    f"{base_url}/v1/draw/result",
                    headers={**headers, "Content-Type": "application/json"},
                    json={"id": task_id},
                )
                poll_resp.raise_for_status()
                result_data = poll_resp.json().get('data', {})
                
            status = result_data.get("status", "")

            if status == "succeeded":
                results = result_data.get("results", [])
                if results:
                    image_url = results[0].get("url", "")
                    if image_url:
                        logger.info("Grasai 下载图片: %s", image_url[:80])
                        with httpx.Client(timeout=httpx.Timeout(900)) as http:
                            dl_resp = http.get(image_url)
                            dl_resp.raise_for_status()
                            return dl_resp.content
                raise RuntimeError(f"Grasai 任务完成但未返回图片数据: {result_data}")

            if status in ("failed", "error"):
                error_msg = result_data.get("error", result_data.get("failure_reason", "未知错误"))
                raise RuntimeError(f"Grasai 生图失败: {error_msg}")

        raise TimeoutError("Grasai 生图任务超时 (900s)")

    def _generate_image_manxiaobai(
        self,
        prompt: str,
        size: str,
        reference_images: list[str] | None,
        resolution: str | None = None,
        aspect_ratio: str | None = None,
        ) -> bytes:

        import base64
        import time
        import httpx

        base_url = self._config.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]

        urls: list[str] = []
        if reference_images:
            for path in reference_images:
                try:
                    with open(path, "rb") as f:
                        img_data = f.read()
                    ext = Path(path).suffix.lower()
                    mime = "image/png" if ext == ".png" else "image/jpeg"
                    b64 = base64.b64encode(img_data).decode("utf-8")
                    urls.append(f"data:{mime};base64,{b64}")
                except Exception as e:
                    logger.warning("Grasai 读取参考图失败 %s: %s", path, e)

        body: dict = {
            "model": self._config.model_name or "gpt-image-2",
            "prompt": prompt,
            "size": size or "1024x1024",
            "imageSize": resolution or "1K",
            "response_format": "url",
            "images": urls,
            "quality": "high"
        }

        call_url = f"{base_url}/v1/image-tasks/edits"

        if self._config.api_type == 'gemini' or not urls:
            call_url = f"{base_url}/v1/image-tasks/generations"

        logger.info(f"ManXiaoBai 提交生图任务: {call_url}")
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        with httpx.Client(timeout=httpx.Timeout(120)) as http:
            resp = http.post(
                call_url,
                headers={**headers, "Content-Type": "application/json"},
                json=body,
            )
            resp.raise_for_status()
            task_data = resp.json()

        task_id = task_data.get('id', None)
        logger.info(f"获取到 task_id: {task_id}")
        if not task_id:
            logger.info(f"ManXiaoBai 提交生图任务失败，{task_data}")
            raise RuntimeError(f"ManXiaoBai 创建任务失败，无 task_id: {task_data}")

        logger.info("ManXiaoBai 任务已创建: %s，开始轮询", task_id)
        deadline = time.time() + 900
        while time.time() < deadline:
            time.sleep(3)
            with httpx.Client(timeout=httpx.Timeout(600)) as http:
                poll_resp = http.get(
                    f"{base_url}/v1/image-tasks/{task_id}",
                    headers={**headers, "Content-Type": "application/json"},
                )
                poll_resp.raise_for_status()
                result_data = poll_resp.json()

            status = result_data.get("status", "")

            if status == "succeeded":
                results = result_data.get("result", {}).get("data", [])
                if results:
                    image_url = results[0].get("url", "")
                    if image_url:
                        logger.info("ManXiaoBai 下载图片: %s", image_url[:80])
                        with httpx.Client(timeout=httpx.Timeout(900)) as http:
                            dl_resp = http.get(image_url)
                            dl_resp.raise_for_status()
                            return dl_resp.content
                raise RuntimeError(f"ManXiaoBai 任务完成但未返回图片数据: {result_data}")

            if status in ("failed", "error"):
                logger.info(f"ManXiaoBai 生图失败: {poll_resp.text}")
                error_msg = result_data.get("error", result_data.get("failure_reason", "未知错误"))
                raise RuntimeError(f"ManXiaoBai 生图失败: {error_msg}")
        
        raise TimeoutError("ManXiaoBai 生图任务超时 (900s)")


    @staticmethod
    def rewrite_prompt(
        text_model_config: "AIModelConfig",
        failed_prompt: str,
        error_info: str,
    ) -> str | None:
        import httpx
        import json as _json

        base_url = text_model_config.base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {text_model_config.api_key}",
            "Content-Type": "application/json",
        }

        system_prompt = (
            "你是一个漫画生图提示词优化专家。用户给你的提示词在图片生成时被内容审核拦截了，"
            "请你分析可能存在的违规内容（血腥、暴力、色情、敏感词汇等），"
            "将其改写为合规但不改变画面构图和核心叙事的表达方式。"
            "只输出优化后的提示词文本，不要任何解释、不要任何 Markdown 格式。"
        )

        user_prompt = (
            f"原始提示词：\n{failed_prompt}\n\n"
            f"错误信息：\n{error_info}\n\n"
            "请输出优化后的提示词："
        )

        body = {
            "model": text_model_config.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
        }

        try:
            with httpx.Client(timeout=httpx.Timeout(60)) as http:
                resp = http.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if content and len(content) > 10:
                    logger.info("AI 重写提示词成功，长度 %d -> %d", len(failed_prompt), len(content))
                    return content
                logger.warning("AI 重写提示词返回内容过短或为空")
                return None
        except Exception as e:
            logger.warning("AI 重写提示词失败: %s", e)
            return None


class MultiRoundChatManager:
    def __init__(
        self,
        project_id: str,
        episode_num: int,
        state_service,
    ):
        self._project_id = project_id
        self._episode_num = episode_num
        self._state_service = state_service
        self._lock = __import__("threading").Lock()

    def is_multi_round_enabled(self, config: AIModelConfig) -> bool:
        return False
        # return config.model_name.lower() == "deepseek-v4-pro" or config.model_name.lower() == "deepseek-v4-flash"

    def get_full_messages(
        self,
        config: AIModelConfig,
        system_prompt: str,
        user_content: str,
    ) -> list[dict]:
        history = self._state_service.load_chat_history(self._project_id, self._episode_num)
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        for msg in history:
            messages.append(msg)
        messages.append({"role": "user", "content": user_content})
        return messages

    def save_exchange(
        self,
        user_content: str,
        assistant_message,
    ) -> None:
        with self._lock:
            user_msg = {"role": "user", "content": user_content}
            if hasattr(assistant_message, "model_dump"):
                assistant_msg = assistant_message.model_dump(exclude_none=True)
            elif hasattr(assistant_message, "to_dict"):
                assistant_msg = assistant_message.to_dict()
            else:
                assistant_msg = {"role": "assistant", "content": str(assistant_message)}
            exchange = [user_msg, assistant_msg]
            self._state_service.save_chat_history(self._project_id, self._episode_num, exchange)

    def clear_history(self) -> None:
        path = self._state_service.get_chat_history_path(self._project_id, self._episode_num)
        path.unlink(missing_ok=True)
        logger.info("已清除聊天历史: %s", path)
