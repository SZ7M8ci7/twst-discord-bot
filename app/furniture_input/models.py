from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Evidence:
    value: Any = None
    state: str = "missing"
    source: str = ""
    method: str = ""
    box: tuple = ()
    score: float = 0.0
    reason: str = ""

    @property
    def accepted(self):
        return self.state in {"recognized", "explicit_none"}


@dataclass
class Result:
    fields: dict[str, Evidence] = field(default_factory=dict)
    screens: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def add(self, key, evidence):
        old = self.fields.get(key)
        if old and old.state == "conflict":
            return
        if old and old.accepted and evidence.accepted and old.value != evidence.value:
            self.fields[key] = Evidence(
                state="conflict", reason="画像間で値が一致しません"
            )
        elif not old or not old.accepted:
            self.fields[key] = evidence

    def to_dict(self):
        return asdict(self)
