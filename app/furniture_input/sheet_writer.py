"""Pure write planning and narrowly scoped Sheets batch requests."""

from dataclasses import dataclass, field
from .post_parser import normalize_name, resolve_post

INPUT_COLUMNS = set("CDEFGHINOPRU") | {
    "Y",
    "AA",
    "AC",
    "AE",
    "AG",
    "AI",
    "AK",
    "AM",
    "AO",
}
CORE_COLUMNS = set("DEFGHINOPR")


def column_index(column):
    n = 0
    for char in column:
        n = n * 26 + ord(char) - 64
    return n - 1


def value_cell(value, formula=False):
    key = (
        "formulaValue"
        if formula
        else ("numberValue" if isinstance(value, (int, float)) else "stringValue")
    )
    return {"userEnteredValue": {key: value}}


def cell_request(sheet_id, row, column, value, formula=False):
    return {
        "updateCells": {
            "start": {
                "sheetId": sheet_id,
                "rowIndex": row - 1,
                "columnIndex": column_index(column),
            },
            "rows": [{"values": [value_cell(value, formula)]}],
            "fields": "userEnteredValue",
        }
    }


def formulas(row):
    r = row
    floor = f'OR(H{r}="内観・外観：床",H{r}="内観・外観：壁紙")'
    return {
        "J": f'=IF(OR(D{r}="",E{r}="",F{r}="",H{r}="",P{r}=""),"",D{r}-E{r}-K{r}-IF({floor},68,86))',
        "K": f'=IF(OR(F{r}="",H{r}="",P{r}=""),"",IF(P{r}="ナイトレイブンカレッジ",F{r}-IF({floor},27,36),F{r}))',
        "L": f'=IF(OR(J{r}="",N{r}="",O{r}=""),"",IF(O{r}="－",J{r},ROUNDUP(J{r}*2/3)))',
        "M": f'=IF(OR(J{r}="",N{r}="",O{r}="",O{r}="－"),"",ROUNDDOWN(J{r}/3))',
    }


def calculated_points(values):
    if any(values.get(c) in (None, "") for c in "DEFHP"):
        return None
    d, e, f = (int(values[c]) for c in "DEF")
    floor = values["H"] in {"内観・外観：床", "内観・外観：壁紙"}
    k = f - (27 if floor else 36) if values["P"] == "ナイトレイブンカレッジ" else f
    j = d - e - k - (68 if floor else 86)
    if k < 0 or not 0 <= j <= 500 or e < 0:
        raise ValueError("測定条件と計算値が不整合")
    return {
        "J": j,
        "K": k,
        "L": j if values.get("O") == "－" else (2 * j + 2) // 3,
        "M": "" if values.get("O") == "－" else j // 3,
    }


@dataclass
class Plan:
    row: int | None = None
    values: dict = field(default_factory=dict)
    formula_values: dict = field(default_factory=dict)
    reasons: list = field(default_factory=list)
    new: bool = False


def build_plan(rows, post, result):
    """rows start at row 3 and column B; supplied values use FORMULA rendering."""
    post, reasons = resolve_post(rows, post)
    if post is None:
        return Plan(reasons=reasons)
    matches = [
        i + 3
        for i, r in enumerate(rows)
        if len(r) > 1 and normalize_name(r[1]) == post["key"]
    ]
    plan = Plan()
    if len(matches) > 1:
        plan.reasons.append("同名の行が複数あります")
        return plan
    if matches:
        plan.row = matches[0]
        existing = rows[plan.row - 3]
        if not existing or existing[0] not in {"未入力", "編集中"}:
            plan.reasons.append("未入力・編集中以外の行は自動更新しません")
            return plan
    else:
        last = max(
            (i + 3 for i, r in enumerate(rows) if len(r) > 1 and r[1]), default=2
        )
        plan.row = last + 1
        plan.new = True
        existing = rows[plan.row - 3] if plan.row - 3 < len(rows) else []
        # Formula-only placeholders can be reused, but never unrelated inputs.
        if any(
            v not in (None, "")
            for i, v in enumerate(existing)
            if i + 1 not in (9, 10, 11, 12)
        ):
            plan.reasons.append("新規行の入力欄に既存データがあります")
            return plan
    current = {}
    for c in INPUT_COLUMNS | set("BJKLM"):
        index = column_index(c) - 1
        current[c] = existing[index] if index < len(existing) else ""
    candidates = {
        c: e.value
        for c, e in result.fields.items()
        if c in INPUT_COLUMNS and e.accepted
    }
    candidates.update(C=post["name"])
    if post["category"]:
        candidates["H"] = post["category"]
    else:
        plan.reasons.append("分類不明のため分類と測定値を保留")
    if current["H"] and post["category"] and current["H"] != post["category"]:
        plan.reasons.append("分類が既存行と異なります")
        return plan
    if current["P"] and candidates.get("P") and current["P"] != candidates["P"]:
        for c in "DEF":
            candidates.pop(c, None)
        plan.reasons.append("寮が既存行と異なるため測定値を保留")
    group = [result.fields.get(c) for c in "DEF"]
    complete = (
        all(e and e.accepted for e in group)
        and len({e.source for e in group if e}) == 1
    )
    if complete:
        try:
            complete = calculated_points({**current, **candidates}) is not None
        except (TypeError, ValueError):
            complete = False
    for c in "DEF":
        if current[c] not in (None, "") and str(current[c]) != str(candidates.get(c)):
            complete = False
    if not complete:
        for c in "DEF":
            candidates.pop(c, None)
        plan.reasons.append("D/E/Fの一組を確定できないため保留")
    for c, value in candidates.items():
        if current[c] in (None, ""):
            plan.values[c] = value
        elif (
            normalize_name(current[c]) != normalize_name(value)
            if c == "C"
            else str(current[c]) != str(value)
        ):
            plan.reasons.append(f"{c}列の既存値と候補が異なります")
    if plan.new:
        plan.values["B"] = "未入力"
        plan.formula_values = {
            c: f for c, f in formulas(plan.row).items() if not current[c]
        }
    return plan
