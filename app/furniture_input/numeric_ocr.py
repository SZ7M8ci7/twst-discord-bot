"""Bounded local Tesseract OCR. No network and no Japanese/name OCR."""

import csv
import io
import os
import re
import shutil
import subprocess
import cv2
import numpy as np


def white_on_purple(crop):
    """Remove the bar's white outer corners, retaining only enclosed white glyphs."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    purple = cv2.inRange(hsv, (125, 90, 100), (175, 255, 255))
    if np.count_nonzero(purple) / purple.size < 0.25:
        return None
    return enclosed_white_glyphs(hsv, ((120, 190), (140, 180)))


def white_on_dark(crop):
    """Extract complete white digits from a previously located shortage bar."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    if np.mean(hsv[:, :, 2] < 100) < 0.5:
        return []
    return enclosed_white_glyphs(hsv, ((100, 190), (120, 180)))


def enclosed_white_glyphs(hsv, thresholds):
    variants = []
    for saturation, brightness in thresholds:
        mask = cv2.inRange(hsv, (0, 0, brightness), (180, saturation, 255))
        _, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
        clean = np.zeros_like(mask)
        for i, (x, y, w, h, area) in enumerate(stats[1:], 1):
            if x == 0 or y == 0 or x + w == mask.shape[1] or y + h == mask.shape[0]:
                continue
            if area >= 3:
                clean[labels == i] = 255
        x, y, w, h = cv2.boundingRect(clean)
        if not w or not h:
            return []
        variants.append(255 - clean[y : y + h, x : x + w])
    return variants


class NumericOCR:
    def __init__(self, executable=None):
        self.executable = (
            executable or os.getenv("TESSERACT_CMD") or shutil.which("tesseract")
        )
        if not self.executable and os.name == "nt":
            candidate = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            if os.path.isfile(candidate):
                self.executable = candidate
        if not self.executable:
            raise RuntimeError("Tesseract is required; set TESSERACT_CMD")

    def read(
        self,
        crop,
        pattern=r"\d+",
        maximum=999999,
        *,
        single_digit=False,
        required_only=False,
        tight=False,
        display=False,
        dark_bar=False,
    ):
        if crop.size == 0:
            return None
        if display:
            return self.read_display(crop, pattern, maximum)
        variants = white_on_dark(crop) if dark_bar else white_on_purple(crop)
        border = 15 if variants is not None else 12
        if variants is None:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            if gray.mean() < 110:
                gray = 255 - gray
            gray = cv2.resize(
                gray,
                None,
                fx=3,
                fy=3,
                interpolation=cv2.INTER_LINEAR if single_digit else cv2.INTER_CUBIC,
            )
            binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
            variants = (gray, binary)
            if tight:
                variants = []
                for mask in (
                    binary,
                    cv2.threshold(gray, 160, 255, cv2.THRESH_BINARY)[1],
                ):
                    x, y, w, h = cv2.boundingRect(255 - mask)
                    # Never silently omit a clipped digit.
                    if (
                        not w
                        or not h
                        or x == 0
                        or y == 0
                        or x + w == mask.shape[1]
                        or y + h == mask.shape[0]
                    ):
                        return None
                    variants.append(mask[y : y + h, x : x + w])
        else:
            # Try bounded character heights; each must independently pass
            # the same two-mask agreement and confidence checks.
            groups = []
            for target_height in (32, 48, 24):
                resized = []
                for variant in variants:
                    scale = max(1, min(4, target_height / variant.shape[0]))
                    resized.append(
                        cv2.resize(
                            variant,
                            (
                                round(variant.shape[1] * scale),
                                round(variant.shape[0] * scale),
                            ),
                            interpolation=cv2.INTER_CUBIC,
                        )
                    )
                groups.append(resized)
            for group in groups:
                value, conflict = self._read_variants(
                    group, border, pattern, maximum, single_digit, required_only
                )
                if value is not None or conflict:
                    return value
            return None
        return self._read_variants(
            variants, border, pattern, maximum, single_digit, required_only
        )[0]

    def read_size(self, crop):
        """Normalize three complete glyphs in an already located size cell.

        Verify the middle glyph's diagonal arms before reading the whole
        expression. Both foreground thresholds must pass the same OCR checks.
        """
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        if gray.mean() < 110:
            return None
        variants = []
        for threshold in (150, 170):
            mask = (gray < threshold).astype(np.uint8) * 255
            _, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
            components = sorted(
                [(i, s) for i, s in enumerate(stats[1:], 1) if s[4] >= 3],
                key=lambda item: item[1][0],
            )
            if len(components) != 3:
                return None
            (_, left), (middle_id, middle), (_, right) = components
            x, y, w, h = cv2.boundingRect(mask)
            if (
                h < 8
                or x == 0
                or y == 0
                or x + w == mask.shape[1]
                or y + h == mask.shape[0]
                or not 0.8 <= left[3] / right[3] <= 1.25
                or abs(left[1] - right[1]) > 0.2 * h
                or not 0.5 <= middle[3] / h <= 1.2
                or not 0.65 <= middle[2] / middle[3] <= 1.5
                or left[0] + left[2] >= middle[0]
                or middle[0] + middle[2] >= right[0]
            ):
                return None
            mx, my, mw, mh, _ = middle
            yy, xx = np.where(labels[my : my + mh, mx : mx + mw] == middle_id)
            xx, yy = xx / (mw - 1), yy / (mh - 1)
            diagonal = np.minimum(abs(xx - yy), abs(xx + yy - 1)) <= 0.2
            arms = all(
                np.any(horizontal & vertical)
                for horizontal in (xx < 0.3, xx > 0.7)
                for vertical in (yy < 0.3, yy > 0.7)
            )
            if diagonal.mean() < 0.85 or not arms:
                return None
            variants.append(
                cv2.resize(
                    255 - mask[y : y + h, x : x + w],
                    (max(1, round(w * 32 / h)), 32),
                    interpolation=cv2.INTER_CUBIC,
                )
            )
        return self._read_variants(variants, 12, r"[1-9]x[1-9]", 9, False, False)[0]

    def read_display(self, crop, pattern, maximum):
        """Read the white comfort display at a bounded glyph height.

        Enlarging its entire black rectangle can make a valid 9 look like 3.
        Require both foreground masks to agree, without falling back to that
        unsafe reading when the display is blank, clipped or contradictory.
        """
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        if gray.mean() >= 110:
            return None
        variants = []
        for threshold in (190, 210):
            mask = (gray > threshold).astype(np.uint8) * 255
            x, y, w, h = cv2.boundingRect(mask)
            if (
                not w
                or h < 8
                or x == 0
                or y == 0
                or x + w == mask.shape[1]
                or y + h == mask.shape[0]
            ):
                return None
            variants.append(
                cv2.resize(
                    255 - mask[y : y + h, x : x + w],
                    (max(1, round(w * 48 / h)), 48),
                    interpolation=cv2.INTER_CUBIC,
                )
            )
        return self._read_variants(variants, 12, pattern, maximum, False, False)[0]

    def _read_variants(
        self, variants, border, pattern, maximum, single_digit, required_only
    ):
        candidates = []
        for variant in variants:
            variant = cv2.copyMakeBorder(
                variant, border, border, border, border, cv2.BORDER_CONSTANT, value=255
            )
            data = cv2.imencode(".png", variant)[1].tobytes()
            proc = subprocess.run(
                [
                    self.executable,
                    "stdin",
                    "stdout",
                    "-l",
                    "eng",
                    "--psm",
                    "10" if single_digit else "7",
                    "-c",
                    "tessedit_char_whitelist="
                    + ("123456789Il" if single_digit else "0123456789/xX,"),
                    "tsv",
                ],
                input=data,
                capture_output=True,
                timeout=8,
                check=True,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            words = [
                r
                for r in csv.DictReader(
                    io.StringIO(proc.stdout.decode()), delimiter="\t"
                )
                if r.get("text", "").strip()
            ]
            text = "".join(r["text"] for r in words).replace(",", "").replace("X", "x")
            # Restricted to a separately located one-digit size cell.
            if single_digit:
                text = text.replace("I", "1").replace("l", "1")
            if not words or not re.fullmatch(pattern, text):
                return None, False
            if min(float(r["conf"]) for r in words) < 55:
                return None, False
            if any(int(v) > maximum for v in re.findall(r"\d+", text)):
                return None, False
            if required_only:
                if not re.fullmatch(r"\d+/\d+", text):
                    return None, False
                text = text.split("/")[1]
            candidates.append(text)
        if len(candidates) != 2:
            return None, False
        if len(set(candidates)) != 1:
            # Contradictory valid readings must not be rescued by resizing.
            return None, True
        return candidates[0], False
