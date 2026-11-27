from datetime import date

import pytest

from core import correction as co
from core.db import DB, ROLE_LAWYER, ROLE_STAFF

ORDER = """서 울 회 생 법 원
보 정 명 령
사 건 2026개회 12345 개인회생
채 무 자 홍길동
채무자는 이 명령을 송달받은 날부터 14일 안에 다음 사항을 보정하시기 바랍니다.
다 음
1. 채권자목록 관련
 가. 채권자 국민카드의 채권액 산정기준일이 다른 채권자와 다르므로 통일하여
 수정한 채권자목록을 제출할 것.
 나. 신용정보조회서상 신한카드 채무가 누락되어 있으니 확인하여 보정할 것.
2. 재산목록 관련
 가. 채무자 명의 예금계좌 전부에 대한 최근 1년간 거래내역서를 제출할 것.
 나. 2025. 3. 5. 300만 원 출금 내역의 사용처를 소명할 것.
 다. 보유 중인 보험 전부의 해약환급금 예상액 증명서를 제출할 것.
3. 최근 1년간 급여명세서와 근로소득 원천징수영수증을 제출할 것.
2026. 11. 20.
판사 김판사
"""


def test_parse_order_splits_items():
    p = co.parse_order(ORDER)
    assert p.case_no == "2026개회12345" and p.days == 14 and p.order_date == "2026-11-20"
    assert [i.no for i in p.items] == ["1-가", "1-나", "2-가", "2-나", "2-다", "3"]
    first = p.items[0]
    assert first.heading == "채권자목록 관련" and "수정한 채권자목록을 제출할 것." in first.text  # 이어지는 줄 합침
    assert "2025. 3. 5." in p.items[3].text                       # 본문 속 날짜는 명령일로 오인하지 않음
    cats = {i.no: i.category for i in p.items}
    assert cats["1-가"] == "채권자목록" and cats["2-가"] == "재산목록" and cats["3"] == "수입·지출"
    docs = {i.no: i.docs for i in p.items}
    assert any("거래내역" in d for d in docs["2-가"])
    assert any("사용처" in d for d in docs["2-나"])
    assert any("보험" in d for d in docs["2-다"])
    assert any("급여" in d for d in docs["3"]) and any("원천징수" in d for d in docs["3"])
    assert p.items[0].answer          # 분류별 답변 첫 문장


def test_parse_circled_and_plain():
    p = co.parse_order("다음\n① 진술서를 보완할 것\n② 가족관계증명서를 제출할 것")
    assert [(i.no, i.text) for i in p.items] == [("1", "진술서를 보완할 것"), ("2", "가족관계증명서를 제출할 것")]
    assert p.items[1].docs == ["가족관계증명서"]
    p = co.parse_order("부채증명서를 제출하시오.")
    assert len(p.items) == 1 and p.items[0].text == "부채증명서를 제출하시오."


def test_deadline_rules():
    # 2026-11-20(금) 송달 + 14일 = 12-04(금)
    assert co.compute_deadline("2026-11-20", 14, set()) == "2026-12-04"
    # 끝나는 날이 토요일이면 월요일로
    assert co.compute_deadline("2026-11-14", 14, set()) == "2026-11-30"
    # 공휴일이면 다음 평일
    assert co.compute_deadline("2026-12-11", 14, {"2026-12-25"}) == "2026-12-28"
    assert co.d_day("2026-12-04", date(2026, 12, 1)) == 3
    assert co.d_day_label(-2) == "기한 2일 지남" and co.d_day_label(0) == "오늘 마감"


def test_client_message_lists_unrequested_docs():
    docs = [{"name": "급여명세서", "status": "요청 전"}, {"name": "급여명세서", "status": "요청함"},
            {"name": "보험 증명서", "status": "받음"}]
    msg = co.client_message("홍길동", "2099-12-31", docs)
    assert "홍길동님" in msg and "1. 급여명세서" in msg and "보험" not in msg and "12월 27일까지" in msg
    assert co.client_message("홍길동", None, [{"name": "a", "status": "받음"}]) == ""


@pytest.fixture
def env():
    db = DB(":memory:")
    s = db.add_user("영준", ROLE_STAFF, "pw")
    l = db.add_user("김변호사", ROLE_LAWYER, "pw")
    cid = db.create_case(s, debtor_name="홍길동", case_no="2026개회12345", court="서울회생법원")
    p = co.parse_order(ORDER)
    oid = db.create_correction(cid, s, order_date=p.order_date, served_date="2026-11-20", days=14,
                               deadline="2026-12-04", source_name="보정명령.pdf", raw_text=ORDER,
                               items=[vars(i) for i in p.items])
    return db, {"id": s, "role": ROLE_STAFF}, {"id": l, "role": ROLE_LAWYER}, cid, oid


def blockers(db, oid):
    return co.approval_blockers(db.correction_items(oid), db.correction_docs(oid))


def test_approval_flow_and_revert(env):
    db, s, l, cid, oid = env
    items = db.correction_items(oid)
    assert len(items) == 6 and db.get_correction(oid)["seq"] == 1
    assert blockers(db, oid)       # 서류 대기
    db.set_correction_status(oid, s, co.C_REVIEW)
    with pytest.raises(PermissionError):
        db.set_correction_status(oid, s, co.C_APPROVED, [])          # 사무원 불가
    with pytest.raises(PermissionError):
        db.set_correction_status(oid, l, co.C_APPROVED, blockers(db, oid))
    for it in items:
        db.update_item(it["id"], s["id"], answer=f"{it['no']} 답변")   # 수정 → 작성 중으로 되돌아감
    assert db.get_correction(oid)["status"] == co.C_DRAFT
    for docs in db.correction_docs(oid).values():
        for d in docs:
            db.update_doc(d["id"], s["id"], status="받음", received_at="2026-11-25")
    assert blockers(db, oid) == []
    db.set_correction_status(oid, s, co.C_REVIEW)
    db.set_correction_status(oid, l, co.C_APPROVED, [])
    assert db.get_correction(oid)["approved_by_name"] == "김변호사"
    db.set_correction_status(oid, s, co.C_SUBMITTED)
    assert db.get_correction(oid)["submitted_at"]
    assert db.open_corrections() == []


def test_edit_after_approval_reverts(env):
    db, s, l, cid, oid = env
    for it in db.correction_items(oid):
        db.update_item(it["id"], s["id"], answer="답")
    for docs in db.correction_docs(oid).values():
        for d in docs:
            db.update_doc(d["id"], s["id"], status="생략", note="해당 없음")
    db.set_correction_status(oid, s, co.C_REVIEW)
    db.set_correction_status(oid, l, co.C_APPROVED, [])
    it = db.correction_items(oid)[0]
    db.add_doc(it["id"], s["id"], "추가 서류")
    o = db.get_correction(oid)
    assert o["status"] == co.C_DRAFT and o["approved_by"] is None
    assert any(a["action"] == "보정 상태 되돌림" for a in db.audit(cid))


def test_reply_html_and_text(env):
    db, s, l, cid, oid = env
    items = db.correction_items(oid)
    db.update_item(items[0]["id"], s["id"], answer="수정한 채권자목록을 제출합니다.\n<기준일 통일>")
    db.update_item(items[1]["id"], s["id"], answer="")
    d = db.correction_docs(oid)
    some = next(iter(d.values()))[0]
    db.update_doc(some["id"], s["id"], status="받음")
    items, d = db.correction_items(oid), db.correction_docs(oid)
    case, order = db.get_case(cid), db.get_correction(oid)
    h = co.reply_html(case, order, items, d, draft=True)
    assert "보 정 서 [초안]" in h and "2026. 11. 20.자 보정명령(1차)" in h
    assert "&lt;기준일 통일&gt;" in h                     # 글자 그대로 (HTML 이스케이프)
    assert "1. 채권자목록 관련" in h and "(답변 미작성)" in h and some["name"] in h
    t = co.reply_text(case, order, items, d)
    assert t.startswith("1. 채권자목록 관련") and "첨부서류\n1. " in t


def test_second_order_gets_next_seq(env):
    db, s, l, cid, oid = env
    oid2 = db.create_correction(cid, s["id"], order_date=None, served_date=None, days=None, deadline=None,
                                source_name=None, raw_text="x", items=[{"no": "1", "text": "x"}])
    assert db.get_correction(oid2)["seq"] == 2
    assert [o["seq"] for o in db.open_corrections()] == [1, 2]   # 기한 있는 것 먼저
