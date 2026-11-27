"""누락 점검 규칙. 설계안 '누락 점검 규칙' 6가지를 그대로 구현합니다.

각 경고는 고정된 id를 가집니다. 같은 문제가 남아 있는 한 id가 바뀌지 않으므로
'확인함' 기록이 유지되고, 문제가 해결되면 경고 자체가 사라집니다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

UNKNOWN = "미확인"

_CORP_WORDS = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(재\)|재단법인|\(사\)|사단법인|\(합\)|합자회사")
_NON_WORD = re.compile(r"[\s\-_.,·()\[\]]")


def normalize_name(name: str | None) -> str:
    """채권자명 비교용: (주)·주식회사·공백·기호를 지우고 비교합니다."""
    if not name:
        return ""
    n = _CORP_WORDS.sub("", name)
    n = _NON_WORD.sub("", n)
    return n.lower()


def same_name(a: str | None, b: str | None) -> bool:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    # 약칭 대비: 한쪽이 다른 쪽을 포함하면 같은 기관으로 봄 (예: 국민카드 / 케이비국민카드)
    return na == nb or (min(len(na), len(nb)) >= 3 and (na in nb or nb in na))


def claim_total(claim: dict):
    """원금+이자+지연손해금. 하나라도 비어 있으면 0으로 계산하지 않고 '미확인'."""
    parts = [claim.get("principal"), claim.get("interest"), claim.get("late_fee")]
    if any(p is None for p in parts):
        return UNKNOWN
    return sum(int(p) for p in parts)


@dataclass
class Warning:
    id: str
    rule: str
    message: str
    target: str = ""


RULE_LABELS = {
    "R1": "신용정보에 있으나 목록에 없음",
    "R2": "문진 '있음'인데 채권 없음",
    "R3": "부채증명서 미수령",
    "R4": "양도 전·후 중복 의심",
    "R5": "금액 기준일 불일치",
    "R6": "금액·발생일 빈칸",
    "R7": "부채증명서와 금액 불일치",
}


def check(creditors: list[dict], claims: list[dict], credit_items: list[dict],
          questionnaire: dict[str, dict], question_labels: dict[str, str],
          certs: dict[int, dict] | None = None) -> list[Warning]:
    """certs: 채권자 id → 가장 최근 부채증명서 (3단계). 없으면 R7을 건너뜀."""
    out: list[Warning] = []

    # R1 신용정보조회서에 있는데 채권자목록에 없는 채권자
    for it in credit_items:
        if not any(same_name(it["institution"], c["name"]) for c in creditors):
            # 양도받은 기관으로 등록된 경우(원채권자 = 신용정보상 기관)도 목록에 있는 것으로 봄
            if any(same_name(it["institution"], cl.get("transferred_from")) for cl in claims):
                continue
            key = normalize_name(it["institution"])
            out.append(Warning(f"R1:{key}", "R1",
                               f"신용정보조회서의 '{it['institution']}'이(가) 채권자목록에 없습니다.",
                               it["institution"]))

    # R2 문진에서 '있음'인데 해당 분류의 채권이 없음
    cats_present = {cl.get("category") for cl in claims if cl.get("category")}
    for key, row in questionnaire.items():
        if row.get("answer") == "있음" and key not in cats_present:
            label = question_labels.get(key, key)
            out.append(Warning(f"R2:{key}", "R2",
                               f"문진에서 '{label}'을(를) '있음'으로 답했는데, 이 분류로 등록된 채권이 없습니다.",
                               label))

    # R3 부채증명서가 아직 안 들어온 채권자
    for c in creditors:
        if c.get("debt_cert_status") != "수령":
            out.append(Warning(f"R3:{c['id']}", "R3",
                               f"'{c['name']}'의 부채증명서가 아직 들어오지 않았습니다 (현재: {c.get('debt_cert_status') or '미요청'}).",
                               c["name"]))

    # R4 양도 전·후 기관이 모두 등록된 중복 의심
    for cl in claims:
        src = cl.get("transferred_from")
        if not src:
            continue
        for c in creditors:
            if c["id"] != cl["creditor_id"] and same_name(src, c["name"]):
                out.append(Warning(f"R4:{cl['id']}:{c['id']}", "R4",
                                   f"'{cl['creditor_name']}'의 채권은 '{src}'에서 양도받은 것인데, "
                                   f"'{c['name']}'도 별도 채권자로 등록되어 있습니다. 중복인지 확인하세요.",
                                   cl["creditor_name"]))

    # R5 금액 기준일이 채권마다 다름
    dates = sorted({cl["amount_base_date"] for cl in claims if cl.get("amount_base_date")})
    if len(dates) > 1:
        out.append(Warning("R5:" + "|".join(dates), "R5",
                           f"채권별 금액 기준일이 서로 다릅니다: {', '.join(dates)}", ""))

    # R6 금액이나 발생일이 비어 있음
    for cl in claims:
        missing = []
        if cl.get("principal") is None:
            missing.append("원금")
        if cl.get("interest") is None:
            missing.append("이자")
        if cl.get("late_fee") is None:
            missing.append("지연손해금")
        if not cl.get("occurred_date"):
            missing.append("발생일")
        if not cl.get("amount_base_date"):
            missing.append("금액 기준일")
        if missing:
            out.append(Warning(f"R6:{cl['id']}:{'/'.join(missing)}", "R6",
                               f"'{cl['creditor_name']}' 채권({cl.get('cause') or '원인 미기재'})에 빈칸: "
                               f"{', '.join(missing)} → '{UNKNOWN}'로 표시됩니다.",
                               cl["creditor_name"]))

    # 채권자가 있는데 채권(계약)이 하나도 없는 경우도 빈칸으로 봄
    with_claims = {cl["creditor_id"] for cl in claims}
    for c in creditors:
        if c["id"] not in with_claims:
            out.append(Warning(f"R6:creditor:{c['id']}", "R6",
                               f"'{c['name']}'에 등록된 채권(계약)이 없습니다.", c["name"]))
    # R7 부채증명서 합계와 입력된 채권 합계가 다름
    for c in creditors:
        cert = (certs or {}).get(c["id"])
        if not cert or cert.get("total") is None:
            continue
        mine = [claim_total(cl) for cl in claims if cl["creditor_id"] == c["id"]]
        if not mine or any(t == UNKNOWN for t in mine):
            continue   # 빈칸은 R6에서 경고
        s = sum(mine)
        if s != cert["total"]:
            out.append(Warning(f"R7:{cert['id']}:{s}", "R7",
                               f"'{c['name']}' 부채증명서 합계는 {cert['total']:,}원(기준일 {cert.get('base_date') or '미확인'})인데 "
                               f"입력된 채권 합계는 {s:,}원입니다 (차이 {cert['total'] - s:+,}원).", c["name"]))
    return out


def open_warnings(warnings: list[Warning], acks: dict[str, dict]) -> list[Warning]:
    return [w for w in warnings if w.id not in acks]
