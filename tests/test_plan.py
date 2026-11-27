import pytest

from core import plan as pl

CFG = {
    "median_income": {"2026": {"1": 2_000_000, "2": 3_000_000, "3": 4_000_000, "4": 5_000_000,
                               "5": 6_000_000, "6": 7_000_000}},
    "living_ratio": 0.6, "discount_rate": 0.05, "default_months": 36, "max_months": 60,
    "min_repay": {"threshold": 50_000_000, "low_rate": 0.05, "high_rate": 0.03, "high_add": 1_000_000},
}


def cr(i, name):
    return {"id": i, "name": name}


def cl(i, cid, p, it=0, lf=0):
    return {"id": i, "creditor_id": cid, "principal": p, "interest": it, "late_fee": lf}


def test_living_cost_and_households():
    assert pl.living_cost("2026", 1, CFG) == 1_200_000
    assert pl.living_cost("2026", 3, CFG) == 2_400_000
    assert pl.median_income("2026", 8, CFG) == 9_000_000        # 6인 + 2×(6인-5인)
    assert pl.living_cost("1999", 1, CFG) is None


def test_minimum_repay():
    assert pl.minimum_repay(40_000_000, CFG) == 2_000_000
    assert pl.minimum_repay(100_000_000, CFG) == 4_000_000


def test_allocate_exact_and_largest_remainder():
    out = pl.allocate(100, [(1, 1), (2, 1), (3, 1)])
    assert sum(out.values()) == 100 and sorted(out.values()) == [33, 33, 34]
    out = pl.allocate(1_000_001, [(1, 3), (2, 7)])
    assert out == {1: 300_000, 2: 700_001}


def test_annuity_pv():
    assert pl.annuity_pv(1000, 12, 0) == 12000
    # 월 0.4167% 할인 36개월: 계수 약 33.37
    assert 33_300 < pl.annuity_pv(1000, 36, 0.05) < 33_400


def base_inputs(**kw):
    d = dict(monthly_income=2_500_000, household=1, median_year="2026", months=36, start_date="2027-01-25")
    d.update(kw)
    return pl.Inputs(**d)


def test_compute_general_only():
    creditors = [cr(1, "국민카드"), cr(2, "신한카드")]
    claims = [cl(10, 1, 30_000_000), cl(11, 2, 10_000_000, 500_000, 100_000)]
    r = pl.compute(base_inputs(), creditors, claims, CFG)
    assert not r.errors
    assert r.living == 1_200_000 and r.available == 1_300_000 and r.payment == 1_300_000
    assert r.general_total == 40_600_000 and r.debt_total == 40_600_000
    # 전액 변제 가능 금액(46.8M) > 채권(40.6M) → 채권액까지만 갚고 경고
    assert sum(x.total_pay for x in r.rows) == r.total_pay == 40_600_000
    assert any("덜 냅니다" in w for w in r.warnings)
    assert all(m["합계"] <= 1_300_000 for m in r.schedule)
    assert r.schedule[0]["날짜"] == "2027-01-25" and r.schedule[1]["날짜"] == "2027-02-25"


def test_compute_priority_first_and_pro_rata():
    creditors = [cr(1, "세무서"), cr(2, "A카드"), cr(3, "B캐피탈"), cr(4, "C은행(담보)")]
    claims = [cl(1, 1, 2_000_000), cl(2, 2, 60_000_000), cl(3, 3, 40_000_000), cl(4, 4, 90_000_000)]
    inp = base_inputs(creditor_types={"1": pl.TYPE_PRIORITY, "4": pl.TYPE_EXCLUDED})
    r = pl.compute(inp, creditors, claims, CFG)
    assert not r.errors
    rows = {x.name: x for x in r.rows}
    assert rows["세무서"].total_pay == 2_000_000 and r.priority_months == 2     # 130만 + 70만
    assert r.schedule[0][1] == 1_300_000 and r.schedule[1][1] == 700_000
    assert r.schedule[1][2] + r.schedule[1][3] == 600_000                       # 둘째 달 남는 돈은 일반채권으로
    assert rows["C은행(담보)"].total_pay == 0 and r.excluded_total == 90_000_000
    gen_paid = rows["A카드"].total_pay + rows["B캐피탈"].total_pay
    assert gen_paid == 46_800_000 - 2_000_000
    assert abs(rows["A카드"].total_pay / gen_paid - 0.6) < 0.0001              # 채권액 비율 6:4
    assert rows["A카드"].regular_month == 780_000 and rows["B캐피탈"].regular_month == 520_000
    assert r.debt_total == 102_000_000 and r.min_required == 4_060_000 and r.min_ok
    assert abs(r.repay_rate - 44_800_000 / 100_000_000) < 1e-9


def test_liquidation_check_and_suggestion():
    creditors, claims = [cr(1, "A")], [cl(1, 1, 100_000_000)]
    inp = base_inputs(assets=[{"name": "임차보증금", "value": 60_000_000, "deduct": 15_000_000},
                              {"name": "예금", "value": 100_000, "deduct": 1_850_000}])
    r = pl.compute(inp, creditors, claims, CFG)
    assert r.liquidation == 45_000_000                     # 공제가 더 큰 항목은 0
    assert not r.pv_ok and r.pv < 45_000_000
    assert "개월로 늘리면" in r.suggestion
    n = int(r.suggestion.split("변제기간을 ")[1].split("개월")[0])
    assert pl.annuity_pv(1_300_000, n, 0.05) >= 45_000_000 > pl.annuity_pv(1_300_000, n - 1, 0.05)
    r2 = pl.compute(base_inputs(assets=[{"name": "집", "value": 200_000_000, "deduct": 0}]), creditors, claims, CFG)
    assert "월 변제금이 최소" in r2.suggestion


def test_errors_and_overrides():
    creditors, claims = [cr(1, "A")], [cl(1, 1, None)]
    r = pl.compute(base_inputs(), creditors, claims, CFG)
    assert any("미확인" in e for e in r.errors)
    r = pl.compute(base_inputs(basis=pl.BASIS_PRINCIPAL), creditors, [cl(1, 1, 5_000_000, None)], CFG)
    assert not r.errors and r.rows[0].claim == 5_000_000
    r = pl.compute(base_inputs(monthly_income=1_000_000), creditors, [cl(1, 1, 5)], CFG)
    assert any("변제할 돈이 없습니다" in e for e in r.errors)
    r = pl.compute(base_inputs(payment_override=1_000_000, living_override=1_000_000, extra_living=200_000),
                   creditors, [cl(1, 1, 50_000_000)], CFG)
    assert r.living == 1_200_000 and r.payment == 1_000_000 and any("가용소득" in w for w in r.warnings)
    r = pl.compute(base_inputs(months=48), creditors, [cl(1, 1, 90_000_000)], CFG)
    assert any("연장 사유" in w for w in r.warnings)


def test_inputs_roundtrip_and_fingerprint():
    i = base_inputs(creditor_types={"1": pl.TYPE_PRIORITY}, assets=[{"name": "x", "value": 1, "deduct": 0}])
    assert pl.Inputs.from_json(i.to_json()) == i
    a = pl.claims_fingerprint([cr(1, "A")], [cl(1, 1, 100)])
    assert a == pl.claims_fingerprint([cr(1, "A")], [cl(1, 1, 100)])
    assert a != pl.claims_fingerprint([cr(1, "A")], [cl(1, 1, 101)])


def test_db_plan_status_flow():
    from core.db import DB, ROLE_LAWYER, ROLE_STAFF
    db = DB(":memory:")
    s = {"id": db.add_user("영준", ROLE_STAFF, "pw"), "role": ROLE_STAFF}
    l = {"id": db.add_user("김변호사", ROLE_LAWYER, "pw"), "role": ROLE_LAWYER}
    cid = db.create_case(s["id"], debtor_name="홍길동")
    with pytest.raises(PermissionError):
        db.set_plan_status(cid, s, "검토 요청", "fp1")
    db.save_plan_inputs(cid, s["id"], base_inputs().to_json())
    db.set_plan_status(cid, s, "검토 요청", "fp1")
    with pytest.raises(PermissionError):
        db.set_plan_status(cid, s, "변호사 확정", "fp1")
    with pytest.raises(PermissionError):
        db.set_plan_status(cid, l, "변호사 확정", "fp1", ["채권자목록 미확정"])
    db.set_plan_status(cid, l, "변호사 확정", "fp1", [])
    assert db.get_plan(cid)["approved_by_name"] == "김변호사"
    assert not db.plan_list_changed(cid, s["id"], "fp1")
    assert db.plan_list_changed(cid, s["id"], "fp2")          # 채권자목록 바뀜 → 되돌림
    assert db.get_plan(cid)["status"] == "작성 중"
    db.set_plan_status(cid, s, "검토 요청", "fp2")
    db.save_plan_inputs(cid, s["id"], base_inputs(months=48).to_json())   # 입력 수정 → 되돌림
    assert db.get_plan(cid)["status"] == "작성 중"


def test_exports(tmp_path):
    from openpyxl import load_workbook
    from core import plan_export
    creditors = [cr(1, "세무서"), cr(2, "A카드"), cr(3, "C은행")]
    claims = [cl(1, 1, 2_000_000), cl(2, 2, 60_000_000), cl(3, 3, 9_000_000)]
    inp = base_inputs(creditor_types={"1": pl.TYPE_PRIORITY, "3": pl.TYPE_EXCLUDED}, months=48,
                      assets=[{"name": "예금", "value": 3_000_000, "deduct": 1_850_000, "note": "압류금지"}])
    r = pl.compute(inp, creditors, claims, CFG)
    case = {"debtor_name": "홍길동", "case_no": "2026개회1", "court": "서울회생법원"}
    wb = load_workbook(plan_export.plan_xlsx(case, inp, r, True, tmp_path / "p.xlsx"))
    assert wb.sheetnames == ["요약", "채권자별", "월별 변제표", "청산가치"]
    assert "[초안]" in wb["요약"]["A1"].value
    sched = wb["월별 변제표"]
    assert sched.max_row == 48 + 2 and sched.cell(1, 3).value == "세무서"
    assert sched.cell(sched.max_row, sched.max_column).value == r.total_pay
    assert wb["청산가치"].cell(2, 4).value == 1_150_000
    h = plan_export.plan_html(case, inp, r, draft=False)
    assert "변 제 계 획 안" in h and "[초안]" not in h and "2회차까지" in h and "C은행" in h
    assert "변제기간을 48개월로 정한 사유" in h
