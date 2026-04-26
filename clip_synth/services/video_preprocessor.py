import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("clip_synth.video_preprocessor")


class VideoPreprocessor:
    """视频预处理服务，使用 ffmpeg 对视频进行降帧处理以加速 AI 分析"""

    TARGET_FPS = 5

    def __init__(self, cache_dir: str | None = None):
        if cache_dir:
            self._cache_dir = Path(cache_dir)
        else:
            self._cache_dir = (
                Path(__file__).resolve().parent.parent.parent
                / "cache" / "preprocessed"
            )
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def preprocess(self, video_path: str) -> str:
        """将视频降帧到 5fps，返回临时文件路径"""
        input_path = Path(video_path)
        if not input_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        output_path = self._cache_dir / f"{input_path.stem}_5fps{input_path.suffix}"

        if output_path.exists():
            logger.info("降帧视频已存在，跳过预处理: %s", output_path)
            return str(output_path)

        logger.info(
            "开始降帧处理: %s -> %s (目标: %d fps)",
            video_path, output_path, self.TARGET_FPS,
        )

        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-vf", f"fps={self.TARGET_FPS}",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "28",
            "-an",
            "-y",
            str(output_path),
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=False,
                timeout=600,
            )

            if result.returncode != 0:
                error_msg = result.stderr.decode("utf-8", errors="replace").strip() or "未知错误"
                raise RuntimeError(f"ffmpeg 处理失败: {error_msg}")

            output_size = output_path.stat().st_size
            logger.info(
                "降帧完成: %s (大小: %.1f MB)",
                output_path,
                output_size / (1024 * 1024),
            )
            return str(output_path)

        except FileNotFoundError as e:
            raise RuntimeError(
                "未找到 ffmpeg，请确保已安装 ffmpeg 并添加到系统 PATH"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("ffmpeg 处理超时（超过 600 秒）") from e

    def cleanup(self, video_path: str) -> None:
        """删除预处理生成的缓存文件"""
        path = Path(video_path)
        if path.exists() and path.parent == self._cache_dir:
            path.unlink(missing_ok=True)
            logger.debug("已删除缓存文件: %s", video_path)

    def cleanup_all(self) -> None:
        """清理所有预处理生成的缓存文件"""
        if self._cache_dir.exists():
            for f in self._cache_dir.iterdir():
                if f.is_file():
                    f.unlink(missing_ok=True)
            logger.info("已清理所有预处理缓存文件")
