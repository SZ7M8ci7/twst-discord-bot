import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from app.furniture_input.numeric_ocr import NumericOCR, white_on_purple
from app.furniture_input.models import Evidence, Result
from app.furniture_input.recognizer import (
    ASSETS,
    Recognizer,
    explicit_dorm_dash,
    load_template,
    material_badge,
)

CROPS = Path(__file__).parent / "fixtures/recognition_crops"


def tsv(text, confidence=90):
    return SimpleNamespace(stdout=f"text\tconf\n{text}\t{confidence}\n".encode())


class NumericAgreementTests(unittest.TestCase):
    def setUp(self):
        self.ocr = NumericOCR(executable="mock-tesseract")
        self.crop = np.full((30, 90, 3), 240, np.uint8)
        cv2.putText(
            self.crop, "3/5", (3, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1
        )

    def test_material_ignores_owned_count_disagreement_only(self):
        with patch("subprocess.run", side_effect=[tsv("316/11"), tsv("336/11")]):
            self.assertEqual(
                self.ocr.read(self.crop, r"\d+/\d+", required_only=True), "11"
            )
        with patch("subprocess.run", side_effect=[tsv("316/11"), tsv("336/11")]):
            self.assertIsNone(self.ocr.read(self.crop, r"\d+/\d+"))

    def test_required_count_disagreement_is_held(self):
        with patch("subprocess.run", side_effect=[tsv("316/11"), tsv("316/17")]):
            self.assertIsNone(self.ocr.read(self.crop, r"\d+/\d+", required_only=True))

    def test_low_confidence_and_missing_separator_are_held(self):
        for text, confidence in [("316/11", 54), ("31611", 95)]:
            with patch("subprocess.run", return_value=tsv(text, confidence)):
                self.assertIsNone(
                    self.ocr.read(self.crop, r"\d+/\d+", required_only=True)
                )

    def test_letter_one_mapping_is_limited_to_isolated_size_digit(self):
        with patch("subprocess.run", side_effect=[tsv("I"), tsv("l")]):
            self.assertEqual(
                self.ocr.read(self.crop, r"[1-9]", 9, single_digit=True), "1"
            )
        with patch("subprocess.run", return_value=tsv("I")):
            self.assertIsNone(self.ocr.read(self.crop))

    def test_empty_purple_bar_does_not_invoke_ocr(self):
        crop = np.full((30, 100, 3), (200, 20, 200), np.uint8)
        self.assertEqual(white_on_purple(crop), [])
        with patch("subprocess.run") as run:
            self.assertIsNone(self.ocr.read(crop))
            run.assert_not_called()

    def test_normalized_size_requires_agreement_and_confidence(self):
        crop = load_template(CROPS / "size-five-ortho.png")
        for outputs in (
            [tsv("5X5"), tsv("3X5")],
            [tsv("5X5"), tsv("5X5", 54)],
            [tsv("5X5"), tsv("55")],
        ):
            with patch("subprocess.run", side_effect=outputs):
                self.assertIsNone(self.ocr.read_size(crop))

    def test_normalized_size_rejects_incomplete_or_non_multiply_glyphs(self):
        crop = load_template(CROPS / "size-five-ortho.png")
        mask = (cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) < 150).astype(np.uint8)
        x, y, w, h = cv2.boundingRect(mask)
        stats = sorted(
            cv2.connectedComponentsWithStats(mask)[2][1:], key=lambda s: s[0]
        )
        mx, my, mw, mh, _ = stats[1]
        blank_middle = crop.copy()
        blank_middle[:, mx : mx + mw] = 240
        plus = blank_middle.copy()
        cv2.line(plus, (mx, my + mh // 2), (mx + mw - 1, my + mh // 2), (0, 0, 0), 2)
        cv2.line(plus, (mx + mw // 2, my), (mx + mw // 2, my + mh - 1), (0, 0, 0), 2)
        for invalid in (
            np.full_like(crop, 240),
            np.zeros_like(crop),
            crop[y : y + h, x : x + w],
            blank_middle,
            plus,
        ):
            with patch("subprocess.run") as run:
                self.assertIsNone(self.ocr.read_size(invalid))
                run.assert_not_called()

    def test_tight_crop_rejects_truncated_text(self):
        self.crop[:, :5] = 0
        with patch("subprocess.run") as run:
            self.assertIsNone(self.ocr.read(self.crop, tight=True))
            run.assert_not_called()

    def test_white_count_conflict_does_not_retry_another_scale(self):
        crop = load_template(CROPS / "cloth-count-ruggie.png")
        with patch("subprocess.run", side_effect=[tsv("342/11"), tsv("342/17")]) as run:
            self.assertIsNone(self.ocr.read(crop, r"\d+/\d+", required_only=True))
            self.assertEqual(run.call_count, 2)


class IconTests(unittest.TestCase):
    def setUp(self):
        self.recognizer = Recognizer(ocr=Mock())

    def test_blank_and_unknown_crests_are_held(self):
        for crop in [
            np.full((40, 31, 3), 240, np.uint8),
            np.zeros((40, 31, 3), np.uint8),
        ]:
            self.assertFalse(
                self.recognizer.dorm_crest(crop, "test", (0, 0, 31, 40)).accepted
            )

    def test_none_requires_visible_dash_not_blank(self):
        template = self.recognizer.templates["dorm_なし"]
        panel = np.full((486, 832, 3), 240, np.uint8)
        self.assertFalse(explicit_dorm_dash(panel, template))
        for name, crest in self.recognizer.templates.items():
            if not name.startswith("crest_"):
                continue
            test = panel.copy()
            test[432:457, 90:111] = cv2.resize(crest, (21, 25))
            self.assertFalse(explicit_dorm_dash(test, template), name)

    def test_templates_have_provenance_and_exclude_validation_images(self):
        provenance = json.loads(
            (ASSETS / "provenance.json").read_text(encoding="utf-8")
        )
        listed = {entry["file"] for entry in provenance["templates"]}
        self.assertEqual(listed, {p.name for p in ASSETS.glob("*.png")})
        cases = json.loads(
            (Path(__file__).parent / "fixtures/furniture_samples.json").read_text(
                encoding="utf-8"
            )
        )
        evaluation_ids = {
            i for c in cases if c["split"] != "calibration" for i in c["images"]
        }
        self.assertFalse(
            evaluation_ids
            & {e["source_attachment_id"] for e in provenance["templates"]}
        )

    def test_resampled_stone_and_ore_agree_in_both_comparisons(self):
        names = ("木", "枝", "石", "鉱石", "金属", "ガラス", "粘土", "布", "紙")
        for file, expected in (("stone", "石"), ("ore", "鉱石")):
            evidence = self.recognizer.raw_icon(
                load_template(CROPS / f"{file}.png"),
                names,
                "test",
                (),
                np.arange(0.20, 0.52, 0.01),
            )
            self.assertTrue(evidence.accepted)
            self.assertEqual(evidence.value, expected)
            self.assertEqual(evidence.method, "template:raw-and-smoothed")

    def test_blank_material_is_not_promoted_by_smoothing(self):
        evidence = self.recognizer.raw_icon(
            np.full((57, 58, 3), 240, np.uint8),
            ("石", "鉱石"),
            "test",
            (),
            np.arange(0.20, 0.52, 0.01),
        )
        self.assertFalse(evidence.accepted)


class LocalTesseractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ocr = NumericOCR()
        except RuntimeError as exc:
            raise unittest.SkipTest(str(exc)) from exc

    def test_small_white_material_count(self):
        for file in ("cloth-count.png", "cloth-count-ruggie.png"):
            with self.subTest(file=file):
                self.assertEqual(
                    self.ocr.read(
                        load_template(CROPS / file), r"\d+/\d+", required_only=True
                    ),
                    "11",
                )

    def test_five_by_five_size_from_both_furniture(self):
        for file in ("size-five-ortho.png", "size-five-vil.png"):
            with self.subTest(file=file):
                self.assertEqual(self.ocr.read_size(load_template(CROPS / file)), "5x5")

    def test_normalized_size_integrates_as_twenty_five_cells(self):
        r = Recognizer(ocr=self.ocr)
        for file in ("size-five-ortho.png", "size-five-vil.png"):
            crop = load_template(CROPS / file)
            panel = np.full((486, 832, 3), 240, np.uint8)
            panel[360:386, 262:307] = cv2.resize(crop, (45, 26))
            hi = cv2.resize(panel, None, fx=2, fy=2)
            hi[720:772, 524:614] = crop
            r.highres = {id(panel): hi}
            evidence = r.dimensions(panel, "test")
            self.assertTrue(evidence.accepted)
            self.assertEqual(evidence.value, 25)
            self.assertEqual(evidence.method, "numeric_ocr:normalized-size")

    def test_comfort_nine_is_not_read_as_three(self):
        self.assertEqual(
            self.ocr.read(
                load_template(CROPS / "comfort-89.png"), maximum=9999, display=True
            ),
            "89",
        )

    def test_display_rejects_blank_clipped_and_disagreement(self):
        crop = load_template(CROPS / "comfort-89.png")
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        x, y, w, h = cv2.boundingRect((gray > 190).astype(np.uint8))
        for invalid in (np.zeros_like(crop), crop[y : y + h, x : x + w]):
            with patch("subprocess.run") as run:
                self.assertIsNone(self.ocr.read(invalid, display=True))
                run.assert_not_called()
        with patch("subprocess.run", side_effect=[tsv("89"), tsv("83")]):
            self.assertIsNone(self.ocr.read(crop, display=True))

    def test_two_item_cap(self):
        self.assertEqual(
            self.ocr.read(load_template(CROPS / "short-cap.png"), r"\d+/\d+", 999),
            "0/2",
        )

    def test_thin_bonus_digit(self):
        self.assertEqual(
            self.ocr.read(
                load_template(CROPS / "bonus-nine.png"), maximum=999, tight=True
            ),
            "9",
        )

    def test_normalized_jade_cloth_count(self):
        self.assertEqual(
            self.ocr.read(
                load_template(CROPS / "cloth-count-jade.png"),
                r"\d+/\d+",
                required_only=True,
            ),
            "10",
        )

    def test_normalized_clay_count_preserves_existing_reading(self):
        self.assertEqual(
            self.ocr.read(
                load_template(CROPS / "clay-count.png"),
                r"\d+/\d+",
                required_only=True,
            ),
            "10",
        )

    def test_small_cloth_count_preserves_existing_reading(self):
        self.assertEqual(
            self.ocr.read(
                load_template(CROPS / "cloth-count-queen.png"),
                r"\d+/\d+",
                required_only=True,
            ),
            "5",
        )

    def test_black_shortage_counts(self):
        for name, expected in (("ten", "0/10"), ("eight", "0/8")):
            crop = load_template(CROPS / f"shortage-count-{name}.png")
            self.assertEqual(self.ocr.read(crop, r"\d+/\d+", dark_bar=True), expected)
        for value in (0, 240):
            with patch("subprocess.run") as run:
                self.assertIsNone(
                    self.ocr.read(np.full_like(crop, value), r"\d+/\d+", dark_bar=True)
                )
                run.assert_not_called()

    def test_legacy_status_detects_and_reads_measurement(self):
        r = Recognizer(ocr=self.ocr)
        image = load_template(CROPS / "legacy-status.png")
        screens = list(r.panels(image))
        self.assertEqual(len(screens), 1)
        kind, panel, bounds = screens[0]
        self.assertEqual(kind, "status")
        self.assertEqual(panel.shape[:2], (406, 794))
        x, y, w, h = bounds
        r.highres[id(panel)] = cv2.resize(image[y : y + h, x : x + w], (1588, 812))
        measurement = r.status(image, panel, bounds, "legacy")
        self.assertEqual(measurement["comfort"].value, 101)
        self.assertEqual(measurement["bonus"], 9)
        self.assertEqual(measurement["dorm_points"], [0] * 7 + [36])
        # Missing headers must not be guessed from the furniture or baseline.
        self.assertIsNone(r.status(image[y:], panel, (x, 0, w, h), "clipped"))
        blank = image.copy()
        blank[y : y + h, x : x + w] = 240
        self.assertFalse(list(r.panels(blank)))

    def test_shortage_craft_reads_single_recipe(self):
        r = Recognizer(ocr=self.ocr)
        result = Result()
        panel = load_template(CROPS / "shortage-craft.png")
        r.highres[id(panel)] = load_template(CROPS / "shortage-craft-hi.png")
        r.craft(panel, "shortage", result)
        self.assertEqual(result.fields["U"].value, 5000)
        # These ore/metal icons carry the red event-only badge. Earlier labels
        # incorrectly treated their quantities as ordinary materials.
        self.assertFalse({"AE", "AG"} & result.fields.keys())

    def test_limited_materials_never_populate_ordinary_columns(self):
        r = Recognizer(ocr=self.ocr)
        panel = load_template(CROPS / "limited-material-craft.png")
        r.highres[id(panel)] = load_template(CROPS / "limited-material-craft-hi.png")
        result = Result()
        r.craft(panel, "limited", result)
        self.assertEqual(result.fields["U"].value, 6000)
        self.assertFalse(
            set(("Y", "AA", "AC", "AE", "AG", "AI", "AK", "AM", "AO"))
            & result.fields.keys()
        )

    def test_event_shortage_preserves_ordinary_recipe_values(self):
        for file, expected in (
            ("limited-shortage-craft", {"U": 11000, "Y": 24, "AA": 24}),
            ("thin-shortage-craft", {"U": 8000, "Y": 8, "AC": 8}),
        ):
            with self.subTest(file=file):
                r = Recognizer(ocr=self.ocr)
                panel = load_template(CROPS / (file + ".png"))
                r.highres[id(panel)] = load_template(CROPS / (file + "-hi.png"))
                result = Result()
                r.craft(panel, file, result)
                self.assertEqual(
                    {k: result.fields[k].value for k in expected}, expected
                )

    def test_shifted_shop_size_requires_complete_glyphs(self):
        for blank in (False, True):
            r = Recognizer(ocr=self.ocr)
            panel = load_template(CROPS / "shifted-shop-detail.png")
            hi = load_template(CROPS / "shifted-shop-detail-hi.png")
            if blank:
                panel[360:386, 242:307] = 240
                hi[720:772, 484:614] = 240
            r.highres[id(panel)] = hi
            result = Result()
            r.detail(panel, "shop", result)
            self.assertEqual(result.fields["I"].accepted, not blank)
            if not blank:
                self.assertEqual(result.fields["I"].value, 1)
                self.assertEqual(
                    result.fields["I"].method, "numeric_ocr:normalized-size"
                )


class ResampledThemeTests(unittest.TestCase):
    def test_pop_recovers_in_both_theme_slots(self):
        r = Recognizer(ocr=Mock())
        for name in ("resampled-pop-primary", "resampled-pop-secondary"):
            crop = load_template(CROPS / (name + ".png"))
            self.assertFalse(r.classify(crop, "theme_", "test", ()).accepted)
            evidence = r.classify(crop, "theme_", "test", (), smooth_fallback=True)
            self.assertTrue(evidence.accepted)
            self.assertEqual(evidence.value, "ポップ")

    def test_colour_without_glyph_and_obscured_icons_are_held(self):
        r = Recognizer(ocr=Mock())
        crop = load_template(CROPS / "resampled-pop-primary.png")
        colour_only = crop.copy()
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        # Fill the centre of the coloured disk, removing its flower shape.
        coloured = (hsv[:, :, 1] > 70) & (hsv[:, :, 0] > 140)
        yy, xx = np.where(coloured)
        cx, cy = round(float(xx.mean())), round(float(yy.mean()))
        cv2.circle(colour_only, (cx, cy), 8, (145, 70, 193), -1)
        occluded = crop.copy()
        occluded[12:27] = 240
        for invalid in (np.full_like(crop, 240), colour_only, occluded):
            self.assertFalse(
                r.classify(invalid, "theme_", "test", (), smooth_fallback=True).accepted
            )


class RepairedPanelTests(unittest.TestCase):
    def test_scratched_measurement_still_requires_text_anchor(self):
        r = Recognizer(ocr=Mock())
        image = load_template(CROPS / "scratched-status.png")
        screens = list(r.panels(image))
        self.assertEqual(len(screens), 1)
        kind, panel, (x, y, w, h) = screens[0]
        self.assertEqual(kind, "status")
        self.assertEqual(panel.shape[:2], (406, 794))
        # A similarly sized white rectangle with the anchor erased is unknown.
        blank = image.copy()
        blank[
            y + round(20 * h / 406) : y + round(60 * h / 406),
            x + round(18 * w / 794) : x + round(172 * w / 794),
        ] = 240
        self.assertFalse(list(r.panels(blank)))


class MaterialBadgeTests(unittest.TestCase):
    def test_grey_shield_at_both_resolutions(self):
        for scale, suffix in ((1, ""), (2, "-hi")):
            panel = load_template(CROPS / f"grey-limited-craft{suffix}.png")
            for cx in (461, 547, 633, 719):
                with self.subTest(scale=scale, cx=cx):
                    self.assertEqual(
                        material_badge(panel, cx * scale, 203 * scale, scale), "limited"
                    )

    def test_grey_rectangle_or_blank_is_not_a_shield(self):
        panel = load_template(CROPS / "grey-limited-craft.png")
        panel[157:181, 469:493] = 240
        self.assertIsNone(material_badge(panel, 461, 203))
        panel[161:179, 472:489] = (110, 95, 90)
        self.assertIsNone(material_badge(panel, 461, 203))

    def test_grey_shield_without_glyph_only_blocks_output(self):
        panel = load_template(CROPS / "grey-limited-craft.png")
        # Preserve the shield silhouette but erase its white exclamation mark.
        corner = panel[157:181, 469:493]
        hsv = cv2.cvtColor(corner, cv2.COLOR_BGR2HSV)
        white = (hsv[:, :, 1] < 65) & (hsv[:, :, 2] > 220)
        inner = np.zeros(white.shape, bool)
        inner[6:20, 8:15] = True
        corner[white & inner] = (110, 95, 90)
        self.assertEqual(material_badge(panel, 461, 203), "uncertain")

    def test_yellow_red_and_orange_event_badges(self):
        for file, cx, y in (
            ("limited-material-craft", 504, 203),
            ("limited-shortage-craft", 590, 238),
            ("thin-shortage-craft", 590, 238),
        ):
            self.assertEqual(
                material_badge(load_template(CROPS / (file + ".png")), cx, y), "limited"
            )

    def test_ordinary_materials_and_paint_are_not_event_badges(self):
        panel = load_template(CROPS / "ordinary-material-craft.png")
        for cx in (461, 547, 633, 719):
            self.assertIsNone(material_badge(panel, cx, 169))

    def test_colour_without_exclamation_cannot_confirm_shortage(self):
        panel = load_template(CROPS / "limited-material-craft.png")
        # An unreadable shield must still block ordinary-material output.
        panel[162:176, 515:529] = (80, 190, 240)
        self.assertEqual(material_badge(panel, 504, 203), "uncertain")
        panel[157:181, 512:536] = 240
        self.assertIsNone(material_badge(panel, 504, 203))


class ShortageGuardTests(unittest.TestCase):
    def check_held(self, count, black_count="0/10", enabled_button=False):
        r = Recognizer(ocr=Mock())
        r.detail = Mock()
        panel = load_template(CROPS / "shortage-craft.png")
        r.highres[id(panel)] = load_template(CROPS / "shortage-craft-hi.png")
        if enabled_button:
            panel[425:438, 407:425] = (180, 20, 150)

        def number(panel, box, source, *args, **kwargs):
            if box == (522, 311, 580, 340):
                return Evidence(
                    count, "recognized" if count is not None else "unreadable"
                )
            if kwargs.get("dark_bar"):
                return Evidence(
                    black_count, "recognized" if black_count else "unreadable"
                )
            return Evidence("20", "recognized")

        r.number = number
        result = Result()
        r.craft(panel, "test", result)
        self.assertFalse(result.fields)
        self.assertTrue(result.reasons)

    def test_multiple_and_ambiguous_craft_counts_are_held(self):
        for count in ("2/5", "0/1", "0/2", None):
            with self.subTest(count=count):
                self.check_held(count)

    def test_zero_count_requires_verified_material_shortage(self):
        for black_count in (None, "10/10", "12/10", "0/0"):
            with self.subTest(black_count=black_count):
                self.check_held("0/0", black_count)

    def test_zero_count_requires_disabled_craft_buttons(self):
        self.check_held("0/0", enabled_button=True)


class MeasurementContextTests(unittest.TestCase):
    def setUp(self):
        self.recognizer = Recognizer(ocr=Mock())

    def test_faint_none_label_still_requires_a_dash(self):
        panel = np.full((486, 832, 3), 240, np.uint8)
        panel[432:462, 65:118] = load_template(CROPS / "faint-dorm-none.png")
        template = self.recognizer.templates["dorm_なし"]
        self.assertTrue(explicit_dorm_dash(panel, template))
        panel[434:459, 87:115] = 240
        self.assertFalse(explicit_dorm_dash(panel, template))

    def test_short_none_dash_is_not_a_dot_or_vertical_line(self):
        panel = np.full((486, 832, 3), 240, np.uint8)
        panel[432:462, 65:118] = load_template(CROPS / "short-dorm-none.png")
        template = self.recognizer.templates["dorm_なし"]
        self.assertTrue(explicit_dorm_dash(panel, template))
        panel[434:459, 87:115] = 240
        self.assertFalse(explicit_dorm_dash(panel, template))
        for width, height in ((2, 2), (3, 3), (2, 5)):
            invalid = panel.copy()
            invalid[448 : 448 + height, 94 : 94 + width] = 100
            self.assertFalse(explicit_dorm_dash(invalid, template))

    def test_zero_glyph_does_not_match_other_points_or_blank(self):
        self.assertTrue(
            self.recognizer.zero_number(load_template(CROPS / "faint-dorm-zero.png"))
        )
        for file in ("dorm-number-five.png", "dorm-number-thirty-six.png"):
            self.assertFalse(self.recognizer.zero_number(load_template(CROPS / file)))
        self.assertFalse(
            self.recognizer.zero_number(np.full((32, 28, 3), 240, np.uint8))
        )

    def measurement(self, category, points, dorm="なし"):
        image = np.full((486, 832, 3), 240, np.uint8)
        encoded = cv2.imencode(".png", image)[1].tobytes()
        r = self.recognizer
        r.panels = Mock(
            return_value=[
                ("detail", image, (0, 0, 832, 486)),
                ("status", image, (0, 0, 832, 486)),
            ]
        )
        r.detail = lambda panel, source, result: result.add(
            "P", Evidence(dorm, "recognized", source)
        )
        r.status = Mock(
            return_value={
                "source": "same",
                "comfort": Evidence(97, "recognized"),
                "bonus": 9,
                "dorm_points": points,
            }
        )
        return r.analyze([encoded], category=category)

    def test_floor_baseline_requires_explicit_category(self):
        for category in ("内観・外観：床", "内観・外観：壁紙"):
            result = self.measurement(category, [0] * 7 + [27])
            self.assertEqual([result.fields[k].value for k in "DEF"], [97, 9, 0])
        for category in (None, "家具：椅子"):
            result = self.measurement(category, [0] * 7 + [27])
            self.assertFalse(any(k in result.fields for k in "DEF"))

    def test_inconsistent_floor_measurement_is_held(self):
        for points in ([0] * 7 + [36], [1] + [0] * 6 + [27]):
            result = self.measurement("内観・外観：床", points)
            self.assertFalse(any(k in result.fields for k in "DEF"))

    def test_nrc_keeps_displayed_points_for_sheet_formula(self):
        result = self.measurement(
            "内観・外観：壁紙", [0] * 7 + [39], "ナイトレイブンカレッジ"
        )
        self.assertEqual(result.fields["F"].value, 39)


if __name__ == "__main__":
    unittest.main()
