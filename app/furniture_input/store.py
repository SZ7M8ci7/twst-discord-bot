import threading
from .post_parser import normalize_name
from .sheet_writer import build_plan, cell_request, calculated_points, column_index
from .sheet_schema import verify_headers


class SheetStore:
    """Event-scoped writes to the existing input sheet; no job/result storage.

    Run one bot process. The lock serializes writes within this process only.
    """

    def __init__(self, connect, mode):
        self.connect = connect
        self.mode = mode
        self.lock = threading.RLock()
        self.sheet = None

    def check_schema(self):
        if self.sheet is None:
            self.sheet = self.connect()
        verify_headers(self.sheet.get("B1:BF2"))

    def apply(self, post, result):
        with self.lock:
            self.check_schema()
            before = self.sheet.get("B3:BF", value_render_option="FORMULA")
            plan = build_plan(before, post, result)
            if self.mode == "dry_run":
                return {"state": "dry_run", "plan": plan}
            if not plan.values:
                return {"state": "skipped", "plan": plan}
            # Re-resolve by name and compare immediately before writing. Sheets has no CAS;
            # human editors should mark B=編集中 before editing a row.
            fresh = self.sheet.get("B3:BF", value_render_option="FORMULA")
            if fresh != before:
                return {
                    "state": "skipped",
                    "plan": plan,
                    "reason": "書込み直前にシート変更を検出",
                }
            requests = []
            metadata = self.sheet.spreadsheet.fetch_sheet_metadata(
                params={"fields": "sheets(properties(sheetId,gridProperties))"}
            )
            row_count = self.sheet.row_count
            for sh in metadata.get("sheets", []):
                prop = sh.get("properties", {})
                if prop.get("sheetId") == self.sheet.id:
                    row_count = prop.get("gridProperties", {}).get(
                        "rowCount", row_count
                    )
            if plan.row > row_count:
                requests.append(
                    {
                        "appendDimension": {
                            "sheetId": self.sheet.id,
                            "dimension": "ROWS",
                            "length": plan.row - row_count,
                        }
                    }
                )
            if plan.new and plan.row > 3:
                for paste_type in ("PASTE_FORMAT", "PASTE_DATA_VALIDATION"):
                    requests.append(
                        {
                            "copyPaste": {
                                "source": {
                                    "sheetId": self.sheet.id,
                                    "startRowIndex": plan.row - 2,
                                    "endRowIndex": plan.row - 1,
                                    "startColumnIndex": 1,
                                    "endColumnIndex": 58,
                                },
                                "destination": {
                                    "sheetId": self.sheet.id,
                                    "startRowIndex": plan.row - 1,
                                    "endRowIndex": plan.row,
                                    "startColumnIndex": 1,
                                    "endColumnIndex": 58,
                                },
                                "pasteType": paste_type,
                            }
                        }
                    )
            requests.extend(
                cell_request(self.sheet.id, plan.row, c, v)
                for c, v in plan.values.items()
            )
            requests.extend(
                cell_request(self.sheet.id, plan.row, c, v, True)
                for c, v in plan.formula_values.items()
            )
            # Send once. If the response is lost, do not retry an ambiguous write.
            self.sheet.spreadsheet.batch_update({"requests": requests})
            errors = self.verify(post, plan)
            return {
                "state": "readback_mismatch" if errors else "written",
                "plan": plan,
                "readback_errors": errors,
            }

    def verify(self, post, plan):
        with self.lock:
            row = plan.row
            check = self.sheet.get(
                f"B{row}:BF{row}", value_render_option="UNFORMATTED_VALUE"
            )
            actual = check[0] if check else []
            errors = []
            if len(actual) < 2 or normalize_name(str(actual[1])) != post["key"]:
                errors.append("C")
            for c, v in plan.values.items():
                i = column_index(c) - 1
                if i >= len(actual) or str(actual[i]) != str(v):
                    errors.append(c)
            vals = {
                c: actual[column_index(c) - 1]
                for c in "DEFHNOP"
                if column_index(c) - 1 < len(actual)
            }
            if all(vals.get(c) not in (None, "") for c in "DEFHNOP"):
                try:
                    expected = calculated_points(vals)
                    for c, v in expected.items():
                        i = column_index(c) - 1
                        if i >= len(actual) or str(actual[i]) != str(v):
                            errors.append(c)
                except (ValueError, TypeError):
                    errors.append("calculation")
            return errors
