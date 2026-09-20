import io
import warnings
import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 12_000_000


def decode(data):
    if len(data) > MAX_BYTES:
        raise ValueError("image exceeds 15 MB")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            if (
                source.format not in {"PNG", "JPEG", "WEBP"}
                or getattr(source, "n_frames", 1) != 1
            ):
                raise ValueError("unsupported image format")
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("image exceeds 12 megapixels")
            source = ImageOps.exif_transpose(source)
            if source.height < 400 or source.width < 600:
                raise ValueError("image resolution too low")
            # Downsample before allocating OpenCV working buffers.
            source.thumbnail((2800, 1400))
            return cv2.cvtColor(np.asarray(source.convert("RGB")), cv2.COLOR_RGB2BGR)
