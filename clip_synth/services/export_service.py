import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Callable, List, Tuple

logger = logging.getLogger("clip_synth.export_service")


class ExportService:
    def __init__(self, output_dir: str | None = None):
        if output_dir:
            self._output_dir = Path(output_dir)
        else:
            self._output_dir = (
                Path(__file__).resolve().parent.parent.parent / "exports"
            )
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._is_exporting = False
        self._process = None

    async def export_video(
        self,
        project_name: str,
        segments: List[Tuple[str, str, str]],
        progress_callback: Callable[[float], None] | None = None,
    ) -> Path:
        self._is_exporting = True

        try:
            output_path = self._output_dir / f"{project_name}_剪辑成品.mp4"

            if not segments:
                raise ValueError("没有选中的视频片段")

            if len(segments) == 1:
                video_path, start_time, end_time = segments[0]
                if progress_callback:
                    progress_callback(10.0)
                await self._run_ffmpeg_cut(
                    video_path, start_time, end_time, output_path,
                )
                if progress_callback:
                    progress_callback(100.0)
            else:
                concat_dir = self._output_dir / f".{project_name}_concat_parts"
                concat_dir.mkdir(parents=True, exist_ok=True)
                part_paths = []

                try:
                    total = len(segments)
                    for i, (video_path, start_time, end_time) in enumerate(segments):
                        part_path = concat_dir / f"part_{i:04d}.mp4"
                        await self._run_ffmpeg_cut(
                            video_path, start_time, end_time, part_path,
                        )
                        part_paths.append(part_path)

                        if progress_callback:
                            pct = 10.0 + (i + 1) / total * 70.0
                            progress_callback(pct)

                    await self._run_ffmpeg_concat(part_paths, output_path)

                    if progress_callback:
                        progress_callback(100.0)
                finally:
                    for p in part_paths:
                        try:
                            p.unlink(missing_ok=True)
                        except Exception:
                            pass
                    try:
                        concat_dir.rmdir()
                    except Exception:
                        pass

            logger.info("视频导出完成: %s", output_path)
            return output_path

        finally:
            self._is_exporting = False

    async def _run_ffmpeg_cut(
        self,
        video_path: str,
        start_time: str,
        end_time: str,
        output_path: Path,
    ) -> None:
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-ss", start_time,
            "-to", end_time,
            "-c", "copy",
            "-y",
            str(output_path),
        ]

        await self._run_subprocess(cmd, "切割")

    async def _run_ffmpeg_concat(
        self,
        part_paths: List[Path],
        output_path: Path,
    ) -> None:
        concat_file = output_path.with_suffix(".txt")
        try:
            concat_file.write_text(
                "\n".join(f"file '{p.resolve()}'" for p in part_paths),
                encoding="utf-8",
            )

            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                "-y",
                str(output_path),
            ]

            await self._run_subprocess(cmd, "合并")
        finally:
            try:
                concat_file.unlink(missing_ok=True)
            except Exception:
                pass

    async def _run_subprocess(self, cmd: List[str], action: str) -> None:
        loop = asyncio.get_event_loop()

        def run():
            flags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                flags = subprocess.CREATE_NO_WINDOW
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=flags,
            )
            self._process = proc
            stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip() or "未知错误"
                raise RuntimeError(f"ffmpeg {action}失败: {error_msg}")
            return stdout, stderr

        try:
            await loop.run_in_executor(None, run)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"ffmpeg {action}失败: {str(e)}") from e
        finally:
            self._process = None

    def cancel_export(self) -> None:
        self._is_exporting = False
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass

    @property
    def is_exporting(self) -> bool:
        return self._is_exporting
