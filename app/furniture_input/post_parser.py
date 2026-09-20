import hashlib
import json
import re
from pathlib import Path
import mojimoji

TYPES = tuple(
    f"{group}：{kind}"
    for group, kinds in {
        "内観・外観": ("前景", "壁紙", "床"),
        "家具": ("その他", "収納", "机", "椅子"),
        "装飾": ("パーティション", "ラグ", "写真", "壁装飾"),
        "雑貨": ("大型雑貨", "小型雑貨", "小物雑貨", "衣装"),
    }.items()
    for kind in kinds
)


def normalize_name(text):
    return re.sub(r"\s+", " ", mojimoji.han_to_zen(text or "").strip()).casefold()


def parse_post(content):
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    normalized = [mojimoji.han_to_zen(line) for line in lines]
    categories = {line for line in normalized if line in TYPES}
    if not lines or len(categories) != 1:
        return None
    names = [
        re.sub(r"^家具名\s*[:：]\s*", "", line).strip()
        for line in lines
        if re.match(r"^家具名\s*[:：]", line)
    ]
    if len(set(names)) > 1:
        return None
    name = names[0] if names else lines[0]
    if not name or mojimoji.han_to_zen(name) in TYPES or len(name) > 150:
        return None
    aliases = json.loads(
        Path(__file__).with_name("aliases.json").read_text(encoding="utf-8")
    )
    canonical = {normalize_name(k): v for k, v in aliases.items()}.get(
        normalize_name(name), name
    )
    # Use full-width name notation consistently with the existing sheet.
    canonical = mojimoji.han_to_zen(canonical)
    return {
        "name": canonical,
        "posted_name": name,
        "category": categories.pop(),
        "key": normalize_name(canonical),
    }


def revision_key(message_id, content, attachment_ids, version):
    payload = [str(message_id), content, sorted(map(str, attachment_ids)), version]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
