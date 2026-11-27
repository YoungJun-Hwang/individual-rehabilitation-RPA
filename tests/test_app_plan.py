"""변제계획안 화면 흐름: 입력 → 계산 → 검토 요청 → 변호사 확정 → 채권 수정 시 되돌림."""
import os

import pytest
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")


@pytest.fixture
def at(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDITOR_DB", str(tmp_path / "t.db"))
    import importlib, core.db
    importlib.reload(core.db)
    import streamlit as st
    st.cache_resource.clear()
    from core.db import DB, ROLE_STAFF, ROLE_LAWYER
    db = DB(str(tmp_path / "t.db"))
    s = {"id": db.add_user("영준", ROLE_STAFF, "1234"), "role": ROLE_STAFF}
    l = {"id": db.add_user("김변호사", ROLE_LAWYER, "5678"), "role": ROLE_LAWYER}
    cid = db.create_case(s["id"], debtor_name="홍길동", court="서울회생법원")
    a = db.add_creditor(cid, s["id"], name="국민카드", debt_cert_status="수령")
    db.add_claim(a, s["id"], principal=30_000_000, interest=0, late_fee=0, occurred_date="2023-01-01",
                 amount_base_date="2026-11-20")
    b = db.add_creditor(cid, s["id"], name="종로세무서", debt_cert_status="수령")
    db.add_claim(b, s["id"], category="tax", principal=1_000_000, interest=0, late_fee=0, occurred_date="2024-01-01",
                 amount_base_date="2026-11-20")
    db.set_status(cid, s, "검토 요청")
    db.set_status(cid, l, "변호사 확인 완료", 0)
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    return app, db


def btn(at, label):
    for b in at.button:
        if b.label.startswith(label):
            return b
    raise AssertionError(f"{label!r} 없음: {[b.label for b in at.button]}")


def ti(at, label):
    return next(t for t in at.text_input if t.label.startswith(label))


def login(at, name, pw):
    at.selectbox[0].select(name)
    at.text_input[0].input(pw)
    btn(at, "로그인").click().run()
    btn(at, "이 사건 열기").click().run()
    btn(at, "변제계획안").click().run()
    assert not at.exception, at.exception


def test_plan_flow(at):
    at, db = at
    login(at, "영준", "1234")
    ti(at, "월 평균 소득").input("2,800,000")
    ti(at, "첫 변제일").input("2027-01-25")
    btn(at, "저장하고 계산").click().run()
    assert not at.exception, at.exception
    p = db.get_plan(1)
    assert p and '"monthly_income": 2800000' in p["inputs_json"]
    assert '"2": "우선(조세 등)"' in p["inputs_json"]                    # 세금 분류 → 우선 자동
    assert any("청산가치 보장" in s.value for s in at.success)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["월 변제금"].endswith("원")
    btn(at, "검토 요청").click().run()
    assert db.get_plan(1)["status"] == "검토 요청"
    btn(at, "로그아웃").click().run()
    login(at, "김변호사", "5678")
    next(c for c in at.checkbox if c.label.startswith("소득·생계비")).check().run()
    btn(at, "변호사 확정").click().run()
    assert not at.exception, at.exception
    assert db.get_plan(1)["status"] == "변호사 확정"
    # 채권자목록이 바뀌면 다음에 화면을 열 때 '작성 중'으로 돌아감
    db.update_claim(1, 1, principal=31_000_000)
    at.run()
    assert db.get_plan(1)["status"] == "작성 중"
    assert any("되돌렸습니다" in w.value for w in at.warning)
