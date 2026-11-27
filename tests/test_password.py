import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from core.db import DB, ROLE_ADMIN, ROLE_LAWYER, ROLE_STAFF

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db():
    d = DB(":memory:")
    d.add_user("영준", ROLE_STAFF, "1234")
    d.add_user("김변호사", ROLE_LAWYER, "5678")
    return d


def u(db, name):
    return db.login(name, {"영준": "1234", "김변호사": "5678"}[name])


def test_change_own_password(db):
    me = u(db, "영준")
    with pytest.raises(PermissionError):
        db.change_password(me["id"], "틀림", "abcd")
    with pytest.raises(ValueError):
        db.change_password(me["id"], "1234", "12")          # 너무 짧음
    with pytest.raises(ValueError):
        db.change_password(me["id"], "1234", "1234")        # 같은 비밀번호
    db.change_password(me["id"], "1234", "새비번99")
    assert db.login("영준", "1234") is None
    assert db.login("영준", "새비번99")["must_change"] is False
    assert db.password_log()[0]["action"] == "비밀번호 변경"


def test_reset_by_lawyer_forces_change(db):
    staff, lawyer = u(db, "영준"), u(db, "김변호사")
    with pytest.raises(PermissionError):
        db.reset_password(staff, lawyer["id"], "temp1")      # 사무원은 불가
    with pytest.raises(PermissionError):
        db.reset_password(lawyer, lawyer["id"], "temp1")     # 본인은 '내 비밀번호 바꾸기'로
    db.reset_password(lawyer, staff["id"], "temp1")
    again = db.login("영준", "temp1")
    assert again and again["must_change"] is True
    db.change_password(staff["id"], "temp1", "내비번77")
    assert db.login("영준", "내비번77")["must_change"] is False
    log = db.password_log()
    assert [r["action"] for r in log] == ["비밀번호 변경", "비밀번호 초기화"]
    assert log[1]["user"] == "김변호사" and log[1]["target"] == "영준"
    assert all("temp1" not in (r["target"] or "") for r in log)   # 비밀번호 자체는 기록 안 함


def test_admin_can_reset(db):
    db.add_user("관리", ROLE_ADMIN, "admin")
    admin = db.login("관리", "admin")
    db.reset_password(admin, u(db, "김변호사")["id"], "temp2")
    assert db.login("김변호사", "temp2")["must_change"]


def test_old_db_gets_column(tmp_path):
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, role TEXT NOT NULL, "
              "pw_hash TEXT NOT NULL, salt TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)")
    c.commit()
    c.close()
    d = DB(p)
    d.add_user("영준", ROLE_STAFF, "1234")
    assert d.login("영준", "1234")["must_change"] is False
    DB(p)   # 두 번 열어도 오류 없음


def test_recovery_tool(tmp_path):
    dbp = tmp_path / "cases.db"
    d = DB(dbp)
    d.add_user("영준", ROLE_STAFF, "1234")
    d.add_user("김변호사", ROLE_LAWYER, "5678")
    d.conn.close()
    env = {**os.environ, "CREDITOR_DB": str(dbp)}
    tool = ROOT / "tools" / "reset_password.py"
    r = subprocess.run([sys.executable, str(tool)], input="1\n새임시1\n새임시1\n", env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1. 김변호사 (변호사)" in r.stdout and "2. 영준 (사무원)" in r.stdout   # 목록은 역할순
    d = DB(dbp)
    assert d.login("김변호사", "5678") is None
    assert d.login("김변호사", "새임시1")["must_change"]
    assert d.password_log()[0]["action"] == "비밀번호 초기화 (관리 도구)"
    r = subprocess.run([sys.executable, str(tool)], input="2\nabcd\nabce\n", env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and d.login("영준", "1234")              # 두 번 다르면 아무것도 안 바뀜
    r = subprocess.run([sys.executable, str(tool)], input="\n", env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "아무것도 바꾸지 않았습니다" in r.stdout
