import hashlib
from pathlib import Path
import cv2
import numpy as np
from .image_loader import decode
from .models import Evidence, Result
from .numeric_ocr import NumericOCR

ASSETS = Path(__file__).parent / "assets"
DORMS = (
    "ハーツラビュル",
    "サバナクロー",
    "オクタヴィネル",
    "スカラビア",
    "ポムフィオーレ",
    "イグニハイド",
    "ディアソムニア",
    "ナイトレイブンカレッジ",
)


def load_template(path):
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def score(crop, template):
    if crop.shape[0] < template.shape[0] or crop.shape[1] < template.shape[1]:
        return 0.0
    return float(
        cv2.minMaxLoc(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED))[1]
    )


def smooth_score(crop, template):
    """Compare layout/shape while suppressing one-pixel resampling differences."""
    return score(
        cv2.GaussianBlur(crop, (3, 3), 0.7), cv2.GaussianBlur(template, (3, 3), 0.7)
    )


def explicit_dorm_dash(panel, template):
    crop = panel[432:462, 65:118]
    raw = score(crop, template)
    if raw < 0.80 and not (raw >= 0.75 and smooth_score(crop, template) >= 0.93):
        return False
    gray = cv2.cvtColor(panel[434:459, 87:115], cv2.COLOR_BGR2GRAY)

    def horizontal_mark(threshold):
        _, _, stats, _ = cv2.connectedComponentsWithStats(
            (gray < threshold).astype(np.uint8)
        )
        if len(stats) != 2:
            return False
        x, y, w, h, area = stats[1]
        return bool(
            4 <= x <= 10
            and 10 <= y <= 16
            and 3 <= w <= 8
            and 1 <= h <= 3
            and w >= 1.5 * h
            and area >= 0.6 * w * h
        )

    # Resampling may leave only a square dark core in a short dash. Its
    # antialiased edges must still form a horizontal mark at BOTH thresholds.
    return horizontal_mark(150) or (
        np.any(gray < 150) and horizontal_mark(170) and horizontal_mark(190)
    )


def material_badge(panel, cx, bar_y, scale=1):
    """Return limited/uncertain for a shield badge in the icon's reserved corner.

    A shield alone prevents ordinary-material output; a readable white
    exclamation mark is additionally required to use it as shortage evidence.
    """
    corner = panel[
        bar_y - 46 * scale : bar_y - 22 * scale, cx + 8 * scale : cx + 32 * scale
    ]
    if corner.shape[:2] != (24 * scale, 24 * scale):
        return None
    hsv = cv2.cvtColor(corner, cv2.COLOR_BGR2HSV)
    warm = (
        ((hsv[:, :, 0] <= 38) | (hsv[:, :, 0] >= 170))
        & (hsv[:, :, 1] > 90)
        & (hsv[:, :, 2] > 130)
    ).astype(np.uint8)
    # Older event materials use a muted blue-grey shield. Keep this separate
    # from warm badges so ordinary grey metal/black icon outlines cannot pass
    # on colour alone: the candidate must also have the shield's tapered base.
    cool = (
        (hsv[:, :, 0] >= 90)
        & (hsv[:, :, 0] <= 130)
        & (hsv[:, :, 1] >= 10)
        & (hsv[:, :, 1] <= 100)
        & (hsv[:, :, 2] >= 55)
        & (hsv[:, :, 2] <= 180)
    ).astype(np.uint8)
    candidates = [(s, False) for s in cv2.connectedComponentsWithStats(warm)[2][1:]]
    candidates.extend((s, True) for s in cv2.connectedComponentsWithStats(cool)[2][1:])
    uncertain = False
    for (x, y, w, h, area), grey in candidates:
        if not (
            7 * scale <= w <= 19 * scale
            and 9 * scale <= h <= 21 * scale
            and area >= 0.45 * w * h
        ):
            continue
        if grey:
            shape = cool[y : y + h, x : x + w]
            top_width = np.count_nonzero(shape[h // 4], axis=0)
            base_width = np.count_nonzero(shape[-2 * scale :], axis=1).max()
            if y > 8 * scale or top_width < 0.65 * w or base_width > 0.7 * w:
                continue
        uncertain = True
        region = hsv[y : y + h, x : x + w]
        white = ((region[:, :, 1] < 65) & (region[:, :, 2] > 220)).astype(np.uint8)
        glyphs = cv2.connectedComponentsWithStats(white)[2][1:].astype(float)
        glyphs[:, :4] /= scale
        glyphs[:, 4] /= scale * scale
        w, h = w / scale, h / scale
        for sx, sy, sw, sh, sa in glyphs:
            if not (
                1 <= sw <= 5
                and 3 <= sh <= 0.65 * h
                and sa >= 3
                and sy >= 1
                and sy + sh <= 0.8 * h
                and 0.3 * w <= sx + sw / 2 <= 0.7 * w
            ):
                continue
            if any(
                1 <= dw <= 4
                and 1 <= dh <= 3
                and da >= 1
                and 1 <= dy - (sy + sh) <= 5
                and abs(dx + dw / 2 - (sx + sw / 2)) <= 2.5
                for dx, dy, dw, dh, da in glyphs
            ):
                return "limited"
    return "uncertain" if uncertain else None


class Recognizer:
    def __init__(self, ocr=None):
        cv2.setNumThreads(1)
        self.ocr = ocr or NumericOCR()
        self.templates = {p.stem: load_template(p) for p in ASSETS.glob("*.png")}
        self.highres = {}
        self.raw_assets = {
            p.stem: load_template(p) for p in ASSETS.parent.parent.glob("*.png")
        }

    def raw_icon(self, crop, names, source, box, scales, threshold=0.90):
        ranks = []
        for name in names:
            template = self.raw_assets.get(name)
            if template is None:
                continue
            best = max(
                score(crop, cv2.resize(template, None, fx=s, fy=s)) for s in scales
            )
            ranks.append((best, name))
        ranks.sort(reverse=True)
        if not ranks:
            return Evidence(state="unreadable", reason="テンプレート未登録")
        best, name = ranks[0]
        margin = best - (ranks[1][0] if len(ranks) > 1 else 0)
        accepted = best >= threshold and margin >= 0.15
        method = "template:existing-assets"
        if not accepted and best >= 0.85 and margin >= 0.15:
            smoothed = sorted(
                (
                    (
                        max(
                            smooth_score(
                                crop, cv2.resize(self.raw_assets[n], None, fx=s, fy=s)
                            )
                            for s in scales
                        ),
                        n,
                    )
                    for _, n in ranks
                ),
                reverse=True,
            )
            smooth_best, smooth_name = smoothed[0]
            smooth_margin = smooth_best - (smoothed[1][0] if len(smoothed) > 1 else 0)
            accepted = (
                smooth_name == name and smooth_best >= 0.93 and smooth_margin >= 0.18
            )
            if accepted:
                method = "template:raw-and-smoothed"
        return Evidence(
            name if accepted else None,
            "recognized" if accepted else "unreadable",
            source,
            method,
            box,
            best,
            f"margin={margin:.3f}",
        )

    def classify(
        self, crop, prefix, source, box, threshold=0.90, *, smooth_fallback=False
    ):
        candidates = sorted(
            (
                (score(crop, template), name[len(prefix) :])
                for name, template in self.templates.items()
                if name.startswith(prefix)
            ),
            reverse=True,
        )
        if not candidates:
            return Evidence(
                state="unreadable", source=source, reason="テンプレート未登録"
            )
        best, value = candidates[0]
        margin = best - (candidates[1][0] if len(candidates) > 1 else 0)
        accepted = best >= threshold and margin >= 0.06
        method = "template:v1"
        if not accepted and smooth_fallback and best >= 0.85 and margin >= 0.10:
            smoothed = sorted(
                (smooth_score(crop, t), n[len(prefix) :])
                for n, t in self.templates.items()
                if n.startswith(prefix)
            )
            smooth_best, smooth_value = smoothed[-1]
            smooth_margin = smooth_best - (smoothed[-2][0] if len(smoothed) > 1 else 0)
            # Resizing can change one-pixel edges in a small theme glyph. Both
            # comparisons must independently identify the same separated winner.
            accepted = (
                smooth_value == value and smooth_best >= 0.93 and smooth_margin >= 0.12
            )
            if accepted:
                method = "template:raw-and-smoothed"
        return Evidence(
            value if accepted else None,
            "recognized" if accepted else "unreadable",
            source,
            method,
            box,
            best,
            f"margin={margin:.3f}",
        )

    def panels(self, image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        mask = (gray > 205).astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = sorted(
            (cv2.boundingRect(c) for c in contours),
            key=lambda b: b[2] * b[3],
            reverse=True,
        )
        # A hairline scratch across the panel can split the outer white border.
        # Try the untouched contours first; repaired candidates remain subject
        # to the same dimensions and text-anchor checks, using original pixels.
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        repaired, _ = cv2.findContours(
            closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        fallback = sorted(
            (cv2.boundingRect(c) for c in repaired),
            key=lambda b: b[2] * b[3],
            reverse=True,
        )[:4]
        candidates = boxes[:4] + [b for b in fallback if b not in boxes[:4]]
        for x, y, w, h in candidates:
            if w < 500 or h < 300 or w * h < image.shape[0] * image.shape[1] * 0.25:
                continue
            ratio = w / h
            if 1.66 < ratio < 1.77:
                panel = cv2.resize(image[y : y + h, x : x + w], (832, 486))
                if (
                    score(panel[68:120, 395:580], self.templates["craft_anchor"])
                    >= 0.92
                ):
                    yield "craft", panel, (x, y, w, h)
                    return
                if (
                    score(panel[68:120, 595:780], self.templates["detail_tab_anchor"])
                    >= 0.92
                ):
                    yield "detail", panel, (x, y, w, h)
                    return
                if score(
                    panel[88:119, 380:475], self.templates["detail_anchor"]
                ) >= 0.88 or (
                    score(panel[88:119, 380:475], self.templates["detail_anchor"])
                    >= 0.75
                    and smooth_score(
                        panel[88:119, 380:475], self.templates["detail_anchor"]
                    )
                    >= 0.92
                    and self.classify(panel[16:58, 44:130], "rarity_", "", ()).accepted
                ):
                    yield "detail", panel, (x, y, w, h)
                    return
            legacy_status = 1.92 < ratio < 1.99
            if 2.00 < ratio < 2.12 or legacy_status:
                panel = cv2.resize(
                    image[y : y + h, x : x + w],
                    (794, 406 if legacy_status else 386),
                )
                if (
                    max(
                        score(
                            panel[22:55, 20:170]
                            if legacy_status
                            else panel[12:45, 20:170],
                            t,
                        )
                        for n, t in self.templates.items()
                        if n.startswith("status_anchor")
                    )
                    >= 0.88
                ):
                    yield "status", panel, (x, y, w, h)
                    return

    def dorm_crest(self, crop, source, box):
        # Status icons include every dorm. Grayscale removes the inactive tint;
        # shape, absolute similarity and separation from other crests must agree.
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        ranks = []
        for name, template in self.templates.items():
            if not name.startswith("crest_"):
                continue
            template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            best = max(
                score(crop, cv2.resize(template, None, fx=s, fy=s))
                for s in np.arange(0.50, 0.81, 0.01)
            )
            ranks.append((best, name.removeprefix("crest_")))
        ranks.sort(reverse=True)
        if len(ranks) < 2:
            return Evidence(state="unreadable", source=source, reason="寮紋章未登録")
        best, name = ranks[0]
        margin = best - ranks[1][0]
        accepted = best >= 0.94 and margin >= 0.10
        return Evidence(
            name if accepted else None,
            "recognized" if accepted else "unreadable",
            source,
            "template:crest-shape",
            box,
            best,
            f"margin={margin:.3f}",
        )

    def number(self, panel, box, source, pattern=r"\d+", maximum=999999, **options):
        x1, y1, x2, y2 = box
        if id(panel) in self.highres:
            crop = self.highres[id(panel)][y1 * 2 : y2 * 2, x1 * 2 : x2 * 2]
        else:
            crop = panel[y1:y2, x1:x2]
        text = self.ocr.read(crop, pattern, maximum, **options)
        if text is None and id(panel) in self.highres:
            text = self.ocr.read(panel[y1:y2, x1:x2], pattern, maximum, **options)
        return Evidence(
            text,
            "recognized" if text is not None else "unreadable",
            source,
            "numeric_ocr:tesseract",
            box,
            reason="二通りの前処理で一致" if text is not None else "OCR不一致・低信頼",
        )

    def dimensions(self, panel, source, offset=0):
        size = self.number(
            panel, (262 + offset, 360, 307 + offset, 386), source, r"[1-9]x[1-9]", 9
        )
        if (
            not size.accepted
            and max(
                score(panel[358:388, 260 + offset : 309 + offset], t)
                for n, t in self.templates.items()
                if n.startswith("size_1x1")
            )
            >= 0.97
        ):
            size = Evidence(
                "1x1",
                "recognized",
                source,
                "template:numeric-fallback",
                (260 + offset, 358, 309 + offset, 388),
            )
        if (
            not size.accepted
            and score(
                cv2.GaussianBlur(
                    panel[362:384, 276 + offset : 289 + offset], (3, 3), 0.8
                ),
                cv2.GaussianBlur(self.templates["size_multiply"], (3, 3), 0.8),
            )
            >= 0.92
        ):
            # Tesseract often drops the multiplication sign or sees a narrow 1 as I.
            # Read two one-digit cells only after verifying the separator's image.
            hi = self.highres.get(id(panel), cv2.resize(panel, None, fx=2, fy=2))
            left = self.ocr.read(
                hi[720:772, 524 + offset * 2 : 554 + offset * 2],
                r"[1-9]",
                9,
                single_digit=True,
            )
            right = self.ocr.read(
                hi[720:772, 576 + offset * 2 : 614 + offset * 2],
                r"[1-9]",
                9,
                single_digit=True,
            )
            if left and right:
                size = Evidence(
                    f"{left}x{right}",
                    "recognized",
                    source,
                    "numeric_ocr:split-dimensions",
                    (262 + offset, 360, 307 + offset, 386),
                )
        if not size.accepted:
            return self.normalized_dimensions(panel, source, offset)
        if size.accepted:
            a, b = map(int, size.value.split("x"))
            size.value = a * b
        return size

    def normalized_dimensions(self, panel, source, offset=0):
        hi = self.highres.get(id(panel), cv2.resize(panel, None, fx=2, fy=2))
        normalized = self.ocr.read_size(
            hi[720:772, 524 + offset * 2 : 614 + offset * 2]
        )
        if normalized is None:
            return Evidence(
                state="unreadable", source=source, reason="マス数の形状・OCR不一致"
            )
        a, b = map(int, normalized.split("x"))
        return Evidence(
            a * b,
            "recognized",
            source,
            "numeric_ocr:normalized-size",
            (262 + offset, 360, 307 + offset, 386),
            reason="3文字の形状確認と二通りの前処理で一致",
        )

    def detail(self, panel, source, result):
        result.add(
            "G",
            self.classify(panel[16:58, 44:130], "rarity_", source, (44, 16, 130, 58)),
        )
        for column, x in (("N", 100), ("O", 237)):
            evidence = self.classify(
                panel[395:434, x - 18 : x + 18],
                "theme_",
                source,
                (x - 18, 395, x + 18, 434),
                smooth_fallback=True,
            )
            if not evidence.accepted:
                evidence = self.raw_icon(
                    panel[395:434, x - 18 : x + 18],
                    (
                        "スタイリッシュ",
                        "ベーシック",
                        "エレガント",
                        "ポップ",
                        "ユニーク",
                    ),
                    source,
                    (x - 18, 395, x + 18, 434),
                    np.arange(0.30, 0.60, 0.01),
                )
            result.add(column, evidence)
        if result.fields["N"].accepted and not result.fields["O"].accepted:
            gray = cv2.cvtColor(panel[398:429, 216:357], cv2.COLOR_BGR2GRAY)
            if np.count_nonzero(gray < 150) == 0:
                result.add(
                    "O",
                    Evidence(
                        "－",
                        "explicit_none",
                        source,
                        "empty-theme-slot:v1",
                        (216, 398, 357, 429),
                    ),
                )
        # 'none' includes its label; a blank/occluded icon is never interpreted as none.
        if explicit_dorm_dash(panel, self.templates["dorm_なし"]):
            result.add(
                "P",
                Evidence(
                    "なし", "explicit_none", source, "template:v1", (65, 432, 118, 462)
                ),
            )
        else:
            dorm = self.classify(
                panel[428:468, 85:116], "dorm_", source, (85, 428, 116, 468)
            )
            if not dorm.accepted:
                dorm = self.dorm_crest(
                    panel[428:468, 85:116], source, (85, 428, 116, 468)
                )
            result.add("P", dorm)
        size = self.dimensions(panel, source)
        shifted_label = score(
            cv2.GaussianBlur(panel[361:386, 195:242], (5, 5), 1.2),
            cv2.GaussianBlur(self.templates["size_label"], (5, 5), 1.2),
        )
        if not size.accepted and (
            shifted_label >= 0.92
            or (
                shifted_label >= 0.91
                and score(panel[361:386, 195:242], self.templates["size_label"]) >= 0.74
            )
        ):
            size = self.dimensions(panel, source, offset=-20)
        if not size.accepted and shifted_label >= 0.91:
            # This fallback requires all three glyphs and a genuine ×, rather
            # than accepting a weaker label match with ordinary numeric OCR.
            size = self.normalized_dimensions(panel, source, offset=-20)
        result.add("I", size)
        limit = self.number(panel, (133, 360, 175, 386), source, r"\d+/\d+", 999)
        if not limit.accepted:
            limit = self.number(panel, (145, 360, 185, 386), source, r"\d+/\d+", 999)
        if limit.accepted:
            owned, cap = map(int, limit.value.split("/"))
            if cap < 1 or owned > cap:
                limit = Evidence(state="unreadable", reason="所持数/上限が不正")
            else:
                limit.value = cap
        result.add("R", limit)

    def craft(self, panel, source, result):
        self.detail(panel, source, result)
        count = self.number(panel, (522, 311, 580, 340), source, r"\d+/\d+", 999)
        # Prices and materials can be multiplied by the selected craft quantity.
        shortage = count.accepted and count.value == "0/0"
        if not count.accepted or not (count.value.startswith("1/") or shortage):
            result.reasons.append("作成数1を確認できないため材料・マドルを保留")
            return
        hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([125, 90, 100]), np.array([175, 255, 255]))
        # Black shortage bars touch the icon outline. Isolate their horizontal
        # top/bottom edges before joining them, so icons cannot enlarge the box.
        dark = (cv2.cvtColor(panel, cv2.COLOR_BGR2GRAY) < 65).astype(np.uint8) * 255
        thin = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((1, 25), np.uint8))
        thin = cv2.morphologyEx(thin, cv2.MORPH_CLOSE, np.ones((15, 1), np.uint8))
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 25), np.uint8))
        dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((15, 1), np.uint8))
        mask |= dark
        mask[:135] = 0
        mask[260:] = 0
        mask[:, :390] = 0
        boxes = []
        # Keep established boxes unchanged. A one-pixel edge only supplies a
        # missing bar; merging both masks would alter working OCR crops.
        for candidate in (mask, thin):
            candidate[:135] = 0
            candidate[260:] = 0
            candidate[:, :390] = 0
            contours, _ = cv2.findContours(
                candidate, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if not (35 <= w <= 95 and 10 <= h <= 25):
                    continue
                if any(
                    abs(x + w / 2 - (bx + bw / 2)) < 8 and abs(y - by) < 5
                    for bx, by, bw, _ in boxes
                ):
                    continue
                boxes.append((x, y, w, h))
        columns = {
            "木": "Y",
            "枝": "AA",
            "石": "AC",
            "鉱石": "AE",
            "金属": "AG",
            "ガラス": "AI",
            "粘土": "AK",
            "布": "AM",
            "紙": "AO",
        }
        found = {}
        confirmed_shortage = False
        for x, y, w, h in boxes:
            cx = x + w // 2
            box = (cx - 29, y - 54, cx + 29, y + 3)
            badge = material_badge(panel, cx, y)
            if badge == "uncertain" and id(panel) in self.highres:
                if (
                    material_badge(self.highres[id(panel)], cx * 2, y * 2, scale=2)
                    == "limited"
                ):
                    badge = "limited"
            material = Evidence(state="unreadable")
            if badge is None:
                material = self.classify(
                    panel[y - 54 : y + 3, cx - 29 : cx + 29],
                    "material_",
                    source,
                    box,
                    0.94,
                )
                if not material.accepted:
                    material = self.raw_icon(
                        panel[y - 54 : y + 3, cx - 29 : cx + 29],
                        columns,
                        source,
                        box,
                        np.arange(0.20, 0.52, 0.01),
                    )
            is_dark = np.mean(hsv[y : y + h, x : x + w, 2] < 100) > 0.5
            if not material.accepted and not (
                shortage and is_dark and badge == "limited"
            ):
                continue
            quantity = self.number(
                panel,
                (x, y - 1, x + w, y + h + 2),
                source,
                r"\d+/\d+",
                999999,
                required_only=not is_dark,
                dark_bar=is_dark,
            )
            if quantity.accepted:
                if is_dark:
                    owned, required = map(int, quantity.value.split("/"))
                    if owned >= required:
                        continue
                    confirmed_shortage = True
                    quantity.value = str(required)
                if not material.accepted:
                    continue
                quantity.value = int(quantity.value)
                if quantity.value < 1:
                    continue
                column = columns[material.value]
                if column in found:
                    found[column] = Evidence(
                        state="conflict", reason="同じ材料の複数スロット"
                    )
                else:
                    found[column] = quantity
        if shortage:
            disabled = all(
                np.mean(
                    (hsv[425:438, x : x + 18, 1] < 25)
                    & (hsv[425:438, x : x + 18, 2] > 160)
                    & (hsv[425:438, x : x + 18, 2] < 225)
                )
                >= 0.95
                for x in (407, 750)
            )
            if not confirmed_shortage or not disabled:
                result.reasons.append(
                    "作成数0/0の材料不足・無効ボタンを確認できないため保留"
                )
                return
        madol = self.number(panel, (537, 359, 598, 388), source, maximum=1000000)
        if madol.accepted:
            madol.value = int(madol.value)
        result.add("U", madol)
        for column, evidence in found.items():
            result.add(column, evidence)

    def status(self, image, panel, bounds, source):
        x, y, w, h = bounds
        legacy = panel.shape[0] == 406
        factor = w / 794
        top = round(y - 108 * factor)
        if top < 0:
            return None
        height = 108 + panel.shape[0]
        full = cv2.resize(image[top : y + h, x : x + w], (794, height))
        self.highres[id(full)] = cv2.resize(
            image[top : y + h, x : x + w], (1588, height * 2)
        )
        comfort = self.number(
            full,
            (278, 42, 388, 94) if legacy else (110, 42, 240, 94),
            source,
            maximum=9999,
            display=True,
        )
        if legacy:
            # Older screens have 10 extra pixels above/below the same content;
            # preserve its scale instead of squeezing the taller panel.
            hi = self.highres.get(id(panel))
            panel = panel[10:396]
            if hi is not None:
                self.highres[id(panel)] = hi[20:792]
        if comfort.accepted:
            comfort.value = int(comfort.value)
        dorm_points = []
        for i in range(8):
            ev = self.number(
                panel, (415 + i * 43, 339, 438 + i * 43, 366), source, maximum=999
            )
            if not ev.accepted:
                ev = self.number(
                    panel,
                    (415 + i * 43, 344, 438 + i * 43, 366),
                    source,
                    maximum=999,
                    tight=True,
                )
            if not ev.accepted and self.zero_number(
                panel[336:368, 413 + i * 43 : 441 + i * 43]
            ):
                ev = Evidence("0", "recognized", source, "template:numeric-fallback")
            dorm_points.append(int(ev.value) if ev.accepted else None)
        # Validate the visible baseline series card, including its installed count.
        baseline_rows = []
        for row in range(4):
            yy = 71 + row * 81
            if yy + 62 > 350:
                break
            if (
                max(
                    score(panel[yy - 5 : yy + 25, 60:255], t)
                    for n, t in self.templates.items()
                    if n.startswith("baseline")
                )
                >= 0.91
            ):
                count = self.number(
                    panel, (208, yy + 32, 249, yy + 57), source, r"\d+/\d+", 99
                )
                points = self.number(
                    panel, (293, yy + 32, 325, yy + 57), source, maximum=99
                )
                if (
                    count.accepted
                    and count.value == "2/6"
                    and points.accepted
                    and points.value == "14"
                ):
                    baseline_rows.append(row)
        # Initial supported measurement layout: baseline only or one extra series card.
        bonus = None
        if baseline_rows == [0]:
            # The entire remaining list must be the flat empty-list background.
            region = panel[143:350, 46:360]
            edges = cv2.Canny(region, 70, 150)
            if np.count_nonzero(edges) / edges.size < 0.007:
                bonus = 0
        elif baseline_rows == [1]:
            count = self.number(panel, (208, 103, 249, 128), source, r"\d+/\d+", 99)
            points = self.number(panel, (293, 103, 325, 128), source, maximum=999)
            if not points.accepted:
                points = self.number(
                    panel, (293, 103, 325, 128), source, maximum=999, tight=True
                )
            region = panel[226:350, 46:360]
            if (
                count.accepted
                and count.value.startswith("1/")
                and points.accepted
                and np.count_nonzero(cv2.Canny(region, 70, 150))
                / region.shape[0]
                / region.shape[1]
                < 0.007
            ):
                bonus = int(points.value)
        return {
            "source": source,
            "comfort": comfort,
            "bonus": bonus,
            "dorm_points": dorm_points,
        }

    def zero_number(self, crop):
        """Verify the whole zero cell, optionally excluding the crest above it."""
        return any(
            score(crop, t) >= 0.93
            or (
                score(crop[8:], t[8:]) >= 0.92 and smooth_score(crop[8:], t[8:]) >= 0.96
            )
            for n, t in self.templates.items()
            if n.startswith("zero")
        )

    def analyze(self, images, *, category=None):
        result = Result()
        statuses = []
        seen = set()
        for data in images:
            source = hashlib.sha256(data).hexdigest()
            if source in seen:
                continue
            seen.add(source)
            try:
                image = decode(data)
                screens = list(self.panels(image))
                if not screens:
                    result.screens.append({"source": source, "type": "unknown"})
                for kind, panel, bounds in screens:
                    x, y, w, h = bounds
                    self.highres = {
                        id(panel): cv2.resize(
                            image[y : y + h, x : x + w],
                            (panel.shape[1] * 2, panel.shape[0] * 2),
                        )
                    }
                    result.screens.append(
                        {"source": source, "type": kind, "bounds": bounds}
                    )
                    if kind == "craft":
                        self.craft(panel, source, result)
                    elif kind == "detail":
                        self.detail(panel, source, result)
                    else:
                        status = self.status(image, panel, bounds, source)
                        if status:
                            statuses.append(status)
            except (ValueError, OSError) as exc:
                result.reasons.append(type(exc).__name__ + ": invalid image")
            finally:
                self.highres = {}
        dorm = result.fields.get("P", Evidence())
        baseline_points = (
            27 if category in {"内観・外観：床", "内観・外観：壁紙"} else 36
        )
        groups = []
        for status in statuses:
            if (
                not dorm.accepted
                or not status["comfort"].accepted
                or status["bonus"] is None
            ):
                continue
            points = status["dorm_points"]
            if any(v is None for v in points):
                continue
            if dorm.value == "なし":
                value = (
                    0
                    if points[:7] == [0] * 7 and points[7] == baseline_points
                    else None
                )
            elif dorm.value in DORMS:
                idx = DORMS.index(dorm.value)
                value = points[idx]
                if any(
                    p != (baseline_points if i == 7 else 0)
                    for i, p in enumerate(points)
                    if i != idx
                ):
                    value = None
            else:
                value = None
            if value is not None:
                groups.append(
                    (status["comfort"].value, status["bonus"], value, status["source"])
                )
        if groups and len({g[:3] for g in groups}) == 1:
            d, e, f, source = groups[0]
            for key, value in zip("DEF", (d, e, f)):
                result.add(
                    key,
                    Evidence(value, "recognized", source, "numeric_ocr+baseline:v1"),
                )
        elif groups:
            for key in "DEF":
                result.fields[key] = Evidence(
                    state="conflict", reason="測定画像の数値組が不一致"
                )
        elif statuses:
            result.reasons.append("D/E/Fは同一画像の測定条件・数値が揃わないため保留")
        return result
