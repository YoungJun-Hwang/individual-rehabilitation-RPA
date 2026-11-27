"""채권자목록 행 만들기. 화면 미리보기·엑셀·HWP가 모두 이 결과를 씁니다."""
from __future__ import annotations

import json
from pathlib import Path

from .rules import UNKNOWN, claim_total

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

COLUMNS = ["채권번호", "채권자", "주소", "연락처", "채권의 원인", "발생일",
           "원금", "이자", "지연손해금", "합계", "금액 기준일", "비고"]
AMOUNT_COLUMNS = {"원금", "이자", "지연손해금", "합계"}


def load_json(name: str):
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def court_settings(court: str | None) -> dict:
    cfg = load_json("courts.json")
    s = dict(cfg["default"])
    s.update(cfg["courts"].get(court or "", {}))
    return s


def court_names() -> list[str]:
    return list(load_json("courts.json")["courts"])


def questions() -> list[dict]:
    return load_json("questionnaire.json")


def _apply_unit(v, settings):
    if v is None or v == UNKNOWN:
        return v
    unit = int(settings.get("amount_unit") or 1)
    if unit <= 1:
        return int(v)
    if settings.get("round_mode") == "round":
        return int(round(v / unit) * unit)
    return int(v // unit * unit)


def fmt_amount(v) -> str:
    if v is None or v == UNKNOWN:
        return UNKNOWN
    return f"{int(v):,}"


def build_rows(creditors: list[dict], claims: list[dict], court: str | None = None) -> tuple[list[dict], dict]:
    """채권자목록 행 목록과 합계를 돌려줍니다.
    채권자 하나에 계약이 여러 개면 채권번호를 1-1, 1-2처럼 붙입니다.
    금액이 비어 있으면 0이 아니라 '미확인'이며, 합계는 확인된 금액만 더하고 '미확인 n건'을 따로 표시합니다.
    """
    settings = court_settings(court)
    by_cr: dict[int, list[dict]] = {}
    for cl in claims:
        by_cr.setdefault(cl["creditor_id"], []).append(cl)

    rows: list[dict] = []
    totals = {"원금": 0, "이자": 0, "지연손해금": 0, "합계": 0, "미확인": 0}
    for i, cr in enumerate(creditors, start=1):
        cls = by_cr.get(cr["id"], [])
        contact = " / ".join(x for x in [
            f"전화 {cr['phone']}" if cr.get("phone") else "",
            f"팩스 {cr['fax']}" if cr.get("fax") else "",
        ] if x)
        if not cls:
            rows.append({"채권번호": str(i), "채권자": cr["name"], "주소": cr.get("address") or "",
                         "연락처": contact, "채권의 원인": UNKNOWN, "발생일": UNKNOWN,
                         "원금": UNKNOWN, "이자": UNKNOWN, "지연손해금": UNKNOWN, "합계": UNKNOWN,
                         "금액 기준일": UNKNOWN, "비고": "등록된 채권 없음"})
            totals["미확인"] += 1
            continue
        for j, cl in enumerate(cls, start=1):
            no = str(i) if len(cls) == 1 else f"{i}-{j}"
            notes = []
            if cl.get("transferred_from"):
                notes.append(f"{cl['transferred_from']}에서 양수")
            if cl.get("evidence"):
                notes.append(f"근거: {cl['evidence']}")
            if cl.get("note"):
                notes.append(cl["note"])
            p = _apply_unit(cl.get("principal"), settings)
            it = _apply_unit(cl.get("interest"), settings)
            lf = _apply_unit(cl.get("late_fee"), settings)
            tot = claim_total({"principal": p, "interest": it, "late_fee": lf})
            row = {"채권번호": no, "채권자": cr["name"], "주소": cr.get("address") or "",
                   "연락처": contact, "채권의 원인": cl.get("cause") or UNKNOWN,
                   "발생일": cl.get("occurred_date") or UNKNOWN,
                   "원금": UNKNOWN if p is None else p, "이자": UNKNOWN if it is None else it,
                   "지연손해금": UNKNOWN if lf is None else lf, "합계": tot,
                   "금액 기준일": cl.get("amount_base_date") or UNKNOWN, "비고": "; ".join(notes)}
            rows.append(row)
            for k in ("원금", "이자", "지연손해금"):
                if isinstance(row[k], int):
                    totals[k] += row[k]
            if not isinstance(tot, int):
                totals["미확인"] += 1
    # 합계 = 확인된 금액(원금·이자·지연손해금)만 더한 값. 미확인 칸은 0으로 보지 않고 건수만 따로 셉니다.
    totals["합계"] = totals["원금"] + totals["이자"] + totals["지연손해금"]
    return rows, totals


def display_rows(rows: list[dict]) -> list[dict]:
    return [{k: (fmt_amount(v) if k in AMOUNT_COLUMNS else v) for k, v in r.items()} for r in rows]
