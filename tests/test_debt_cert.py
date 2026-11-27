from pathlib import Path

import pytest

from core import debt_cert as dc, ocr, rules
from core.db import DB, ROLE_STAFF
from core.ocr import Line, Page

FX = Path(__file__).parent / "fixtures"
HAS_OCR = ocr.tesseract_status()[0]
needs_ocr = pytest.mark.skipif(not HAS_OCR, reason="Tesseract(kor) 없음")


def lines(*texts):
    return [Page(image=None, lines=[Line(t, (0, i * 20, 500, i * 20 + 15)) for i, t in enumerate(texts)])]


def read(name):
    got = dc.read_certificate((FX / name).read_bytes(), name, "홍길동")
    assert len(got) == 1
    return got[0][0]


# ---------- 글자에서 금액 뽑기 ----------
def test_amounts_skip_dates_and_fix_ocr_noise():
    vals = [a.value for a in dc.amounts_in("카드론 2023.07.15 6,OOO,000| 5.000.000 - 45,000")]
    assert vals == [6_000_000, 5_000_000, 0, 45_000]
    assert [a.value for a in dc.amounts_in("기준일 2026년 11월 20일 현재")] == []
    assert [a.value for a in dc.amounts_in("전화 02-1234-5678 원금 :.7,000,000 원")] == [7_000_000]


def test_table_by_order_when_no_positions():
    r = dc.extract(lines("기준일: 2026. 11. 20. 현재", "채무자: 홍길동",
                         "과목 대출일 대출금액 원금잔액 이자 연체이자 합계",
                         "카드론 2023.07.15 6,000,000 5,000,000 123,456 45,000 5,168,456",
                         "합계 5,000,000 123,456 45,000 5,168,456",      # 대출금액 칸 없음 → 오른쪽 맞춤
                         "위와 같이 증명합니다."), "홍길동")
    c = r.contracts[0]
    assert (c.principal, c.interest, c.late, c.total, c.date) == (5_000_000, 123_456, 45_000, 5_168_456, "2023-07-15")
    assert r.total_row.principal == 5_000_000 and r.base_date == "2026-11-20"
    assert r.debtor_found and not r.checks


def test_key_value_and_estimate_missing_field():
    r = dc.extract(lines("2026.11.15 현재", "원 2 : 7,000,000 원", "이 자 : 310,000 원",
                         "지연손해금 : 95,500 원", "합 계 : 7,405,500 원"), "홍길동")
    c = r.contracts[0]
    assert (c.interest, c.late, c.total) == (310_000, 95_500, 7_405_500)
    assert c.principal == 7_000_000 and r.estimated        # 원금은 합계에서 빼서 추정
    assert not r.debtor_found and any("채무자" in m for m in r.checks)


def test_sum_mismatch_is_reported():
    r = dc.extract(lines("2026.11.15 현재 홍길동", "원금 1,000,000", "이자 10,000", "지연손해금 0", "합계 1,020,000"),
                   "홍길동")
    assert any("합계는 1,020,000원" in m for m in r.checks)


# ---------- 실제 파일 ----------
def test_text_pdf_without_ocr():
    assert not ocr.needs_ocr((FX / "cert_table_text.pdf").read_bytes(), "x.pdf")
    r = read("cert_table_text.pdf")
    assert r.mode == "table" and r.base_date == "2026-11-20"
    assert [(c.principal, c.interest, c.late, c.total) for c in r.contracts] == [
        (5_000_000, 123_456, 45_000, 5_168_456), (3_200_000, 150_000, 42_000, 3_392_000)]
    assert r.summary().total == 8_560_456 and not r.checks


@needs_ocr
def test_scanned_table_pdf():
    r = read("cert_table_scan.pdf")
    assert [(c.label, c.date, c.principal, c.interest, c.late, c.total) for c in r.contracts] == [
        ("카드론", "2023-07-15", 5_000_000, 123_456, 45_000, 5_168_456),
        ("신용카드", "2023-03-01", 3_200_000, 150_000, 42_000, 3_392_000)]
    assert r.base_date == "2026-11-20" and r.debtor_found and not r.checks


@needs_ocr
def test_scanned_photo_key_value():
    r = read("cert_kv_scan.jpg")
    c = r.contracts[0]
    assert (c.principal, c.interest, c.late, c.total, c.date) == (7_000_000, 310_000, 95_500, 7_405_500, "2022-01-10")
    assert r.base_date == "2026-11-15"


@needs_ocr
def test_split_pages(tmp_path):
    from PIL import Image
    a = Image.open(FX / "cert_kv_scan.jpg").convert("RGB")
    p = tmp_path / "two.pdf"
    a.save(p, save_all=True, append_images=[a.copy()], resolution=200)
    got = dc.read_certificate(p.read_bytes(), "two.pdf", "홍길동", split_pages=True)
    assert len(got) == 2 and all(r.summary().total == 7_405_500 for r, _ in got)


# ---------- 대조 · 반영 ----------
def test_guess_creditor_and_targets():
    creditors = [{"id": 1, "name": "국민카드"}, {"id": 2, "name": "에이엔대부"}]
    claims = [{"id": 10, "creditor_id": 2, "transferred_from": "현대캐피탈", "occurred_date": None, "cause": "대출"}]
    assert dc.guess_creditor("채권자 : 주식회사 국민 카드", creditors, claims) == 1
    assert dc.guess_creditor("현대캐피탈 양수채권", creditors, claims) == 2
    assert dc.guess_creditor("아무 말", creditors, claims) is None
    cs = [dc.Contract(label="카드론", date="2023-07-15"), dc.Contract(label="신용카드")]
    cl = [{"id": 1, "occurred_date": None, "cause": "신용카드 이용대금"},
          {"id": 2, "occurred_date": "2023-07-15", "cause": "론"}]
    assert dc.default_targets(cs, cl) == [2, 1]
    assert dc.default_targets([dc.Contract()], []) == [None]


def test_compare():
    s = dc.Contract(principal=100, interest=10, late=0, total=110)
    rows = dc.compare(s, [{"principal": 100, "interest": 5, "late_fee": 0}])
    d = {r["항목"]: r["차이"] for r in rows}
    assert d == {"원금": 0, "이자": 5, "지연손해금": 0, "합계": 5}


def test_db_save_marks_received_and_r7():
    db = DB(":memory:")
    uid = db.add_user("영준", ROLE_STAFF, "pw")
    cid = db.create_case(uid, debtor_name="홍길동")
    cr = db.add_creditor(cid, uid, name="국민카드")
    db.add_claim(cr, uid, principal=100, interest=10, late_fee=0, occurred_date="2024-01-01",
                 amount_base_date="2026-11-01")
    db.save_debt_cert(cid, cr, uid, source_name="a.pdf", base_date="2026-11-20", principal=100, interest=20,
                      late_fee=0, other=None, total=120, contracts=[], ocr_text="", applied=False)
    assert db.creditors(cid)[0]["debt_cert_status"] == "수령"
    ws = rules.check(db.creditors(cid), db.claims(cid), [], {}, {}, db.latest_certs(cid))
    r7 = [w for w in ws if w.rule == "R7"]
    assert len(r7) == 1 and "+10원" in r7[0].message
    assert not [w for w in ws if w.rule == "R3"]
    db.update_claim(db.claims(cid)[0]["id"], uid, interest=20)
    ws = rules.check(db.creditors(cid), db.claims(cid), [], {}, {}, db.latest_certs(cid))
    assert not [w for w in ws if w.rule == "R7"]
