from pathlib import Path

import pytest

from core import credit_pdf as cp
from core.rules import normalize_name

FX = Path(__file__).parent / "fixtures"


def load(name):
    return (FX / name).read_bytes()


def by_inst(res, name):
    return [c for c in res.candidates if normalize_name(c.institution) == normalize_name(name)]


def test_table_layout_reads_and_converts_units():
    res = cp.parse(load("credit_table.pdf"))
    names = {normalize_name(c.institution) for c in res.candidates}
    assert {"국민은행", "현대캐피탈", "ok저축은행", "신한카드", "삼성카드"} <= names
    kb = by_inst(res, "국민은행")[0]
    assert kb.amount == 12_345_000          # 잔액 열 우선, 천원 → 원
    assert kb.date == "2022-03-15" and kb.product == "신용대출" and kb.page == 1
    assert "대출" in kb.section and kb.bbox


def test_inquiry_records_are_skipped():
    res = cp.parse(load("credit_table.pdf"))
    assert not by_inst(res, "카카오뱅크")


def test_defaults_zero_and_overdue_duplicate():
    res = cp.parse(load("credit_table.pdf"))
    samsung = by_inst(res, "삼성카드")[0]
    assert not samsung.include and "0원" in samsung.reason
    hyundai = by_inst(res, "현대캐피탈")
    assert len(hyundai) == 2
    assert hyundai[0].include and not hyundai[1].include and "연체" in hyundai[1].reason


def test_text_layout_fallback():
    res = cp.parse(load("credit_text.pdf"))
    got = {c.institution: c for c in res.candidates}
    assert set(got) == {"우리카드", "에이엔대부", "국민건강보험공단"}   # 합계·조회기록 제외
    assert got["우리카드"].amount == 3_456_789 and got["우리카드"].product == "카드론"
    assert got["우리카드"].date == "2023-04-11"


def test_unit_and_amount_helpers():
    assert cp.find_unit("(단위 : 천원)") == 1000
    assert cp.find_unit("단위: 원") == 1
    assert cp.parse_amount("1,234", 1000) == 1_234_000
    assert cp.parse_amount("미확인") is None
    assert cp.parse_date("2024년 3월 5일") == "2024-03-05"


def test_scanned_pdf_reports_note(tmp_path):
    from PIL import Image
    p = tmp_path / "scan.pdf"
    Image.new("RGB", (200, 200), "white").save(p)
    res = cp.parse(p.read_bytes())
    assert res.scanned_pages == [1] and res.notes and not res.candidates


def test_encrypted_pdf_needs_password(tmp_path):
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(FX / "credit_table.pdf"))
    w.encrypt("900101")
    p = tmp_path / "enc.pdf"
    w.write(p)
    with pytest.raises(cp.PasswordRequired):
        cp.parse(p.read_bytes())
    with pytest.raises(cp.PasswordRequired):
        cp.parse(p.read_bytes(), "000000")
    assert cp.parse(p.read_bytes(), "900101").candidates


def test_render_page():
    data = load("credit_table.pdf")
    res = cp.parse(data)
    img = cp.render_page(data, 1, [(c.bbox, c.include) for c in res.candidates])
    assert img.size[0] > 100
