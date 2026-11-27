"""Streamlit 화면 흐름 테스트: 최초 설정 → 사무원 작업 → 변호사 확인."""
import os
import pytest
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")


@pytest.fixture
def at(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDITOR_DB", str(tmp_path / "t.db"))
    import importlib, core.db
    importlib.reload(core.db)
    a = AppTest.from_file(APP, default_timeout=30)
    a.run()
    return a


def btn(at, label):
    for b in at.button:
        if b.label.startswith(label):
            return b
    raise AssertionError(f"button {label!r} not found: {[b.label for b in at.button]}")


def form_btn(at, label):
    return next(b for b in at.button if b.label == label)


def test_full_flow(at):
    # 최초 설정
    assert at.title[0].value == "처음 설정"
    at.text_input(key="sn").input("영준"); at.text_input(key="sp").input("1234")
    at.text_input(key="ln").input("김변호사"); at.text_input(key="lp").input("5678")
    form_btn(at, "계정 만들기").click().run()
    assert at.title[0].value == "로그인"
    # 사무원 로그인
    at.selectbox[0].select("영준"); at.text_input[0].input("1234")
    form_btn(at, "로그인").click().run()
    assert not at.exception
    # 사건 등록
    at.text_input[1].input("홍길동")  # [0]=검색, [1]=채무자 이름
    form_btn(at, "등록").click().run()
    assert at.header[0].value == "사건 기본정보", [h.value for h in at.header]
    # 문진: 세금 있음
    btn(at, "채무 문진").click().run()
    at.radio(key="qa_tax").set_value("있음")
    at.radio(key="qa_card").set_value("있음")
    form_btn(at, "저장").click().run()
    # 채권자 입력
    btn(at, "채권자 입력").click().run()
    at.text_input[0].input("(주)국민카드")
    form_btn(at, "채권자 추가").click().run()
    assert not at.exception, at.exception
    assert any("국민카드" in e.label for e in at.expander)
    # 채권 추가 (원금만, 나머지 빈칸 → 미확인)
    labels = [t.label for t in at.text_input]
    at.text_input[labels.index("원금")].input("1,500,000")
    at.text_input[labels.index("발생원인 (예: 신용카드 이용대금)")].input("카드대금")
    at.selectbox[0].select("card")
    form_btn(at, "채권 추가").click().run()
    assert not at.exception, at.exception
    # 부채증명서 대조 화면이 열림
    btn(at, "부채증명서 대조").click().run()
    assert not at.exception, at.exception
    assert any(h.value == "부채증명서 대조" for h in at.header)
    # 누락 점검: R2(세금), R3(부채증명서), R6(빈칸)
    btn(at, "누락 점검").click().run()
    md = " ".join(m.value for m in at.markdown)
    assert "세금" in md and "부채증명서" in md and "빈칸" in md
    # 미리보기·출력 → 검토 요청
    btn(at, "미리보기·출력").click().run()
    assert not at.exception, at.exception
    btn(at, "검토 요청").click().run()
    assert any("검토 요청" in m.value for m in at.markdown)
    # 로그아웃 → 변호사 로그인
    btn(at, "로그아웃").click().run()
    at.selectbox[0].select("김변호사"); at.text_input[0].input("5678")
    form_btn(at, "로그인").click().run()
    btn(at, "이 사건 열기").click().run()
    btn(at, "미리보기·출력").click().run()
    assert any("처리하지 않은 경고" in e.value for e in at.error)
    assert btn(at, "변호사 확인 완료").disabled
    # 누락 점검에서 모두 확인함
    btn(at, "누락 점검").click().run()
    while True:
        notes = [t for t in at.text_input if t.key and t.key.startswith("note_")]
        if not notes:
            break
        notes[0].input("변호사 검토: 해당 없음")
        btn(at, "확인함").click().run()
        assert not at.exception, at.exception
    btn(at, "미리보기·출력").click().run()
    at.checkbox[0].check().run()
    b = btn(at, "변호사 확인 완료")
    assert not b.disabled
    b.click().run()
    assert any("변호사 확인 완료" in m.value for m in at.markdown)
    # 변경 기록
    btn(at, "변경 기록").click().run()
    assert not at.exception
