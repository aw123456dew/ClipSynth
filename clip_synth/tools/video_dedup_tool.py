import hashlib
import logging
import os
import random
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class DedupWorker(QThread):
    progress = Signal(int, str)
    dedup_finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        video_paths: list[str],
        output_dir: str,
        frame_extract_enabled: bool,
        frame_min_frames: int,
        frame_max_frames: int,
        bitrate_enabled: bool,
        bitrate_min: float,
        bitrate_max: float,
        image_adjust_params: dict | None = None,
        crop_params: dict | None = None,
        scale_params: dict | None = None,
        move_params: dict | None = None,
        advanced_params: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._video_paths = video_paths
        self._output_dir = output_dir
        self._frame_extract_enabled = frame_extract_enabled
        self._frame_min_frames = frame_min_frames
        self._frame_max_frames = frame_max_frames
        self._bitrate_enabled = bitrate_enabled
        self._bitrate_min = bitrate_min
        self._bitrate_max = bitrate_max
        self._image_adjust_params = image_adjust_params or {}
        self._crop_params = crop_params or {}
        self._scale_params = scale_params or {}
        self._move_params = move_params or {}
        self._advanced_params = advanced_params or {}
        self._process = None

    def run(self):
        try:
            total = len(self._video_paths)
            for idx, video_path in enumerate(self._video_paths):
                name = Path(video_path).name
                base = Path(video_path).stem
                output_path = str(Path(self._output_dir) / f"{base}_dedup.mp4")

                self.progress.emit(
                    int((idx / total) * 100),
                    f"正在处理 ({idx + 1}/{total}): {name}",
                )

                logger.info("开始处理视频: %s", video_path)

                temp_dir = tempfile.mkdtemp(prefix="dedup_")

                current_input = video_path

                if self._advanced_params.get("random_mirror"):
                    current_input = self._process_random_mirror(current_input, temp_dir, base)

                if self._advanced_params.get("random_speed"):
                    current_input = self._process_random_speed(current_input, temp_dir, base)

                ffmpeg_cmd = self._build_ffmpeg_command(current_input, output_path)

                logger.info("ffmpeg 命令: %s", " ".join(ffmpeg_cmd))

                self._run_ffmpeg(ffmpeg_cmd)

                if self._advanced_params.get("metadata_clean"):
                    self._clean_metadata(output_path)

                if self._advanced_params.get("deepfake_spoof"):
                    self._inject_color_spoof(output_path)

                if self._advanced_params.get("fingerprint_check"):
                    self._check_fingerprint(video_path, output_path)

                logger.info("视频处理完成: %s -> %s", video_path, output_path)

            self.progress.emit(100, "所有视频处理完成")
            self.dedup_finished.emit(self._output_dir)
        except Exception as e:
            logger.error("处理失败: %s", str(e), exc_info=True)
            self.error.emit(str(e))

    def _get_duration(self, video_path: str) -> float | None:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        try:
            kwargs = {}
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kwargs)
            if result.returncode == 0:
                text = result.stdout.strip()
                if text:
                    return float(text)
        except Exception as e:
            logger.warning("获取视频时长失败: %s", str(e))
        return None

    def _process_random_mirror(self, input_path: str, work_dir: str, base_name: str) -> str:
        logger.info("开始随机镜像处理: %s", input_path)
        duration = self._get_duration(input_path)
        if not duration or duration < 2:
            logger.warning("视频时长过短，跳过随机镜像")
            return input_path

        segment_duration = duration / 10
        num_segments = random.randint(1, 5)
        chosen_indices = sorted(random.sample(range(10), num_segments))
        logger.info("镜像分段: 共10段, 选中 %d 段: %s", num_segments, chosen_indices)

        concat_path = os.path.join(work_dir, f"{base_name}_mirror.mp4")

        seg_files = []
        for i in range(10):
            seg_file = os.path.join(work_dir, f"seg_{i:02d}.mp4")
            seg_files.append(seg_file)
            start = i * segment_duration
            if i in chosen_indices:
                cmd_seg = [
                    "ffmpeg", "-i", input_path,
                    "-ss", str(start),
                    "-t", str(segment_duration),
                    "-vf", "hflip",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    "-y", seg_file,
                ]
            else:
                cmd_seg = [
                    "ffmpeg", "-i", input_path,
                    "-ss", str(start),
                    "-t", str(segment_duration),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    "-y", seg_file,
                ]
            self._run_ffmpeg(cmd_seg)

        concat_filter = "".join([f"[{i}:v][{i}:a]" for i in range(10)])
        concat_arg = f"{concat_filter}concat=n=10:v=1:a=1[outv][outa]"
        cmd_concat = [
            "ffmpeg",
        ]
        for sf in seg_files:
            cmd_concat.extend(["-i", sf])
        cmd_concat.extend([
            "-filter_complex", concat_arg,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-y", concat_path,
        ])
        self._run_ffmpeg(cmd_concat)

        logger.info("随机镜像处理完成: %s", concat_path)
        return concat_path

    def _process_random_speed(self, input_path: str, work_dir: str, base_name: str) -> str:
        logger.info("开始随机加速处理: %s", input_path)
        duration = self._get_duration(input_path)
        if not duration or duration < 2:
            logger.warning("视频时长过短，跳过随机加速")
            return input_path

        segment_duration = duration / 10
        num_segments = random.randint(1, 5)
        chosen_indices = sorted(random.sample(range(10), num_segments))
        logger.info("加速分段: 共10段, 选中 %d 段: %s", num_segments, chosen_indices)

        concat_path = os.path.join(work_dir, f"{base_name}_speed.mp4")

        seg_files = []
        for i in range(10):
            seg_file = os.path.join(work_dir, f"spd_{i:02d}.mp4")
            seg_files.append(seg_file)
            start = i * segment_duration
            if i in chosen_indices:
                speed = round(random.uniform(1.01, 1.10), 2)
                pts_ratio = 1.0 / speed
                cmd_seg = [
                    "ffmpeg", "-i", input_path,
                    "-ss", str(start),
                    "-t", str(segment_duration),
                    "-vf", f"setpts={pts_ratio}*PTS",
                    "-af", f"atempo={speed}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    "-y", seg_file,
                ]
                self._run_ffmpeg(cmd_seg)
                logger.info("加速段 %d: 倍率 %.2f", i, speed)
            else:
                cmd_seg = [
                    "ffmpeg", "-i", input_path,
                    "-ss", str(start),
                    "-t", str(segment_duration),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    "-y", seg_file,
                ]
                self._run_ffmpeg(cmd_seg)

        concat_filter = "".join([f"[{i}:v][{i}:a]" for i in range(10)])
        concat_arg = f"{concat_filter}concat=n=10:v=1:a=1[outv][outa]"
        cmd_concat = [
            "ffmpeg",
        ]
        for sf in seg_files:
            cmd_concat.extend(["-i", sf])
        cmd_concat.extend([
            "-filter_complex", concat_arg,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-y", concat_path,
        ])
        self._run_ffmpeg(cmd_concat)

        logger.info("随机加速处理完成: %s", concat_path)
        return concat_path

    def _clean_metadata(self, video_path: str) -> None:
        logger.info("开始清洗元数据: %s", video_path)
        temp_path = video_path + ".clean.mp4"
        cmd = [
            "ffmpeg", "-i", video_path,
            "-map_metadata", "-1",
            "-fflags", "+bitexact",
            "-flags", "+bitexact",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-map", "0:v:0", "-map", "0:a:0?",
            "-y", temp_path,
        ]
        self._run_ffmpeg(cmd)

        with open(video_path, "r+b") as f:
            header = bytearray(f.read(1024))
            for i in range(len(header)):
                if random.random() < 0.05:
                    header[i] ^= random.randint(0, 1)
            f.seek(0)
            f.write(header)

        os.replace(temp_path, video_path)
        logger.info("元数据清洗完成: %s", video_path)

    def _inject_color_spoof(self, video_path: str) -> None:
        logger.info("开始注入色彩伪装: %s", video_path)
        temp_path = video_path + ".spoof.mp4"
        src_matrix = random.choice(["bt709", "bt601", "bt2020"])
        dst_matrix = random.choice(["bt709", "bt601", "bt2020"])
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", f"colormatrix=src={src_matrix}:dst={dst_matrix}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-y", temp_path,
        ]
        self._run_ffmpeg(cmd)
        os.replace(temp_path, video_path)
        logger.info("色彩伪装完成: %s -> %s", src_matrix, dst_matrix)

    def _check_fingerprint(self, original_path: str, new_path: str) -> None:
        logger.info("开始数字指纹自检")
        orig_hash = self._file_hash(original_path)
        new_hash = self._file_hash(new_path)
        if orig_hash and new_hash:
            if orig_hash == new_hash:
                logger.warning("数字指纹差异不足，文件哈希相同: %s", orig_hash)
            else:
                logger.info("数字指纹自检通过: 原始=%s, 新文件=%s", orig_hash, new_hash)

    def _file_hash(self, file_path: str, sample_size: int = 65536) -> str | None:
        try:
            with open(file_path, "rb") as f:
                head = f.read(sample_size)
                f.seek(-sample_size, 2)
                tail = f.read(sample_size)
            return hashlib.md5(head + tail).hexdigest()
        except Exception as e:
            logger.warning("计算文件哈希失败: %s", str(e))
            return None

    def _get_video_resolution(self, video_path: str) -> tuple[int, int] | None:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            video_path,
        ]
        try:
            kwargs = {}
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kwargs)
            if result.returncode == 0:
                parts = result.stdout.strip().split(",")
                if len(parts) == 2:
                    return int(parts[0]), int(parts[1])
        except Exception as e:
            logger.warning("获取视频分辨率失败: %s", str(e))
        return None

    def _get_original_bitrate(self, video_path: str) -> int | None:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=bit_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        try:
            kwargs = {}
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kwargs)
            if result.returncode == 0:
                bitrate_str = result.stdout.strip()
                if bitrate_str and bitrate_str.isdigit():
                    return int(bitrate_str)
        except Exception as e:
            logger.warning("获取视频原码率失败: %s", str(e))
        return None

    def _build_ffmpeg_command(self, input_path: str, output_path: str) -> list[str]:
        cmd = ["ffmpeg", "-i", input_path]

        filter_chains: list[str] = []

        if self._frame_extract_enabled:
            interval = random.randint(self._frame_min_frames, self._frame_max_frames)
            drop_offset = random.randint(0, interval - 1)
            select_expr = f"not(eq(mod(n\\,{interval})\\,{drop_offset}))"
            pts_ratio = (interval - 1) / interval
            filter_chains.append(f"select={select_expr}")
            filter_chains.append(f"setpts={pts_ratio:.6f}*PTS")
            logger.info("抽帧区间: %d 帧, 删除偏移: %d, PTS缩放比: %.4f", interval, drop_offset, pts_ratio)

        if self._bitrate_enabled:
            original_bitrate = self._get_original_bitrate(input_path)
            if original_bitrate and original_bitrate > 0:
                bitrate_ratio = random.uniform(self._bitrate_min, self._bitrate_max)
                target_bitrate = int(original_bitrate * bitrate_ratio)
                cmd.extend(["-b:v", f"{target_bitrate}"])
                logger.info(
                    "原码率: %d bps, 倍率: %.2f, 目标码率: %d bps",
                    original_bitrate, bitrate_ratio, target_bitrate,
                )
            else:
                logger.warning("无法获取视频原码率，跳过码率调整: %s", input_path)

        if self._image_adjust_params.get("enabled"):
            eq_opts: list[str] = []

            brightness_range = self._image_adjust_params.get("brightness")
            if brightness_range and brightness_range != (1.0, 1.0):
                ratio = random.uniform(*brightness_range)
                brightness_val = ratio - 1.0
                eq_opts.append(f"brightness={brightness_val:.2f}")
                logger.info("亮度倍率: %.2f, brightness参数: %.2f", ratio, brightness_val)

            contrast_range = self._image_adjust_params.get("contrast")
            if contrast_range and contrast_range != (1.0, 1.0):
                ratio = random.uniform(*contrast_range)
                eq_opts.append(f"contrast={ratio:.2f}")
                logger.info("对比度倍率: %.2f", ratio)

            saturation_range = self._image_adjust_params.get("saturation")
            if saturation_range and saturation_range != (1.0, 1.0):
                ratio = random.uniform(*saturation_range)
                eq_opts.append(f"saturation={ratio:.2f}")
                logger.info("饱和度倍率: %.2f", ratio)

            if eq_opts:
                filter_chains.append("eq=" + ":".join(eq_opts))

            sharpness_range = self._image_adjust_params.get("sharpness")
            if sharpness_range and sharpness_range != (1.0, 1.0):
                ratio = random.uniform(*sharpness_range)
                filter_chains.append(f"unsharp=5:5:{ratio:.2f}:5:5:0.0")
                logger.info("锐化强度: %.2f", ratio)

            denoise_range = self._image_adjust_params.get("denoise")
            if denoise_range and denoise_range != (1.0, 1.0):
                ratio = random.uniform(*denoise_range)
                filter_chains.append(f"hqdn3d={ratio:.2f}:{ratio:.2f}:{ratio:.2f}:{ratio:.2f}")
                logger.info("降噪强度: %.2f", ratio)

            rotate_range = self._image_adjust_params.get("rotate")
            if rotate_range and rotate_range != (0, 0):
                angle = random.randint(*rotate_range)
                if angle != 0:
                    filter_chains.append(f"rotate={angle}*PI/180")
                    logger.info("旋转角度: %d 度", angle)

        if self._crop_params.get("enabled"):
            top = self._crop_params.get("top", 0)
            bottom = self._crop_params.get("bottom", 0)
            left = self._crop_params.get("left", 0)
            right = self._crop_params.get("right", 0)
            resolution = self._get_video_resolution(input_path)
            if resolution:
                orig_w, orig_h = resolution
            else:
                orig_w, orig_h = 1920, 1080
            inner_w = orig_w - left - right
            inner_h = orig_h - top - bottom
            filter_chains.append(f"crop={inner_w}:{inner_h}:{left}:{top}")
            filter_chains.append(f"pad={orig_w}:{orig_h}:(ow-iw)/2:(oh-ih)/2:black")
            logger.info("画面裁剪: 上=%d, 下=%d, 左=%d, 右=%d, 裁剪后=%dx%d, pad回原尺寸=%dx%d",
                        top, bottom, left, right, inner_w, inner_h, orig_w, orig_h)

        if self._scale_params.get("enabled"):
            min_ratio = self._scale_params.get("min_ratio", 1.0)
            max_ratio = self._scale_params.get("max_ratio", 1.0)
            if min_ratio != 1.0 or max_ratio != 1.0:
                resolution = self._get_video_resolution(input_path)
                if resolution:
                    w, h = resolution
                else:
                    w, h = 1920, 1080
                s_w = int(w * max_ratio)
                s_h = int(h * max_ratio)
                mid = (min_ratio + max_ratio) / 2
                amp = (max_ratio - min_ratio) / 2
                filter_chains.append(
                    f"zoompan=z='{mid}+{amp}*sin(2*PI*on/100)':"
                    f"d=1:fps=25:s={s_w}x{s_h}"
                )
                filter_chains.append(f"crop={w}:{h}")
                logger.info("动态缩放: 倍率范围 %.2f ~ %.2f, 原尺寸=%dx%d, zoompan输出=%dx%d, crop回原尺寸",
                            min_ratio, max_ratio, w, h, s_w, s_h)

        if self._move_params.get("enabled"):
            move_top = self._move_params.get("top", 0)
            move_bottom = self._move_params.get("bottom", 0)
            move_left = self._move_params.get("left", 0)
            move_right = self._move_params.get("right", 0)
            x_shift = move_right - move_left
            y_shift = move_bottom - move_top
            if x_shift != 0 or y_shift != 0:
                pad_x = max(0, -x_shift)
                pad_y = max(0, -y_shift)
                crop_x = max(0, x_shift)
                crop_y = max(0, y_shift)
                new_w = f"iw+{abs(x_shift)}"
                new_h = f"ih+{abs(y_shift)}"
                filter_chains.append(f"pad={new_w}:{new_h}:{pad_x}:{pad_y}:black")
                filter_chains.append(f"crop=iw-{abs(x_shift)}:ih-{abs(y_shift)}:{crop_x}:{crop_y}")
                logger.info("画面移动: x=%d, y=%d", x_shift, y_shift)

        if self._advanced_params.get("noise_inject"):
            noise_amount = random.uniform(0.001, 0.01)
            filter_chains.append(f"noise=alls={noise_amount * 100:.0f}:allf=t+u")
            logger.info("随机信号注入: noise强度=%.4f", noise_amount)

        if filter_chains:
            cmd.extend(["-vf", ",".join(filter_chains)])

        if self._frame_extract_enabled:
            cmd.extend(["-af", f"aselect={select_expr},asetpts={pts_ratio:.6f}*PTS"])

        cmd.extend(["-y", output_path])
        return cmd

    def _run_ffmpeg(self, cmd: list[str]) -> None:
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
            raise RuntimeError(f"ffmpeg 处理失败: {error_msg}")

    def cancel(self):
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass