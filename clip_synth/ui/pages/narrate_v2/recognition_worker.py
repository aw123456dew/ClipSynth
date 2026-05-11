import logging
import subprocess
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from clip_synth.services.subtitle_recognition_service import (
    RecognitionConfig,
    SubtitleRecognitionService,
)

logger = logging.getLogger("clip_synth.narrate_v2")


class RecognitionWorker(QThread):
    progress = Signal(int, str)
    recognition_finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        video_paths: list,
        language: str,
        app_id: str,
        token: str,
        project_id: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._video_paths = video_paths
        self._language = language
        self._app_id = app_id
        self._token = token
        self._project_id = project_id
        self._process = None

    def _run_ffmpeg(self, cmd: list) -> None:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, **kwargs,
        )
        _, stderr = self._process.communicate()
        if self._process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace").strip() or "未知错误"
            raise RuntimeError(f"ffmpeg 处理失败: {error_msg}")

    def cancel(self):
        if self._process:
            self._process.terminate()

    def run(self):
        try:
            projects_cache_dir = Path(__file__).resolve().parent.parent.parent.parent / "cache" / "projects"
            project_dir = projects_cache_dir / (self._project_id or uuid.uuid4().hex[:16])
            project_dir.mkdir(parents=True, exist_ok=True)

            merged_video_path = str(project_dir / "merged.mp4")
            audio_path = str(project_dir / "audio.wav")

            need_merge = not Path(merged_video_path).exists()
            need_extract = not Path(audio_path).exists()

            if need_merge:
                self.progress.emit(5, "正在合并视频...")
                if len(self._video_paths) == 1:
                    Path(merged_video_path).symlink_to(Path(self._video_paths[0]).resolve())
                else:
                    concat_file = project_dir / "concat.txt"
                    try:
                        concat_file.write_text(
                            "\n".join(f"file '{Path(p).resolve()}'" for p in self._video_paths),
                            encoding="utf-8",
                        )
                        cmd = [
                            "ffmpeg", "-f", "concat", "-safe", "0",
                            "-i", str(concat_file),
                            "-c", "copy", "-y", merged_video_path,
                        ]
                        self._run_ffmpeg(cmd)
                    finally:
                        concat_file.unlink(missing_ok=True)
            else:
                self.progress.emit(10, "使用已缓存的合并视频...")

            if need_extract:
                self.progress.emit(20, "正在抽离音频...")
                cmd = [
                    "ffmpeg", "-i", merged_video_path,
                    "-vn", "-acodec", "pcm_s16le", "-ar", "16000",
                    "-ac", "1", "-f", "wav", "-y", audio_path,
                ]
                self._run_ffmpeg(cmd)
            else:
                self.progress.emit(25, "使用已缓存的音频...")

            with open(audio_path, "rb") as f:
                audio_bytes = f.read()

            config = RecognitionConfig(
                app_id=self._app_id,
                token=self._token,
                language=self._language,
                enable_punc=False,
                enable_ddc=True,
                with_speaker_info=True,
            )
            service = SubtitleRecognitionService(config)

            task_id = service._submit_task(audio_bytes)
            if not task_id:
                self.error.emit("提交语音识别任务失败，请检查API配置")
                return

            self.progress.emit(50, "正在等待识别结果...")

            start_time = time.time()
            while True:
                elapsed = time.time() - start_time
                if elapsed > config.max_wait_time:
                    self.error.emit("识别超时")
                    return

                time.sleep(config.polling_interval)
                resp_data = service._query_task(task_id)

                code = resp_data.get("code")
                message = resp_data.get("message", "")

                progress_pct = min(50 + int(elapsed / config.max_wait_time * 45), 95)
                self.progress.emit(progress_pct, f"识别中...{message}")

                if code is None:
                    continue
                if code == 2000:
                    continue
                if code == 0:
                    utterances_raw = resp_data.get("utterances", [])
                    utterances = service._parse_utterances(utterances_raw)
                    text = "".join(u.text for u in utterances)

                    self.progress.emit(100, "识别完成")

                    result_data = {
                        "text": text,
                        "utterances": [
                            {
                                "text": u.text,
                                "start_time": u.start_time,
                                "end_time": u.end_time,
                                "speaker": u.speaker,
                                "words": [
                                    {"text": w.text, "start_time": w.start_time, "end_time": w.end_time}
                                    for w in u.words
                                ],
                            }
                            for u in utterances
                        ],
                        "task_id": task_id,
                        "merged_video_path": merged_video_path,
                    }
                    self.recognition_finished.emit(result_data)
                    return

                self.error.emit(f"识别失败: code={code}, message={message}")
                return

        except RuntimeError as e:
            self.error.emit(str(e))
        except Exception as e:
            logger.exception("识别任务异常")
            self.error.emit(f"识别任务异常: {str(e)}")
