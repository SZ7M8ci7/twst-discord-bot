"""Observed B1:BF2 headers of the furniture input sheet (2026-09-19)."""

from .sheet_writer import column_index

HEADERS = {
    "B": "状態",
    "C": "名前",
    "D": "いごこち度合計",
    "E": "シリーズボーナス",
    "F": "表示寮ポイント",
    "G": "レアリティ",
    "H": "分類",
    "I": "マス数",
    "J": "基礎ポイント",
    "K": "寮ポイント",
    "L": "テーマ1ポイント",
    "M": "テーマ2ポイント",
    "N": "テーマ1",
    "O": "テーマ2",
    "P": "寮",
    "R": "作成可能数",
    "U": "製作マドル",
    "Y": "木",
    "AA": "枝",
    "AC": "石",
    "AE": "鉱石",
    "AG": "金属",
    "AI": "ガラス",
    "AK": "粘土",
    "AM": "布",
    "AO": "紙",
}


def verify_headers(rows):
    for column, expected in HEADERS.items():
        i = column_index(column) - 1
        actual = "".join(str(row[i]) for row in rows if len(row) > i)
        if "".join(actual.split()) != expected:
            raise ValueError(f"Sheet schema mismatch at {column}")
