import pytest
from openpyxl import load_workbook

from core.db import DB, STATUS_APPROVED, STATUS_DRAFT, STATUS_REVIEW, ROLE_LAWYER, ROLE_STAFF
from core import rules, listing, export


@pytest.fixture
def env(tmp_path):
    db = DB(":memory:")
    staff = db.login if False else None
    sid = db.add_user("영준", ROLE_STAFF, "pw1")
    lid = db.add_user("김변호사", ROLE_LAWYER, "pw2")
    cid = db.create_case(sid, debtor_name="홍길동", court="서울회생법원", lawyer_id=lid, case_no="2026개회123")
    return db, {"id": sid, "role": ROLE_STAFF}, {"id": lid, "role": ROLE_LAWYER}, cid


def run_rules(db, cid):
    labels = {q["key"]: q["label"] for q in listing.questions()}
    return rules.check(db.creditors(cid), db.claims(cid), db.credit_items(cid),
                       db.get_questionnaire(cid), labels)


def full_claim(**kw):
    base = dict(category="card", cause="카드대금", occurred_date="2024-01-01", principal=1000,
                interest=100, late_fee=10, amount_base_date="2026-11-01")
    base.update(kw)
    return base


def test_login(env):
    db, *_ = env
    assert db.login("영준", "pw1")["role"] == ROLE_STAFF
    assert db.login("영준", "wrong") is None


def test_normalize_name():
    assert rules.same_name("(주)국민카드", "국민카드 주식회사")
    assert rules.same_name("국민카드", "케이비국민카드")
    assert not rules.same_name("신한카드", "국민카드")
    assert not rules.same_name("", "국민카드")


def test_blank_amount_is_unknown_not_zero():
    assert rules.claim_total({"principal": 100, "interest": None, "late_fee": 0}) == rules.UNKNOWN
    assert rules.claim_total({"principal": 100, "interest": 0, "late_fee": 0}) == 100


def test_r1_credit_report_missing(env):
    db, s, l, cid = env
    db.add_credit_item(cid, s["id"], "신한카드")
    db.add_credit_item(cid, s["id"], "(주)국민카드")
    cr = db.add_creditor(cid, s["id"], name="국민카드 주식회사", debt_cert_status="수령")
    db.add_claim(cr, s["id"], **full_claim())
    ws = run_rules(db, cid)
    r1 = [w for w in ws if w.rule == "R1"]
    assert len(r1) == 1 and "신한카드" in r1[0].message


def test_r1_transfer_counts_as_listed(env):
    db, s, l, cid = env
    db.add_credit_item(cid, s["id"], "현대캐피탈")
    cr = db.add_creditor(cid, s["id"], name="에이엔대부", debt_cert_status="수령")
    db.add_claim(cr, s["id"], **full_claim(transferred_from="현대캐피탈"))
    assert not [w for w in run_rules(db, cid) if w.rule == "R1"]


def test_r2_questionnaire(env):
    db, s, l, cid = env
    db.save_questionnaire(cid, s["id"], {"tax": ("있음", ""), "card": ("있음", ""), "phone": ("없음", "")})
    cr = db.add_creditor(cid, s["id"], name="국민카드", debt_cert_status="수령")
    db.add_claim(cr, s["id"], **full_claim(category="card"))
    r2 = [w for w in run_rules(db, cid) if w.rule == "R2"]
    assert [w.id for w in r2] == ["R2:tax"]


def test_r3_r4_r5_r6(env):
    db, s, l, cid = env
    a = db.add_creditor(cid, s["id"], name="현대캐피탈")              # 미요청 → R3
    b = db.add_creditor(cid, s["id"], name="에이엔대부", debt_cert_status="수령")
    db.add_claim(a, s["id"], **full_claim())
    db.add_claim(b, s["id"], **full_claim(transferred_from="현대캐피탈", amount_base_date="2026-10-01"))
    c = db.add_creditor(cid, s["id"], name="김철수", debt_cert_status="수령")
    db.add_claim(c, s["id"], category="personal", cause="차용금", principal=500)  # 빈칸 → R6
    db.add_creditor(cid, s["id"], name="빈채권자", debt_cert_status="수령")       # 채권 없음 → R6
    rs = {w.rule for w in run_rules(db, cid)}
    assert {"R3", "R4", "R5", "R6"} <= rs
    r6 = [w for w in run_rules(db, cid) if w.rule == "R6"]
    assert len(r6) == 2


def test_clean_case_has_no_warnings(env):
    db, s, l, cid = env
    db.add_credit_item(cid, s["id"], "국민카드")
    db.save_questionnaire(cid, s["id"], {"card": ("있음", "")})
    cr = db.add_creditor(cid, s["id"], name="국민카드", debt_cert_status="수령")
    db.add_claim(cr, s["id"], **full_claim())
    assert run_rules(db, cid) == []


def test_approval_rules(env):
    db, s, l, cid = env
    cr = db.add_creditor(cid, s["id"], name="국민카드")  # R3 경고 발생
    db.add_claim(cr, s["id"], **full_claim())
    ws = run_rules(db, cid)
    open_ = rules.open_warnings(ws, db.acks(cid))
    assert len(open_) == 1
    db.set_status(cid, s, STATUS_REVIEW)
    with pytest.raises(PermissionError):           # 사무원은 확정 불가
        db.set_status(cid, s, STATUS_APPROVED, 0)
    with pytest.raises(PermissionError):           # 경고 남아 있으면 불가
        db.set_status(cid, l, STATUS_APPROVED, len(open_))
    with pytest.raises(ValueError):                # 사유 없는 확인 불가
        db.ack_warning(cid, s["id"], open_[0].id, " ")
    db.ack_warning(cid, s["id"], open_[0].id, "대행업체 발급 지연, 신용정보 금액으로 기재")
    open_ = rules.open_warnings(run_rules(db, cid), db.acks(cid))
    assert open_ == []
    db.set_status(cid, l, STATUS_APPROVED, 0)
    case = db.get_case(cid)
    assert case["status"] == STATUS_APPROVED and case["approved_by_name"] == "김변호사"


def test_edit_after_approval_reverts(env):
    db, s, l, cid = env
    cr = db.add_creditor(cid, s["id"], name="국민카드", debt_cert_status="수령")
    clm = db.add_claim(cr, s["id"], **full_claim())
    db.set_status(cid, s, STATUS_REVIEW)
    db.set_status(cid, l, STATUS_APPROVED, 0)
    db.update_creditor(cr, s["id"], debt_cert_received_at="2026-11-20")   # 서류 상태만: 유지
    assert db.get_case(cid)["status"] == STATUS_APPROVED
    db.update_claim(clm, s["id"], principal=2000)                         # 내용 변경: 되돌림
    case = db.get_case(cid)
    assert case["status"] == STATUS_DRAFT and case["approved_by"] is None
    assert any(a["action"] == "상태 되돌림" for a in db.audit(cid))


def test_listing_numbers_and_totals(env):
    db, s, l, cid = env
    a = db.add_creditor(cid, s["id"], name="국민카드", debt_cert_status="수령")
    db.add_claim(a, s["id"], **full_claim())
    db.add_claim(a, s["id"], **full_claim(principal=None))
    b = db.add_creditor(cid, s["id"], name="신한카드", debt_cert_status="수령")
    db.add_claim(b, s["id"], **full_claim(principal=5000, transferred_from="롯데카드"))
    rows, totals = listing.build_rows(db.creditors(cid), db.claims(cid), "서울회생법원")
    assert [r["채권번호"] for r in rows] == ["1-1", "1-2", "2"]
    assert rows[1]["원금"] == rules.UNKNOWN and rows[1]["합계"] == rules.UNKNOWN
    assert totals["합계"] == 1110 + 110 + 5110 and totals["미확인"] == 1
    assert "롯데카드에서 양수" in rows[2]["비고"]


def test_exports(env, tmp_path):
    db, s, l, cid = env
    a = db.add_creditor(cid, s["id"], name="국민카드", address="서울", phone="02-1", fax="02-2")
    db.add_claim(a, s["id"], **full_claim(interest=None))
    case = db.get_case(cid)
    rows, totals = listing.build_rows(db.creditors(cid), db.claims(cid), case["court"])
    p = export.creditor_list_xlsx(case, rows, totals, draft=True, path=tmp_path / "a.xlsx")
    ws = load_workbook(p).active
    assert "[초안]" in ws["A1"].value
    vals = [c.value for row in ws.iter_rows() for c in row]
    assert "국민카드" in vals and "미확인" in vals
    p2 = export.agency_request_xlsx(case, db.creditors(cid), path=tmp_path / "b.xlsx")
    assert "국민카드" in [c.value for row in load_workbook(p2).active.iter_rows() for c in row]
    h = export.creditor_list_html(case, rows, totals, draft=False)
    assert "국민카드" in h and "미확인" in h and "[초안]" not in h


def test_hwp_unavailable_on_linux():
    ok, why = export.hwp_available()
    import sys
    if sys.platform != "win32":
        assert not ok and "Windows" in why
