_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def format_file_size(size: int | None) -> str | None:
    """把字节数格式化为适合消息展示的 1024 进制单位。"""
    if size is None or size < 0:
        return None

    value = float(size)
    unit = _SIZE_UNITS[0]
    for unit in _SIZE_UNITS:
        if value < 1024 or unit == _SIZE_UNITS[-1]:
            break
        value /= 1024

    if unit == "B":
        return f"{size} B"

    number = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{number} {unit}"
