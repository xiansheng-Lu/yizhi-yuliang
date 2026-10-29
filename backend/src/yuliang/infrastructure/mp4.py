"""MP4时长解析：只读取容器结构中的moov/mvhd，不信任请求时长或文件声明。"""


class Mp4Error(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__("MP4时长解析失败")
        self.reason = reason


def _box(data: bytes, offset: int) -> tuple[bytes, int, int]:
    """返回(类型, 载荷起点, 载荷终点)；支持64位长度和延伸到文件尾。"""
    if offset + 8 > len(data):
        raise Mp4Error("TRUNCATED_BOX")
    size = int.from_bytes(data[offset : offset + 4], "big")
    kind = data[offset + 4 : offset + 8]
    start = offset + 8
    if size == 1:
        if offset + 16 > len(data):
            raise Mp4Error("TRUNCATED_BOX")
        size = int.from_bytes(data[offset + 8 : offset + 16], "big")
        start = offset + 16
    elif size == 0:
        size = len(data) - offset
    if size < start - offset or offset + size > len(data):
        raise Mp4Error("TRUNCATED_BOX")
    return kind, start, offset + size


def _find(data: bytes, start: int, end: int, target: bytes) -> tuple[int, int] | None:
    offset = start
    while offset < end:
        kind, payload, next_offset = _box(data, offset)
        if kind == target:
            return payload, next_offset
        if next_offset <= offset:
            raise Mp4Error("INVALID_BOX")
        offset = next_offset
    return None


def mp4_duration_seconds(data: bytes) -> int:
    """由mvhd的timescale/duration计算整秒时长，不足一秒按一秒计。"""
    if len(data) < 16:
        raise Mp4Error("TOO_SHORT")
    moov = _find(data, 0, len(data), b"moov")
    if moov is None:
        raise Mp4Error("MOOV_MISSING")
    mvhd = _find(data, moov[0], moov[1], b"mvhd")
    if mvhd is None:
        raise Mp4Error("MVHD_MISSING")
    payload = data[mvhd[0] : mvhd[1]]
    if len(payload) < 20:
        raise Mp4Error("MVHD_TRUNCATED")
    version = payload[0]
    if version == 0:
        timescale = int.from_bytes(payload[12:16], "big")
        duration = int.from_bytes(payload[16:20], "big")
    elif version == 1:
        if len(payload) < 32:
            raise Mp4Error("MVHD_TRUNCATED")
        timescale = int.from_bytes(payload[20:24], "big")
        duration = int.from_bytes(payload[24:32], "big")
    else:
        raise Mp4Error("MVHD_VERSION_UNSUPPORTED")
    if timescale <= 0 or duration <= 0:
        raise Mp4Error("MVHD_INVALID")
    # 向上取整：按实际覆盖的整秒计费，不把未满一秒的产物记为0秒。
    return max(1, (duration + timescale - 1) // timescale)
