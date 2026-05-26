import logging
import queue
import threading
import time

from clip_synth.services.ai_service import AIModelConfig, AIService

logger = logging.getLogger("clip_synth.image_gen")


class ImageGenTask:
    __slots__ = ("task_id", "config", "prompt", "on_done", "on_error", "size", "reference_images", "resolution", "aspect_ratio")

    def __init__(
        self,
        task_id: int,
        config: AIModelConfig,
        prompt: str,
        on_done,
        on_error,
        size: str = "1024x1024",
        reference_images: list[str] | None = None,
        resolution: str | None = None,
        aspect_ratio: str | None = None,
    ):
        self.task_id = task_id
        self.config = config
        self.prompt = prompt
        self.on_done = on_done
        self.on_error = on_error
        self.size = size
        self.reference_images = reference_images
        self.resolution = resolution
        self.aspect_ratio = aspect_ratio


class ImageGenService:
    _instance: "ImageGenService | None" = None
    _lock = threading.Lock()

    def __init__(self):
        self._concurrency = 1
        self._task_queue: queue.Queue[ImageGenTask | None] = queue.Queue()
        self._task_counter = 0
        self._counter_lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._running = False
        self._started = False

    @classmethod
    def instance(cls) -> "ImageGenService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_concurrency(self, n: int) -> None:
        n = max(1, min(n, 10))
        if n == self._concurrency:
            return
        self._concurrency = n
        if self._started:
            self._ensure_workers()

    @property
    def concurrency(self) -> int:
        return self._concurrency

    @property
    def pending_count(self) -> int:
        return self._task_queue.qsize()

    def submit(
        self,
        config: AIModelConfig,
        prompt: str,
        on_done,
        on_error,
        size: str = "1024x1024",
        reference_images: list[str] | None = None,
        resolution: str | None = None,
        aspect_ratio: str | None = None,
    ) -> int:
        with self._counter_lock:
            task_id = self._task_counter
            self._task_counter += 1

        task = ImageGenTask(task_id, config, prompt, on_done, on_error, size, reference_images, resolution, aspect_ratio)
        self._task_queue.put(task)
        self._started = True
        self._ensure_workers()
        logger.info("提交生图任务 #%d: %s", task_id, prompt[:60])
        return task_id

    def _ensure_workers(self) -> None:
        needed = self._concurrency - sum(1 for w in self._workers if w.is_alive())
        for _ in range(needed):
            t = threading.Thread(target=self._worker_loop, daemon=True)
            self._workers.append(t)
            t.start()
            logger.debug("启动生图工作线程，当前活跃: %d", self._active_worker_count())

    def _active_worker_count(self) -> int:
        return sum(1 for w in self._workers if w.is_alive())

    def _worker_loop(self) -> None:
        while True:
            try:
                task = self._task_queue.get(timeout=10)
            except queue.Empty:
                continue

            if task is None:
                break

            try:
                logger.info("生图任务 #%d 开始: %s", task.task_id, task.prompt[:60])
                service = AIService(task.config)
                max_retries = 2
                for attempt in range(max_retries + 1):
                    try:
                        image_data = service.generate_image(
                            prompt=task.prompt,
                            size=task.size,
                            reference_images=task.reference_images,
                            resolution=task.resolution,
                            aspect_ratio=task.aspect_ratio,
                        )
                        break
                    except Exception as e:
                        is_timeout = "timeout" in str(e).lower() or "timed out" in str(e).lower()
                        if is_timeout and attempt < max_retries:
                            wait = (attempt + 1) * 15
                            logger.warning(
                                "生图任务 #%d 超时 (attempt %d/%d), %ds 后重试...",
                                task.task_id, attempt + 1, max_retries + 1, wait,
                            )
                            time.sleep(wait)
                        else:
                            raise
                logger.info("生图任务 #%d 完成, 大小=%d bytes", task.task_id, len(image_data))
                task.on_done(task.task_id, image_data)
            except Exception as e:
                logger.error("生图任务 #%d 失败: %s", task.task_id, str(e), exc_info=True)
                task.on_error(task.task_id, str(e))

    def shutdown(self) -> None:
        for _ in self._workers:
            self._task_queue.put(None)
        for w in self._workers:
            w.join(timeout=5)
        self._workers.clear()
        self._started = False
        logger.info("生图服务已关闭")
