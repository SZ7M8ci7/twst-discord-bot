import asyncio
import copy
import io
import unittest
from unittest.mock import Mock, AsyncMock
from types import SimpleNamespace
from PIL import Image
from app.furniture_input.models import Evidence, Result
from app.furniture_input.post_parser import parse_post, revision_key, normalize_name
from app.furniture_input.sheet_writer import (
    build_plan,
    calculated_points,
    cell_request,
)
from app.furniture_input.image_loader import decode, MAX_BYTES
from app.furniture_input.store import SheetStore
from app.furniture_input.service import AutoInputService


def result():
    return Result(
        fields={
            c: Evidence(v, "recognized", "same-status")
            for c, v in {
                "D": 97,
                "E": 0,
                "F": 5,
                "G": "SR",
                "I": 1,
                "N": "スタイリッシュ",
                "O": "ポップ",
                "P": "スカラビア",
                "R": 30,
            }.items()
        }
    )


POST = parse_post("グレート・ルック（ジャミル）\n雑貨：衣装")


def row(state="未入力", name=None, **values):
    data = [""] * 57
    data[0] = state
    data[1] = POST["name"] if name is None else name
    from app.furniture_input.sheet_writer import column_index

    for key, value in values.items():
        data[column_index(key) - 1] = value
    return data


class PostTests(unittest.TestCase):
    def test_label_and_first_line(self):
        self.assertEqual(
            parse_post("家具名：椅子\n家具:椅子")["category"], "家具：椅子"
        )
        self.assertEqual(parse_post("椅子\n家具：椅子")["name"], "椅子")

    def test_chatter_and_ambiguity_ignored(self):
        for text in (
            "ショップ情報です",
            "家具：椅子",
            "椅子\n家具：机\n家具：椅子",
            "家具名：\n家具：机",
            "家具名：a\n家具名：b\n家具：机",
        ):
            self.assertIsNone(parse_post(text))

    def test_names_preserve_meaning(self):
        self.assertEqual(normalize_name("家具 (A)"), normalize_name("家具 （Ａ）"))
        self.assertNotEqual(
            normalize_name("グレートルック"), normalize_name("グレート・ルック")
        )
        self.assertNotEqual(normalize_name("写真①"), normalize_name("写真②"))

    def test_revision_order_independent_and_content_sensitive(self):
        a = revision_key(1, "a", [2, 3], "v1")
        self.assertEqual(a, revision_key(1, "a", [3, 2], "v1"))
        self.assertNotEqual(a, revision_key(1, "b", [2, 3], "v1"))
        self.assertNotEqual(a, revision_key(1, "a", [2, 4], "v1"))

    def test_sheet_names_use_full_width_parentheses(self):
        posted = "フレンドリーテック(シルバー)"
        post = parse_post(f"家具名：{posted}\n雑貨：衣装")
        plan = build_plan([], post, result())
        self.assertEqual(plan.values["C"], "フレンドリーテック（シルバー）")
        self.assertEqual(post["posted_name"], posted)
        self.assertEqual(post["key"], normalize_name(posted))
        existing = row(name=posted)
        self.assertEqual(build_plan([existing], post, result()).row, 3)
        self.assertNotIn("C", build_plan([existing], post, result()).values)

    def test_sheet_names_convert_all_half_width_characters(self):
        posted = "ABC abc 123 ｼﾙﾊﾞｰ!?#&+-/[]()①"
        post = parse_post(f"家具名：{posted}\n雑貨：衣装")
        plan = build_plan([], post, result())
        self.assertEqual(
            plan.values["C"], "ＡＢＣ　ａｂｃ　１２３　シルバー！？＃＆＋－／［］（）①"
        )
        self.assertEqual(post["posted_name"], posted)
        self.assertEqual(post["key"], normalize_name(posted))


class PlanTests(unittest.TestCase):
    def test_new_row_and_formulas(self):
        plan = build_plan([], POST, result())
        self.assertEqual(plan.row, 3)
        self.assertEqual(plan.values["B"], "未入力")
        self.assertEqual(set(plan.formula_values), set("JKLM"))
        self.assertNotIn("J", plan.values)

    def test_all_protected_states(self):
        for state in ("記入済", "公開済", "", "unknown"):
            self.assertFalse(build_plan([row(state)], POST, result()).values)

    def test_editing_row_fills_blanks_preserving_state_values_and_formulas(self):
        plan = build_plan([row("編集中", G="R", E=0, J="=SPECIAL(1)")], POST, result())
        self.assertIn("D", plan.values)
        self.assertFalse(set("BCEGJ") & plan.values.keys())
        self.assertFalse(plan.formula_values)

    def test_duplicate_name_is_not_first_match(self):
        self.assertFalse(build_plan([row(), row()], POST, result()).values)

    def test_only_empty_cells_and_no_formula_change(self):
        plan = build_plan([row(G="R", J="=SPECIAL(1)", K=5)], POST, result())
        self.assertNotIn("G", plan.values)
        self.assertFalse(plan.formula_values)
        self.assertIn("D", plan.values)

    def test_zero_not_empty(self):
        plan = build_plan([row(E=0)], POST, result())
        self.assertNotIn("E", plan.values)
        self.assertIn("D", plan.values)

    def test_group_conflict_holds_all_numbers(self):
        plan = build_plan([row(D=98)], POST, result())
        self.assertFalse(set("DEF") & set(plan.values))
        self.assertIn("G", plan.values)

    def test_missing_or_mixed_source_holds_group(self):
        r = result()
        r.fields["E"].state = "missing"
        self.assertFalse(set("DEF") & set(build_plan([], POST, r).values))
        r = result()
        r.fields["F"].source = "other-image"
        self.assertFalse(set("DEF") & set(build_plan([], POST, r).values))

    def test_negative_points_held(self):
        r = result()
        r.fields["D"].value = 10
        self.assertFalse(set("DEF") & set(build_plan([], POST, r).values))

    def test_dorm_conflict_holds_group(self):
        plan = build_plan([row(P="イグニハイド")], POST, result())
        self.assertFalse(set("DEF") & set(plan.values))

    def test_category_conflict_holds_row(self):
        self.assertFalse(build_plan([row(H="家具：机")], POST, result()).values)

    def test_new_row_never_reuses_unnamed_input(self):
        self.assertFalse(
            build_plan([row(name="", state="", D=100)], POST, result()).values
        )

    def test_unique_punctuation_variant_resolves_without_renaming(self):
        post = parse_post("グレートルック（カリム）\n雑貨：衣装")
        plan = build_plan([row(name="グレート・ルック（カリム）")], post, result())
        self.assertEqual(plan.row, 3)
        self.assertIn("G", plan.values)
        self.assertNotIn("C", plan.values)
        self.assertEqual(
            parse_post("グレートルック(ジャミル)\n雑貨：衣装")["key"], POST["key"]
        )

    def test_ambiguous_variants_are_not_merged(self):
        post = parse_post("グレートルック（エース）\n雑貨：衣装")
        rows = [
            row(name="グレート・ルック（エース）"),
            row(name="グレート ルック（エース）"),
        ]
        self.assertFalse(build_plan(rows, post, result()).values)

    def test_category_omission_requires_existing_identity(self):
        post = parse_post("グレート・ルック（ジャミル）", allow_missing_category=True)
        self.assertFalse(build_plan([], post, result()).values)
        plan = build_plan([row("編集中", H="雑貨：衣装")], post, result())
        self.assertIn("D", plan.values)
        self.assertNotIn("H", plan.values)
        plan = build_plan([row("編集中")], post, result())
        self.assertIn("G", plan.values)
        self.assertFalse(set("DEFH") & plan.values.keys())

    def test_placeholders_preserve_custom_formulas(self):
        plan = build_plan([row(name="", state="", J="=1")], POST, result())
        self.assertNotIn("J", plan.formula_values)

    def test_formula_injection_is_literal(self):
        request = cell_request(1, 3, "C", "=IMPORTXML(1)")
        self.assertEqual(
            request["updateCells"]["rows"][0]["values"][0]["userEnteredValue"],
            {"stringValue": "=IMPORTXML(1)"},
        )

    def test_point_rules(self):
        values = {
            "D": 97,
            "E": 0,
            "F": 5,
            "H": "雑貨：衣装",
            "P": "スカラビア",
            "N": "a",
            "O": "b",
        }
        self.assertEqual(calculated_points(values), {"J": 6, "K": 5, "L": 4, "M": 2})
        self.assertEqual(
            calculated_points({**values, "D": 105, "F": 9}),
            {"J": 10, "K": 9, "L": 7, "M": 3},
        )
        self.assertEqual(
            calculated_points({**values, "D": 107, "E": 9, "F": 0, "P": "なし"})["J"],
            12,
        )
        self.assertEqual(
            calculated_points(
                {
                    **values,
                    "D": 80,
                    "F": 27,
                    "P": "ナイトレイブンカレッジ",
                    "H": "内観・外観：床",
                    "O": "－",
                }
            ),
            {"J": 12, "K": 0, "L": 12, "M": ""},
        )

    def test_conflicting_images_not_majority_vote(self):
        r = Result()
        r.add("G", Evidence("R", "recognized"))
        r.add("G", Evidence("SR", "recognized"))
        r.add("G", Evidence("R", "recognized"))
        self.assertEqual(r.fields["G"].state, "conflict")


class ImageTests(unittest.TestCase):
    def test_bad_format_size_and_resolution(self):
        for data in (b"not an image", b"0" * (MAX_BYTES + 1)):
            with self.assertRaises((ValueError, OSError)):
                decode(data)
        for size in ((20, 20), (4000, 4000)):
            b = io.BytesIO()
            Image.new("RGB", size).save(b, format="PNG")
            with self.assertRaises(ValueError):
                decode(b.getvalue())

    def test_unknown_image_does_not_create_fields(self):
        from app.furniture_input.recognizer import Recognizer

        b = io.BytesIO()
        Image.new("RGB", (1000, 700), "white").save(b, format="PNG")
        r = Recognizer(ocr=Mock()).analyze([b.getvalue(), b.getvalue()])
        self.assertFalse(r.fields)
        self.assertEqual(len(r.screens), 1)


class FakeInputSheet:
    """Only the existing furniture sheet exists. Apply real cell requests in memory."""

    def __init__(self, rows=None):
        from app.furniture_input.sheet_schema import HEADERS
        from app.furniture_input.sheet_writer import column_index

        self.id = 3
        self.row_count = 100
        self.data = copy.deepcopy(rows or [])
        self.headers = [""] * 57
        for c, label in HEADERS.items():
            self.headers[column_index(c) - 1] = label
        self.spreadsheet = Mock()
        self.spreadsheet.worksheet.side_effect = AssertionError("No other sheets")
        self.spreadsheet.add_worksheet.side_effect = AssertionError("No new sheets")
        self.spreadsheet.fetch_sheet_metadata.return_value = {}
        self.spreadsheet.batch_update.side_effect = self.apply_requests
        self.get = Mock(side_effect=self.read)

    def read(self, area, value_render_option=None):
        from app.furniture_input.sheet_writer import column_index

        if area == "B1:BF2":
            return [copy.deepcopy(self.headers)]
        if area == "B3:BF":
            return copy.deepcopy(self.data)
        index = int(area.split(":")[0][1:]) - 3
        actual = copy.deepcopy(self.data[index])
        values = {c: actual[column_index(c) - 1] for c in "DEFHNOP"}
        if all(values.get(c) not in (None, "") for c in "DEFHNOP"):
            for c, v in calculated_points(values).items():
                i = column_index(c) - 1
                if isinstance(actual[i], str) and actual[i].startswith("="):
                    actual[i] = v
        return [actual]

    def apply_requests(self, body):
        for request in body["requests"]:
            if "updateCells" not in request:
                continue
            update = request["updateCells"]
            assert update["start"]["sheetId"] == self.id
            assert update["fields"] == "userEnteredValue"
            index = update["start"]["rowIndex"] - 2
            while len(self.data) <= index:
                self.data.append([""] * 57)
            value = update["rows"][0]["values"][0]["userEnteredValue"]
            self.data[index][update["start"]["columnIndex"] - 1] = next(
                iter(value.values())
            )


def input_store(mode="write", rows=None):
    sheet = FakeInputSheet(rows)
    return SheetStore(Mock(return_value=sheet), mode), sheet


class StoreTests(unittest.TestCase):
    def test_variant_readback_checks_resolved_identity(self):
        post = parse_post("グレートルック（エース）\n雑貨：衣装")
        store, sheet = input_store(
            rows=[
                row(
                    "編集中",
                    name="グレート・ルック（エース）",
                    J="=1",
                    K="=1",
                    L="=1",
                    M="=1",
                )
            ]
        )
        self.assertEqual(store.apply(post, result())["state"], "written")
        self.assertEqual(sheet.data[0][1], "グレート・ルック（エース）")
        self.assertEqual(sheet.data[0][0], "編集中")

    def test_existing_input_sheet_only_without_management_tab(self):
        store, sheet = input_store()
        outcome = store.apply(POST, result())
        self.assertEqual(outcome["state"], "written")
        self.assertEqual(len(sheet.data), 1)
        self.assertEqual(sheet.data[0][1], POST["name"])
        sheet.spreadsheet.worksheet.assert_not_called()
        sheet.spreadsheet.add_worksheet.assert_not_called()
        sheet.spreadsheet.batch_update.assert_called_once()
        requests = sheet.spreadsheet.batch_update.call_args.args[0]["requests"]
        self.assertTrue(
            all(r["updateCells"]["fields"] == "userEnteredValue" for r in requests)
        )

    def test_dryrun_never_writes_any_sheet(self):
        store, sheet = input_store("dry_run")
        self.assertEqual(store.apply(POST, result())["state"], "dry_run")
        self.assertEqual(sheet.data, [])
        sheet.spreadsheet.batch_update.assert_not_called()
        sheet.spreadsheet.add_worksheet.assert_not_called()

    def test_repeat_after_service_restart_uses_sheet_not_history(self):
        store, sheet = input_store()
        store.apply(POST, result())
        restarted = SheetStore(Mock(return_value=sheet), "write")
        self.assertEqual(restarted.apply(POST, result())["state"], "skipped")
        self.assertEqual(len(sheet.data), 1)
        sheet.spreadsheet.batch_update.assert_called_once()

    def test_later_post_completes_only_missing_fields(self):
        store, sheet = input_store()
        first = Result(fields={"G": Evidence("SR", "recognized")})
        store.apply(POST, first)
        outcome = store.apply(POST, result())
        self.assertEqual(outcome["state"], "written")
        self.assertNotIn("G", outcome["plan"].values)
        self.assertIn("D", outcome["plan"].values)
        self.assertEqual(len(sheet.data), 1)

    def test_schema_shift_fails_before_write(self):
        store, sheet = input_store()
        sheet.headers[19] = "所持マドル"
        with self.assertRaises(ValueError):
            store.apply(POST, result())
        sheet.spreadsheet.batch_update.assert_not_called()

    def test_concurrent_edit_prevents_write(self):
        store, sheet = input_store(rows=[row()])
        sheet.get.side_effect = [[sheet.headers], [row()], [row("編集中")]]
        self.assertEqual(store.apply(POST, result())["state"], "skipped")
        sheet.spreadsheet.batch_update.assert_not_called()

    def test_readback_failure_does_not_retry_or_store_receipt(self):
        store, sheet = input_store()
        sheet.get.side_effect = [[sheet.headers], [], [], TimeoutError()]
        with self.assertRaises(TimeoutError):
            store.apply(POST, result())
        sheet.spreadsheet.batch_update.assert_called_once()
        self.assertFalse(hasattr(store, "ledger"))
        self.assertFalse(hasattr(store, "pending"))

    def test_ambiguous_write_is_not_repeated_and_repost_uses_current_sheet(self):
        store, sheet = input_store()

        def apply_then_timeout(body):
            sheet.apply_requests(body)
            raise TimeoutError()

        sheet.spreadsheet.batch_update.side_effect = apply_then_timeout
        with self.assertRaises(TimeoutError):
            store.apply(POST, result())
        self.assertEqual(store.apply(POST, result())["state"], "skipped")
        sheet.spreadsheet.batch_update.assert_called_once()
        self.assertEqual(len(sheet.data), 1)

    def test_readback_checks_furniture_identity_without_rewriting(self):
        store, sheet = input_store(rows=[row(J="=1", K="=1", L="=1", M="=1")])

        def move_row(body):
            sheet.apply_requests(body)
            sheet.data[0][1] = "別の家具"

        sheet.spreadsheet.batch_update.side_effect = move_row
        outcome = store.apply(POST, result())
        self.assertEqual(outcome["state"], "readback_mismatch")
        self.assertIn("C", outcome["readback_errors"])
        sheet.spreadsheet.batch_update.assert_called_once()

    def test_new_row_copies_only_format_and_validation(self):
        store, sheet = input_store(rows=[row("記入済", name="別の家具")])
        store.apply(POST, result())
        requests = sheet.spreadsheet.batch_update.call_args.args[0]["requests"]
        copies = [r["copyPaste"] for r in requests if "copyPaste" in r]
        self.assertEqual(
            {r["pasteType"] for r in copies}, {"PASTE_FORMAT", "PASTE_DATA_VALIDATION"}
        )
        self.assertEqual(sheet.data[0][0], "記入済")
        self.assertEqual(sheet.data[1][1], POST["name"])
        self.assertNotIn("note", str(requests))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setup_service(self, mode="write"):
        attachment = SimpleNamespace(
            id=10, filename="a.png", size=10, read=AsyncMock(return_value=b"test")
        )
        message = SimpleNamespace(
            id=9,
            content="グレート・ルック（ジャミル）\n雑貨：衣装",
            attachments=[attachment],
            author=SimpleNamespace(bot=False),
            channel=SimpleNamespace(id=1),
        )
        channel = Mock()
        channel.fetch_message = AsyncMock(return_value=message)
        client = Mock()
        client.get_channel.return_value = channel
        store, sheet = input_store(mode)
        service = AutoInputService(client, store.connect, {1}, mode)
        service.recognizer = Mock()
        service.recognizer.analyze.return_value = result()
        return service, message, channel, sheet

    async def test_off_performs_no_io(self):
        service, message, channel, sheet = self.setup_service("off")
        await service.submit(message)
        service.store.connect.assert_not_called()
        channel.fetch_message.assert_not_called()
        message.attachments[0].read.assert_not_called()

    async def test_construction_does_not_start_history_scan_or_worker(self):
        client, connect = Mock(), Mock()
        service = AutoInputService(client, connect, {1}, "write")
        await asyncio.sleep(0)
        self.assertFalse(client.mock_calls)
        connect.assert_not_called()
        for attribute in ("task", "next_retry", "wake", "recover_recent", "run"):
            self.assertFalse(hasattr(service, attribute))

    async def test_live_post_reads_recognizes_and_writes_without_reply(self):
        service, message, channel, sheet = self.setup_service()
        await service.submit(message)
        service.recognizer.analyze.assert_called_once_with(
            [b"test"], category=POST["category"]
        )
        sheet.spreadsheet.batch_update.assert_called_once()
        channel.send.assert_not_called()
        channel.history.assert_not_called()
        self.assertEqual(sheet.data[0][1], POST["name"])

    async def test_missing_category_uses_existing_sheet_category(self):
        service, message, channel, sheet = self.setup_service()
        message.content = POST["name"]
        sheet.data = [row("編集中", H="雑貨：衣装")]
        await service.submit(message)
        service.recognizer.analyze.assert_called_once_with(
            [b"test"], category="雑貨：衣装"
        )
        sheet.spreadsheet.batch_update.assert_called_once()

    async def test_unknown_categoryless_chatter_never_downloads_or_writes(self):
        service, message, channel, sheet = self.setup_service()
        message.content = "ショップ情報です"
        await service.submit(message)
        message.attachments[0].read.assert_not_called()
        service.recognizer.analyze.assert_not_called()
        sheet.spreadsheet.batch_update.assert_not_called()

    async def test_known_name_without_category_fills_independent_fields(self):
        service, message, channel, sheet = self.setup_service()
        message.content = POST["name"]
        sheet.data = [row("編集中")]
        await service.submit(message)
        service.recognizer.analyze.assert_called_once_with([b"test"], category=None)
        requests = sheet.spreadsheet.batch_update.call_args.args[0]["requests"]
        columns = {r["updateCells"]["start"]["columnIndex"] for r in requests}
        self.assertFalse({3, 4, 5, 7} & columns)
        self.assertIn(6, columns)

    async def test_concurrent_deliveries_do_not_duplicate_row(self):
        service, message, channel, sheet = self.setup_service()
        await asyncio.gather(service.submit(message), service.submit(message))
        sheet.spreadsheet.batch_update.assert_called_once()
        self.assertEqual(len(sheet.data), 1)

    async def test_edit_during_recognition_no_write_or_resubmit(self):
        service, message, channel, sheet = self.setup_service()
        latest = copy.copy(message)
        latest.content = "机\n家具：机"
        channel.fetch_message.side_effect = [message, latest]
        await service.submit(message)
        service.recognizer.analyze.assert_called_once()
        sheet.spreadsheet.batch_update.assert_not_called()
        channel.history.assert_not_called()
        self.assertEqual(channel.fetch_message.call_count, 2)

    async def test_already_edited_post_is_skipped_before_downloading(self):
        service, message, channel, sheet = self.setup_service()
        latest = copy.copy(message)
        latest.attachments = []
        channel.fetch_message.return_value = latest
        await service.submit(message)
        message.attachments[0].read.assert_not_called()
        sheet.spreadsheet.batch_update.assert_not_called()

    async def test_failure_ends_event_next_event_can_run(self):
        service, message, channel, sheet = self.setup_service()
        message.attachments[0].read.side_effect = [TimeoutError(), b"test"]
        with self.assertLogs("app.furniture_input.service", level="WARNING"):
            await service.submit(message)
        self.assertEqual(message.attachments[0].read.call_count, 1)
        sheet.spreadsheet.batch_update.assert_not_called()
        await service.submit(message)
        sheet.spreadsheet.batch_update.assert_called_once()

    async def test_oversized_image_ends_event_without_download(self):
        service, message, channel, sheet = self.setup_service()
        message.attachments[0].size = MAX_BYTES + 1
        with self.assertLogs("app.furniture_input.service", level="WARNING"):
            await service.submit(message)
        message.attachments[0].read.assert_not_called()
        sheet.spreadsheet.batch_update.assert_not_called()

    async def test_dry_run_does_not_save_recognition_results(self):
        service, message, channel, sheet = self.setup_service("dry_run")
        with self.assertLogs("app.furniture_input.service", level="INFO") as logs:
            await service.submit(message)
        sheet.spreadsheet.batch_update.assert_not_called()
        self.assertNotIn(POST["name"], "\n".join(logs.output))
        self.assertNotIn("recognized", "\n".join(logs.output))
        self.assertEqual(
            set(vars(service)),
            {"client", "channel_ids", "mode", "store", "recognizer", "lock"},
        )


if __name__ == "__main__":
    unittest.main()
