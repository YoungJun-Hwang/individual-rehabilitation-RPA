"""화면 흐름: 변호사가 사무원 비밀번호 초기화 → 사무원 로그인 시 강제 변경 → 계정 관리에서 다시 변경."""
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
    db.add_user("영준", ROLE_STAFF, "1234")
    db.add_user("김변호사", ROLE_LAWYER, "5678")
    a = AppTest.from_file(APP, default_timeout=30)
    a.run()
    return a, db


def btn(at, label):
    for b in at.button:
        if b.label.startswith(label):
            return b
    raise AssertionError(f"{label!r} 없음: {[b.label for b in at.button]}")


def ti(at, label, nth=0):
    return [t for t in at.text_input if t.label.startswith(label)][nth]


def login(at, name, pw):
    at.selectbox[0].select(name)
    at.text_input[0].input(pw)
    btn(at, "로그인").click().run()
    assert not at.exception, at.exception


def test_reset_and_forced_change(at):
    at, db = at
    login(at, "김변호사", "5678")
    btn(at, "👤 계정 관리").click().run()
    at.selectbox[0].select("영준 (사무원)")
    ti(at, "임시 비밀번호").input("temp1")
    btn(at, "초기화").click().run()
    assert not at.exception, at.exception
    assert any("초기화했습니다" in s.value for s in at.success)
    assert db.login("영준", "temp1")["must_change"]
    btn(at, "로그아웃").click().run()

    login(at, "영준", "temp1")
    assert at.title[0].value == "비밀번호 바꾸기"               # 다른 화면으로 못 감
    ti(at, "받은 임시 비밀번호").input("temp1")
    ti(at, "새 비밀번호 (").input("새비번1")
    ti(at, "새 비밀번호 한 번 더").input("다른값")
    btn(at, "비밀번호 바꾸기").click().run()
    assert any("서로 다릅니다" in e.value for e in at.error)
    ti(at, "받은 임시 비밀번호").input("temp1")
    ti(at, "새 비밀번호 (").input("새비번1")
    ti(at, "새 비밀번호 한 번 더").input("새비번1")
    btn(at, "비밀번호 바꾸기").click().run()
    assert not at.exception, at.exception
    assert any("바꿨습니다" in s.value for s in at.success)
    assert at.header[0].value == "사건 목록"
    assert db.login("영준", "새비번1")["must_change"] is False

    # 계정 관리에서 본인이 다시 바꾸기, 사무원에게는 초기화 양식이 없음
    btn(at, "👤 계정 관리").click().run()
    assert not any(b.label == "초기화" for b in at.button)
    ti(at, "지금 비밀번호").input("새비번1")
    ti(at, "새 비밀번호 (").input("또바꿈2")
    ti(at, "새 비밀번호 한 번 더").input("또바꿈2")
    btn(at, "비밀번호 바꾸기").click().run()
    assert db.login("영준", "또바꿈2")
