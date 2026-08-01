import mojimoji


PENDING_STATES = {"未入力", "編集中"}


def normalize_furniture_name(text):
    normalized = mojimoji.han_to_zen(text or "").strip()
    return " ".join(normalized.split()).casefold()


def parse_sync_command(content):
    first_line = (content or "").splitlines()[0].strip() if content else ""
    if first_line.startswith("0:"):
        done_status = False
    elif first_line.startswith("1:"):
        done_status = True
    else:
        return None

    furniture_name = normalize_furniture_name(first_line[2:])
    if not furniture_name:
        return None
    return done_status, furniture_name


def extract_furniture_name(content):
    lines = (content or "").splitlines()
    for line in lines:
        normalized_line = mojimoji.han_to_zen(line).strip()
        if not normalized_line.startswith("家具名："):
            continue
        furniture_name = normalize_furniture_name(normalized_line.split("：", 1)[1])
        return furniture_name or None

    # Screenshot posts use the first line as the furniture name.
    for line in lines:
        furniture_name = normalize_furniture_name(line)
        if furniture_name:
            return furniture_name
    return None


def build_sheet_statuses(rows):
    statuses = {}
    for row in rows:
        state = str(row[0]).strip() if row else ""
        furniture_name = normalize_furniture_name(row[1] if len(row) > 1 else "")
        if not furniture_name:
            continue
        statuses[furniture_name] = state not in PENDING_STATES
    return statuses
