"""보정명령 화면 흐름: 붙여넣기 → 등록 → 답변·서류 → 검토 요청 → 변호사 승인 → 제출."""
import os

import pytest
from streamlit.testing.v1 import AppTest

from tests.test_correction import ORDER

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")


@pytest.fixture
def at(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDITOR_DB", str(tmp_path / "t.db"))
    import importlib, core.db
    importlib.reload(core.db)
    import streamlit as st
    st.cache_resource.clear()   # 앞 테스트의 DB 연결이 남지 않게
    from core.db import DB, ROLE_STAFF, ROLE_LAWYER
    db = DB(str(tmp_path / "t.db"))
    s = db.add_user("영준", ROLE_STAFF, "1234")
    db.add_user("김변호사", ROLE_LAWYER, "5678")
    db.create_case(s, debtor_name="홍길동", court="서울회생법원")
    a = AppTest.from_file(APP, default_timeout=30)
    a.run()
    return a, db


def btn(at, label):
    for b in at.button:
        if b.label.startswith(label):
            return b
    raise AssertionError(f"{label!r} 없음: {[b.label for b in at.button]}")


def login(at, name, pw):
    at.selectbox[0].select(name)
    at.text_input[0].input(pw)
    btn(at, "로그인").click().run()
    btn(at, "이 사건 열기").click().run()
    btn(at, "보정명령").click().run()
    assert not at.exception, at.exception


def test_correction_flow(at):
    at, db = at
    login(at, "영준", "1234")
    at.text_area[0].input(ORDER)
    at.text_input[0].input("2026-11-20")
    btn(at, "명령문 읽기").click().run()
    assert not at.exception, at.exception
    assert any("2026개회12345" in i.value for i in at.info)       # 사건번호 등록 안내
    btn(at, "등록").click().run()
    assert not at.exception, at.exception
    assert any("항목 6개" in s.value for s in at.success)
    case = db.get_case(1)
    assert case["case_no"] == "2026개회12345"
    o = db.corrections(1)[0]
    assert o["deadline"] == "2026-12-04" and o["days"] == 14
    # 서류 모두 받음, 답변 모두 작성 (DB로 빠르게) → 화면에서 검토 요청
    for it in db.correction_items(o["id"]):
        db.update_item(it["id"], 1, answer="답변")
    for ds in db.correction_docs(o["id"]).values():
        for d in ds:
            db.update_doc(d["id"], 1, status="받음")
    at.run()
    assert any("의뢰인 안내문" in m.value for m in at.markdown)
    btn(at, "검토 요청").click().run()
    assert db.get_correction(o["id"])["status"] == "검토 요청"
    # 변호사 승인
    btn(at, "로그아웃").click().run()
    login(at, "김변호사", "5678")
    chk = next(c for c in at.checkbox if c.label.startswith("항목별 답변"))
    chk.check().run()
    btn(at, "변호사 승인").click().run()
    assert not at.exception, at.exception
    assert db.get_correction(o["id"])["status"] == "변호사 승인"
    btn(at, "제출 완료로 표시").click().run()
    assert db.get_correction(o["id"])["status"] == "제출 완료"
    # 사건 목록의 기한 알림에서 빠짐
    btn(at, "📁 사건 목록").click().run()
    assert not any("보정 기한" in s.value for s in at.subheader)
