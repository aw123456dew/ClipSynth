def format_time(seconds: float, format: str = "auto") -> str:
    if format == "auto":
        if seconds >= 3600:
            format = "hh:mm:ss"
        else:
            format = "mm:ss"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = round((seconds - int(seconds)) * 1000)

    if format == "hh:mm:ss":
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    elif format == "mm:ss":
        return f"{minutes:02d}:{secs:02d}"
    elif format == "hh:mm:ss.ms":
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
    else:
        return f"{minutes:02d}:{secs:02d}"


def parse_time(time_str: str) -> float:
    parts = time_str.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    elif len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    else:
        return float(parts[0])
