"""上传对象实际字节探测；有界解码，不把文件名或Content-Type当真实格式。"""

from typing import BinaryIO

from PIL import Image, UnidentifiedImageError


class MediaMismatch(Exception):
    pass


def validate_media(file: BinaryIO, mime: str, size: int) -> None:
    file.seek(0)
    if mime == "text/plain":
        try:
            text = file.read(1048577).decode("utf-8")
        except UnicodeError:
            raise MediaMismatch("INVALID_UTF8") from None
        if size > 1048576 or "\x00" in text:
            raise MediaMismatch("INVALID_TEXT")
        return
    if mime.startswith("image/"):
        formats = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
        try:
            with Image.open(file) as image:
                if (
                    image.format != formats.get(mime)
                    or image.width * image.height > 16777216
                    or image.width < 1
                    or image.height < 1
                ):
                    raise MediaMismatch("INVALID_IMAGE")
                image.verify()
            file.seek(0)
            with Image.open(file) as image:
                image.load()
        except (
            UnidentifiedImageError,
            OSError,
            ValueError,
            SyntaxError,
            Image.DecompressionBombError,
        ):
            raise MediaMismatch("INVALID_IMAGE") from None
        return
    raise MediaMismatch("MEDIA_PROBE_NOT_AVAILABLE")
