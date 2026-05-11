import logging
import os
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional

from clip_synth.models.narrate_project_state import NarrateProjectState
from clip_synth.services.subtitle_service import SubtitleService
from clip_synth.utils.gpu_accel import apply_gpu_encoder_to_cmd

logger = logging.getLogger("clip_synth.narrate_export_service")


def _normalize_time(t: str) -> str:
    return t.replace(",", ".")


def _build_blur_filter(
    mask_x: float,
    mask_y: float,
    mask_width: float,
    mask_height: float,
    blur_radius: int,
    video_width: int,
    video_height: int,
    feather: int = 0,
) -> str:
    """构建模糊遮罩滤镜
    使用crop裁剪出遮罩区域，模糊后叠加到原始位置，支持羽化效果
    """
    # 计算遮罩区域的绝对坐标
    x = int(video_width * mask_x)
    y = int(video_height * mask_y)
    w = int(video_width * mask_width)
    h = int(video_height * mask_height)
    
    # 如果有羽化值，需要扩展裁剪区域
    crop_x = x - feather
    crop_y = y - feather
    crop_w = w + feather * 2
    crop_h = h + feather * 2
    
    # 确保裁剪区域不超出视频边界
    crop_x = max(0, crop_x)
    crop_y = max(0, crop_y)
    crop_w = min(video_width - crop_x, crop_w)
    crop_h = min(video_height - crop_y, crop_h)
    
    # 使用复合滤镜实现带羽化的模糊遮罩
    # 使用alphamerge将遮罩作为alpha通道应用
    filter_str = (
        f"split=3[base][blur_stream][mask_stream];"
        f"[blur_stream]boxblur=luma_radius={blur_radius}:luma_power=1[blurred];"
        f"[mask_stream]drawbox=x={x}:y={y}:w={w}:h={h}:color=white:t=fill[mask];"
    )
    
    # 添加羽化效果并应用遮罩
    if feather > 0:
        # 使用gblur模糊遮罩边缘实现羽化，然后作为alpha通道
        filter_str += (
            f"[mask]gblur=sigma={feather}[mask_blur];"
            f"[blurred][mask_blur]alphamerge[masked_blur];"
            f"[base][masked_blur]overlay=x=0:y=0"
        )
    else:
        filter_str += (
            f"[blurred][mask]alphamerge[masked_blur];"
            f"[base][masked_blur]overlay=x=0:y=0"
        )
    
    return filter_str


def _time_to_seconds(t: str) -> float:
    t = t.replace(",", ".")
    parts = t.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return 0.0


def _run_cmd(cmd: List[str], action: str = "处理") -> None:
    apply_gpu_encoder_to_cmd(cmd)
    logger.info(f"[ffmpeg] {action}: {' '.join(cmd)}")
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
    _, stderr = proc.communicate()
    if proc.returncode != 0:
        error_msg = stderr.decode("utf-8", errors="replace").strip() or "未知错误"
        raise RuntimeError(f"ffmpeg {action}失败: {error_msg}")


def _get_media_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        path,
    ]
    try:
        kwargs = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, capture_output=True, text=False, timeout=30, **kwargs)
        if result.returncode == 0:
            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            if stdout:
                return float(stdout)
    except Exception as e:
        logger.warning("获取时长失败: %s", str(e))
    return 0.0


def _get_video_resolution(path: str) -> tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        path,
    ]
    try:
        kwargs = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, capture_output=True, text=False, timeout=30, **kwargs)
        if result.returncode == 0:
            output = result.stdout.decode("utf-8", errors="replace").strip()
            if output:
                parts = output.split(",")
                if len(parts) >= 2:
                    return int(parts[0]), int(parts[1])
    except Exception as e:
        logger.warning("获取视频分辨率失败: %s", str(e))
    return 1920, 1080


class NarrateExportService:
    def __init__(self, output_dir: str):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._process = None

    def _build_segment_video_map(self, project: NarrateProjectState) -> Dict[str, str]:
        mapping = {}
        for video_state in project.videos:
            for seg_list in video_state.segments.values():
                for seg in seg_list:
                    mapping[seg.id] = video_state.video_path
        return mapping

    def export(
        self,
        project: NarrateProjectState,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> str:
        scripts = project.narration_scripts
        audio_files = project.audio_files
        if not scripts:
            raise RuntimeError("没有解说文案，无法导出")

        seg_video_map = self._build_segment_video_map(project)
        output_path = str(self._output_dir / f"{project.name}_成品视频.mp4")
        raw_dir = self._output_dir / "clip_raw"
        proc_dir = self._output_dir / "clip_processed"
        sub_dir = self._output_dir / "subtitles"
        raw_dir.mkdir(parents=True, exist_ok=True)
        proc_dir.mkdir(parents=True, exist_ok=True)
        sub_dir.mkdir(parents=True, exist_ok=True)
        raw_list: List[str] = []
        final_list: List[str] = []

        # 字幕设置
        enable_subtitle = getattr(project, "enable_subtitle", False)
        subtitle_settings = {
            "font": getattr(project, "subtitle_font", "Microsoft YaHei"),
            "font_size": getattr(project, "subtitle_font_size", 24),
            "font_color": getattr(project, "subtitle_font_color", "#FFFFFF"),
            "bg_color": getattr(project, "subtitle_bg_color", "#000000"),
            "bg_opacity": getattr(project, "subtitle_bg_opacity", 50),
            "position": getattr(project, "subtitle_position", "bottom"),
            "offset_x": getattr(project, "subtitle_offset_x", 0.5),
            "offset_y": getattr(project, "subtitle_offset_y", 0.9),
        }
        logger.info(f"字幕设置: enable={enable_subtitle}, font={subtitle_settings['font']}, size={subtitle_settings['font_size']}, color={subtitle_settings['font_color']}, bg={subtitle_settings['bg_color']}, opacity={subtitle_settings['bg_opacity']}, position={subtitle_settings['position']}, offset=({subtitle_settings['offset_x']}, {subtitle_settings['offset_y']})")

        # 移除字幕（模糊遮罩）设置
        enable_remove_subtitle = getattr(project, "enable_remove_subtitle", False)
        mask_settings = {
            "mask_x": getattr(project, "mask_x", 0.2),
            "mask_y": getattr(project, "mask_y", 0.85),
            "mask_width": getattr(project, "mask_width", 0.6),
            "mask_height": getattr(project, "mask_height", 0.1),
            "blur_radius": getattr(project, "mask_blur_radius", 20),
        }
        logger.info(f"移除字幕设置: enable={enable_remove_subtitle}, mask=({mask_settings['mask_x']}, {mask_settings['mask_y']}, {mask_settings['mask_width']}, {mask_settings['mask_height']}), blur={mask_settings['blur_radius']}")

        total = len(scripts)
        try:
            # ---- 第1遍：从原视频按时间戳裁剪出所有片段（保留原声） ----
            if progress_callback:
                progress_callback("正在裁剪片段...", 5.0)

            for i, script in enumerate(scripts):
                if progress_callback:
                    progress_callback(f"裁剪第 {i+1}/{total} 个片段...", 5.0 + (i / total) * 30.0)

                video_path = seg_video_map.get(script.get("segment_id", ""))
                if not video_path:
                    for vs in project.videos:
                        video_path = vs.video_path
                        break
                if not video_path:
                    merged_path = project.extra_data.get("merged_video_path", "")
                    if merged_path and os.path.exists(merged_path):
                        video_path = merged_path
                if not video_path:
                    raise RuntimeError("无法找到片段对应的视频")

                raw_start = _normalize_time(script.get("start_time", "00:00:00"))
                raw_end = _normalize_time(script.get("end_time", "00:00:00"))
                start_sec = _time_to_seconds(raw_start)
                end_sec = _time_to_seconds(raw_end)
                if end_sec <= start_sec:
                    logger.warning(f"[导出] 片段 {i+1} 时长<=0, 跳过")
                    continue

                duration = end_sec - start_sec
                if duration < 0.5:
                    duration = 0.5

                raw_path = str(raw_dir / f"raw_{i:04d}.mp4")
                cut_cmd = [
                    "ffmpeg", "-y",
                    "-ss", raw_start,
                    "-i", video_path,
                    "-t", f"{duration}",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-avoid_negative_ts", "make_zero",
                    raw_path,
                ]
                _run_cmd(cut_cmd, f"裁剪片段 {i+1}")
                raw_list.append(raw_path)

            if not raw_list:
                raise RuntimeError("没有有效的片段可导出")

            # ---- 第2遍：每个片段单独处理 ----
            if progress_callback:
                progress_callback("正在处理片段...", 40.0)

            audio_idx = 0
            for i, script in enumerate(scripts):
                if i >= len(raw_list):
                    break

                if progress_callback:
                    progress_callback(f"处理第 {i+1}/{len(raw_list)} 个片段...", 40.0 + (i / len(raw_list)) * 45.0)

                content_type = script.get("content_type", "narration")
                raw_path = raw_list[i]
                final_path = str(proc_dir / f"final_{i:04d}.mp4")

                if content_type == "original_sound":
                    os.rename(raw_path, final_path)
                    final_list.append(final_path)
                    continue

                audio_path = None
                audio_duration = 0.0
                timestamps = []
                audio_info = None
                if audio_idx < len(audio_files):
                    audio_info = audio_files[audio_idx]
                    candidate = audio_info.get("path", "")
                    if os.path.exists(candidate):
                        audio_path = candidate
                        audio_duration = _get_media_duration(audio_path)
                        timestamps = audio_info.get("timestamps", [])
                audio_idx += 1

                if not audio_path:
                    raise RuntimeError(
                        f"解说片段 {i+1} 缺少配音文件，请检查音频文件后再试"
                    )

                # 获取视频分辨率
                video_res = _get_video_resolution(raw_path)
                video_width, video_height = video_res

                raw_duration = _get_media_duration(raw_path)
                diff = audio_duration - raw_duration

                # 生成字幕（如果启用）
                subtitle_path = None
                if enable_subtitle:
                    # 检查是否有用户上传的字幕文件（自定义配音时）
                    uploaded_subtitle = audio_info.get("subtitle_path", "") if audio_info else ""
                    if uploaded_subtitle and os.path.exists(uploaded_subtitle):
                        # 使用用户上传的字幕文件，不需要验证解说文案
                        subtitle_path = uploaded_subtitle
                        logger.info(f"使用用户上传的字幕文件: {subtitle_path}")
                    elif timestamps:
                        # 使用TTS生成的时间戳生成字幕
                        reference = script.get("narration_script", "") or script.get("text", "") or ""
                        sentences = SubtitleService.merge_words_to_sentences(timestamps, reference_text=reference)
                        if sentences:
                            subtitle_path = str(sub_dir / f"sub_{i:04d}.srt")
                            srt_content = SubtitleService.generate_srt(sentences, clip_start_time=0.0)
                            SubtitleService.save_srt(srt_content, subtitle_path)
                            logger.info(f"生成字幕文件: {subtitle_path}")

                # 构建 drawtext 滤镜（像水印文字一样直接叠加）
                sub_filter = None
                if subtitle_path:
                    offset_x = subtitle_settings.get("offset_x", 0.5)
                    offset_y = subtitle_settings.get("offset_y", 0.9)
                    position = subtitle_settings["position"]
                    if position not in ("top", "middle", "bottom"):
                        position = "bottom"
                    custom_position = f"custom:{offset_x}:{offset_y}"
                    
                    # 如果是用户上传的字幕文件，从SRT解析sentences
                    uploaded_subtitle = audio_info.get("subtitle_path", "") if audio_info else ""
                    if uploaded_subtitle and os.path.exists(uploaded_subtitle):
                        # 从用户上传的SRT文件解析字幕内容
                        with open(subtitle_path, "r", encoding="utf-8") as f:
                            srt_content = f.read()
                        sentences = SubtitleService.parse_srt(srt_content)
                        logger.info(f"从上传的SRT文件解析到 {len(sentences)} 条字幕")
                    
                    drawtext_filter = SubtitleService.build_drawtext_filter(
                        sentences,
                        clip_start_time=0.0,
                        font=subtitle_settings["font"],
                        font_size=subtitle_settings["font_size"],
                        font_color=subtitle_settings["font_color"],
                        bg_color=subtitle_settings["bg_color"],
                        bg_opacity=subtitle_settings["bg_opacity"],
                        position=custom_position,
                        video_width=video_width,
                        video_height=video_height,
                    )
                    if drawtext_filter:
                        sub_filter = drawtext_filter
                        logger.info(f"字幕滤镜(drawtext): {sub_filter[:500]}")
                    if os.path.exists(subtitle_path):
                        with open(subtitle_path, "r", encoding="utf-8") as f:
                            srt_content = f.read()
                        logger.info(f"SRT字幕内容:\n{srt_content[:500]}")

                # 构建模糊遮罩滤镜（如果启用）
                blur_filter = None
                if enable_remove_subtitle:
                    blur_filter = _build_blur_filter(
                        mask_settings["mask_x"],
                        mask_settings["mask_y"],
                        mask_settings["mask_width"],
                        mask_settings["mask_height"],
                        mask_settings["blur_radius"],
                        video_width,
                        video_height,
                        mask_settings.get("feather", 0),
                    )
                    logger.info(f"模糊遮罩滤镜: {blur_filter}")

                # 合并所有滤镜
                final_filter = None
                filters = []
                if blur_filter:
                    filters.append(blur_filter)
                if sub_filter:
                    filters.append(sub_filter)
                if filters:
                    final_filter = ",".join(filters)

                if diff > 0.5:
                    extend_path = str(raw_dir / f"ext_{i:04d}.mp4")
                    video_path = seg_video_map.get(script.get("segment_id", ""))
                    if not video_path:
                        for vs in project.videos:
                            video_path = vs.video_path
                            break
                    raw_start = _normalize_time(script.get("start_time", "00:00:00"))
                    start_sec = _time_to_seconds(raw_start)
                    extend_cmd = [
                        "ffmpeg", "-y",
                        "-ss", raw_start,
                        "-i", video_path,
                        "-t", f"{audio_duration + 0.5}",
                        "-c:v", "libx264",
                        "-preset", "ultrafast",
                        "-crf", "23",
                        "-an",
                        "-avoid_negative_ts", "make_zero",
                        extend_path,
                    ]
                    _run_cmd(extend_cmd, f"扩展片段 {i+1}")

                    # 添加滤镜（模糊遮罩 + 字幕）
                    if final_filter:
                        overlay_cmd = [
                            "ffmpeg", "-y",
                            "-i", extend_path,
                            "-i", audio_path,
                            "-filter:v", final_filter,
                            "-c:a", "aac",
                            "-b:a", "128k",
                            "-map", "0:v:0",
                            "-map", "1:a:0",
                            "-shortest",
                            final_path,
                        ]
                    else:
                        overlay_cmd = [
                            "ffmpeg", "-y",
                            "-i", extend_path,
                            "-i", audio_path,
                            "-c:v", "copy",
                            "-c:a", "aac",
                            "-b:a", "128k",
                            "-map", "0:v:0",
                            "-map", "1:a:0",
                            "-shortest",
                            final_path,
                        ]
                    _run_cmd(overlay_cmd, f"配音扩展 {i+1}")
                else:
                    if audio_duration <= 0 or raw_duration <= 0:
                        logger.warning("片段 %d: 时长信息无效 (raw=%.2f, audio=%.2f)，跳过加速处理", i + 1, raw_duration, audio_duration)
                        mute_path = str(raw_dir / f"mute_{i:04d}.mp4")
                        mute_cmd = [
                            "ffmpeg", "-y",
                            "-i", raw_path,
                            "-c:v", "copy",
                            "-an",
                            mute_path,
                        ]
                        _run_cmd(mute_cmd, f"静音 {i+1}")
                        input_video_path = mute_path
                    else:
                        speed = raw_duration / audio_duration
                        if speed > 1.0:
                            speed_path = str(raw_dir / f"speed_{i:04d}.mp4")
                            speed_cmd = [
                                "ffmpeg", "-y",
                                "-i", raw_path,
                                "-filter:v", f"setpts={1.0/speed}*PTS",
                                "-an",
                                "-c:v", "libx264",
                                "-preset", "ultrafast",
                                "-crf", "23",
                                speed_path,
                            ]
                            _run_cmd(speed_cmd, f"加速片段 {i+1}")
                            input_video_path = speed_path
                        else:
                            mute_path = str(raw_dir / f"mute_{i:04d}.mp4")
                            mute_cmd = [
                                "ffmpeg", "-y",
                                "-i", raw_path,
                                "-c:v", "copy",
                                "-an",
                                mute_path,
                            ]
                            _run_cmd(mute_cmd, f"静音 {i+1}")
                            input_video_path = mute_path

                    # 添加滤镜（模糊遮罩 + 字幕）
                    if final_filter:
                        overlay_cmd = [
                            "ffmpeg", "-y",
                            "-i", input_video_path,
                            "-i", audio_path,
                            "-filter:v", final_filter,
                            "-c:a", "aac",
                            "-b:a", "128k",
                            "-map", "0:v:0",
                            "-map", "1:a:0",
                            "-shortest",
                            final_path,
                        ]
                    else:
                        overlay_cmd = [
                            "ffmpeg", "-y",
                            "-i", input_video_path,
                            "-i", audio_path,
                            "-c:v", "copy",
                            "-c:a", "aac",
                            "-b:a", "128k",
                            "-map", "0:v:0",
                            "-map", "1:a:0",
                            "-shortest",
                            final_path,
                        ]
                    _run_cmd(overlay_cmd, f"配音合成 {i+1}")

                final_list.append(final_path)

            if not final_list:
                raise RuntimeError("没有有效的片段可导出")

            # ---- 第3遍：顺序合并所有片段 ----
            if progress_callback:
                progress_callback("正在合并所有片段...", 88.0)

            n = len(final_list)
            input_parts = []
            for fp in final_list:
                input_parts.extend(["-i", fp])

            filter_labels = "".join(f"[{j}:v][{j}:a]" for j in range(n))
            filter_complex = f"{filter_labels} concat=n={n}:v=1:a=1 [outv][outa]"

            concat_cmd = (
                ["ffmpeg", "-y"]
                + input_parts
                + [
                    "-filter_complex", filter_complex,
                    "-map", "[outv]",
                    "-map", "[outa]",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    output_path,
                ]
            )
            _run_cmd(concat_cmd, "合并所有片段")

            if progress_callback:
                progress_callback("导出完成", 100.0)

            logger.info(f"视频导出完成: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"导出失败: {e}", exc_info=True)
            raise

    def cancel(self):
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
