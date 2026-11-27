"""변제계획안 계산 (5단계). 숫자는 모두 여기서 코드로 계산합니다 (GPT 사용 안 함).

계산 순서
1. 생계비 = 기준중위소득(가구원 수) × 60% + 추가 생계비
2. 월 가용소득 = 월 평균 소득 − 생계비  →  월 변제금(직접 정하면 그 값)
3. 총 변제액 = 월 변제금 × 변제기간
4. 우선채권(조세 등)을 먼저 전액 갚고, 남는 돈을 일반채권에 채권액 비율로 나눔 (원 단위 내림, 남는 1원 단위는 큰 채권부터)
5. 검사: 총 변제액의 현재가치 ≥ 청산가치, 총 변제액 ≥ 최저변제액, 월 변제금 ≥ 가용소득
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .rules import UNKNOWN, claim_total

CONFIG = Path(__file__).resolve().parent.parent / "config" / "plan.json"

TYPE_GENERAL, TYPE_PRIORITY, TYPE_EXCLUDED = "일반", "우선(조세 등)", "제외(별제권 등)"
CREDITOR_TYPES = [TYPE_GENERAL, TYPE_PRIORITY, TYPE_EXCLUDED]
BASIS_TOTAL, BASIS_PRINCIPAL = "원금+이자+지연손해금", "원금만"


def config() -> dict:
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


# ---------------- 생계비 ----------------
def median_income(year: str, household: int, cfg: dict | None = None) -> int | None:
    t = (cfg or config())["median_income"].get(str(year))
    if not t or household < 1:
        return None
    if str(household) in t:
        return int(t[str(household)])
    step = int(t["6"]) - int(t["5"])
    return int(t["6"]) + step * (household - 6)


def living_cost(year: str, household: int, cfg: dict | None = None) -> int | None:
    cfg = cfg or config()
    m = median_income(year, household, cfg)
    return None if m is None else math.floor(m * float(cfg["living_ratio"]))


# ---------------- 금액 도우미 ----------------
def annuity_pv(monthly: int, months: int, annual_rate: float) -> int:
    """매달 말 monthly씩 months번 갚을 때의 현재가치."""
    r = annual_rate / 12
    if r == 0:
        return monthly * months
    return math.floor(monthly * (1 - (1 + r) ** -months) / r)


def minimum_repay(debt_total: int, cfg: dict | None = None) -> int:
    m = (cfg or config())["min_repay"]
    if debt_total < m["threshold"]:
        return math.ceil(debt_total * m["low_rate"])
    return math.ceil(debt_total * m["high_rate"] + m["high_add"])


def allocate(amount: int, weights: list[tuple[int, int]]) -> dict[int, int]:
    """amount를 weights[(id, 채권액)] 비율로 원 단위로 나눔. 내림 뒤 남는 원은 채권액이 큰 순서로 1원씩."""
    total = sum(w for _, w in weights)
    if amount <= 0 or total <= 0:
        return {i: 0 for i, _ in weights}
    out = {i: amount * w // total for i, w in weights}
    rest = amount - sum(out.values())
    order = sorted(weights, key=lambda x: (-(amount * x[1] % total), -x[1], x[0]))
    for i, _ in order[:rest]:
        out[i] += 1
    return out


# ---------------- 입력과 결과 ----------------
@dataclass
class Inputs:
    monthly_income: int | None = None
    income_note: str = ""
    household: int = 1
    median_year: str = str(date.today().year)
    living_override: int | None = None      # 생계비를 직접 정할 때
    extra_living: int = 0                   # 추가 생계비
    extra_living_note: str = ""
    months: int = 36
    start_date: str | None = None           # 첫 변제일
    payment_override: int | None = None     # 월 변제금을 직접 정할 때
    basis: str = BASIS_TOTAL
    discount_rate: float | None = None
    creditor_types: dict = field(default_factory=dict)   # {creditor_id(str): 구분}
    assets: list = field(default_factory=list)            # [{name, value, deduct, note}]

    @classmethod
    def from_json(cls, s: str | None) -> "Inputs":
        if not s:
            return cls()
        d = json.loads(s)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, sort_keys=True)


@dataclass
class CreditorRow:
    creditor_id: int
    no: int
    name: str
    kind: str
    claim: int            # 변제 기준 채권액
    share: float = 0.0    # 같은 구분 안에서의 비율
    total_pay: int = 0
    first_month: int = 0  # 일반채권 구간 첫 달 금액 (참고)
    regular_month: int = 0  # 평상시 월 변제액


@dataclass
class PlanResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    living: int | None = None
    living_base: int | None = None
    available: int | None = None
    payment: int | None = None
    months: int = 0
    total_pay: int = 0
    debt_total: int = 0         # 무담보(일반+우선) 채권 총액
    general_total: int = 0
    priority_total: int = 0
    excluded_total: int = 0
    liquidation: int = 0
    pv: int = 0
    pv_ok: bool = False
    min_required: int = 0
    min_ok: bool = False
    repay_rate: float = 0.0     # 일반채권 변제율
    priority_months: int = 0
    rows: list[CreditorRow] = field(default_factory=list)
    schedule: list[dict] = field(default_factory=list)  # [{회차, 날짜, creditor_id: 금액...}]
    suggestion: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors and self.pv_ok and self.min_ok


def liquidation_value(assets: list[dict]) -> int:
    total = 0
    for a in assets:
        v = int(a.get("value") or 0) - int(a.get("deduct") or 0)
        total += max(0, v)
    return total


def claims_fingerprint(creditors: list[dict], claims: list[dict]) -> str:
    """채권자목록 내용이 바뀌었는지 알기 위한 지문."""
    data = [(c["id"], c["name"]) for c in creditors] + \
           [(cl["id"], cl["creditor_id"], cl.get("principal"), cl.get("interest"), cl.get("late_fee")) for cl in claims]
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def compute(inp: Inputs, creditors: list[dict], claims: list[dict], cfg: dict | None = None) -> PlanResult:
    cfg = cfg or config()
    res = PlanResult()
    rate = cfg["discount_rate"] if inp.discount_rate is None else inp.discount_rate

    # 1) 생계비·가용소득
    res.living_base = inp.living_override if inp.living_override is not None else \
        living_cost(inp.median_year, int(inp.household), cfg)
    if res.living_base is None:
        res.errors.append(f"{inp.median_year}년 기준중위소득이 config/plan.json에 없습니다. 추가하거나 생계비를 직접 입력하세요.")
        return res
    res.living = res.living_base + int(inp.extra_living or 0)
    if inp.monthly_income is None:
        res.errors.append("월 평균 소득을 입력하세요.")
        return res
    res.available = int(inp.monthly_income) - res.living
    res.payment = int(inp.payment_override) if inp.payment_override else res.available
    if res.payment <= 0:
        res.errors.append(f"월 가용소득이 {res.available:,}원으로 변제할 돈이 없습니다. 소득·생계비를 확인하세요.")
        return res
    if inp.payment_override and inp.payment_override < res.available:
        res.warnings.append(f"월 변제금({inp.payment_override:,}원)이 가용소득({res.available:,}원)보다 적습니다. "
                            "가용소득 전부를 변제에 써야 하는 것이 원칙입니다.")
    res.months = int(inp.months)
    if not 1 <= res.months <= int(cfg["max_months"]):
        res.errors.append(f"변제기간은 1~{cfg['max_months']}개월이어야 합니다.")
        return res
    if res.months > int(cfg["default_months"]):
        res.warnings.append(f"변제기간이 원칙({cfg['default_months']}개월)보다 깁니다. 청산가치 보장 등 연장 사유를 "
                            "변제계획안에 적어야 합니다.")

    # 2) 채권액
    by_cr: dict[int, list[dict]] = {}
    for cl in claims:
        by_cr.setdefault(cl["creditor_id"], []).append(cl)
    for n, c in enumerate(creditors, start=1):
        kind = inp.creditor_types.get(str(c["id"]), TYPE_GENERAL)
        cls = by_cr.get(c["id"], [])
        if not cls:
            res.errors.append(f"'{c['name']}'에 채권이 없습니다.")
            continue
        if inp.basis == BASIS_PRINCIPAL:
            vals = [cl.get("principal") for cl in cls]
            if any(v is None for v in vals):
                res.errors.append(f"'{c['name']}' 원금이 비어 있습니다.")
                continue
            amt = sum(vals)
        else:
            ts = [claim_total(cl) for cl in cls]
            if any(t == UNKNOWN for t in ts):
                res.errors.append(f"'{c['name']}' 채권 금액에 '미확인' 칸이 있습니다.")
                continue
            amt = sum(ts)
        res.rows.append(CreditorRow(c["id"], n, c["name"], kind, amt))
    if res.errors:
        return res
    gen = [r for r in res.rows if r.kind == TYPE_GENERAL]
    pri = [r for r in res.rows if r.kind == TYPE_PRIORITY]
    res.general_total = sum(r.claim for r in gen)
    res.priority_total = sum(r.claim for r in pri)
    res.excluded_total = sum(r.claim for r in res.rows if r.kind == TYPE_EXCLUDED)
    res.debt_total = res.general_total + res.priority_total
    for group, tot in ((gen, res.general_total), (pri, res.priority_total)):
        for r in group:
            r.share = r.claim / tot if tot else 0.0
    if not gen and not pri:
        res.errors.append("변제할 채권(일반·우선)이 없습니다.")
        return res

    # 3) 월별 변제표: 우선채권 먼저 전액, 남는 돈은 일반채권 비율대로 (일반채권은 채권액을 넘지 않음)
    res.total_pay = res.payment * res.months
    start = date.fromisoformat(inp.start_date) if inp.start_date else None
    pri_left = {r.creditor_id: r.claim for r in pri}
    gen_left = {r.creditor_id: r.claim for r in gen}
    paid = {r.creditor_id: 0 for r in res.rows}
    for k in range(1, res.months + 1):
        money = res.payment
        month = {"회차": k, "날짜": _add_months(start, k - 1).isoformat() if start else ""}
        p_need = sum(pri_left.values())
        if p_need > 0:
            res.priority_months = k
            give = allocate(min(money, p_need), [(r.creditor_id, pri_left[r.creditor_id]) for r in pri
                                                  if pri_left[r.creditor_id] > 0])
            for i, v in give.items():
                v = min(v, pri_left[i])
                pri_left[i] -= v
                paid[i] += v
                month[i] = month.get(i, 0) + v
                money -= v
        g_need = sum(gen_left.values())
        if money > 0 and g_need > 0:
            give = allocate(min(money, g_need), [(r.creditor_id, r.claim) for r in gen if gen_left[r.creditor_id] > 0])
            for i, v in give.items():
                v = min(v, gen_left[i])
                gen_left[i] -= v
                paid[i] += v
                month[i] = month.get(i, 0) + v
                money -= v
        month["합계"] = res.payment - money
        res.schedule.append(month)
    res.total_pay = sum(m["합계"] for m in res.schedule)
    for r in res.rows:
        r.total_pay = paid[r.creditor_id]
        amounts = [m.get(r.creditor_id, 0) for m in res.schedule]
        nz = [a for a in amounts if a]
        r.first_month = nz[0] if nz else 0
        r.regular_month = max(set(nz), key=nz.count) if nz else 0
    if sum(pri_left.values()) > 0:
        res.errors.append(f"변제기간 안에 우선채권을 다 갚지 못합니다 (남는 금액 {sum(pri_left.values()):,}원).")
    if res.total_pay < res.payment * res.months:
        res.warnings.append("총 변제액이 채권 전액을 넘어 마지막 몇 달은 덜 냅니다. 변제기간을 줄일 수 있는지 검토하세요.")
    pay_gen = sum(r.total_pay for r in gen)
    res.repay_rate = pay_gen / res.general_total if res.general_total else 1.0

    # 4) 검사
    res.liquidation = liquidation_value(inp.assets)
    r = rate / 12
    res.pv = math.floor(sum(m["합계"] / (1 + r) ** m["회차"] for m in res.schedule))   # 실제 월별 금액의 현재가치
    res.pv_ok = res.pv >= res.liquidation
    res.min_required = minimum_repay(res.debt_total, cfg)
    res.min_ok = res.total_pay >= min(res.min_required, res.debt_total)
    if not res.pv_ok or not res.min_ok:
        res.suggestion = suggest(res.payment, res.liquidation, res.min_required, res.debt_total, rate, cfg,
                                 res.months)
    return res


def suggest(payment: int, liquidation: int, min_required: int, debt_total: int, rate: float, cfg: dict,
            months_now: int) -> str:
    target_min = min(min_required, debt_total)
    for n in range(months_now + 1, int(cfg["max_months"]) + 1):
        if annuity_pv(payment, n, rate) >= liquidation and payment * n >= target_min:
            return f"변제기간을 {n}개월로 늘리면 청산가치·최저변제액 기준을 모두 넘습니다 (같은 월 변제금 {payment:,}원)."
    n = int(cfg["max_months"])
    r = rate / 12
    factor = (1 - (1 + r) ** -n) / r if r else n
    need = max(math.ceil(liquidation / factor), math.ceil(target_min / n))
    return (f"{n}개월로 늘려도 부족합니다. {n}개월 기준으로 월 변제금이 최소 {need:,}원 필요합니다 "
            f"(현재 {payment:,}원, 부족 {need - payment:,}원).")
