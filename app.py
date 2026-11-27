"""개인회생 채권자목록 정리·누락 점검 프로그램 (1단계)

실행: 실행.bat 더블클릭 (또는 `streamlit run app.py`)
모든 자료는 이 PC의 data/cases.db 파일에만 저장되며 인터넷으로 나가지 않습니다.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core import correction, credit_pdf, debt_cert, export, listing, ocr, plan, plan_export, rules
from core.db import (DB, DEBT_CERT_STATES, ROLE_ADMIN, ROLE_LAWYER, ROLE_STAFF, ROLES,
                     STATUS_APPROVED, STATUS_DRAFT, STATUS_REVIEW, STATUSES)

st.set_page_config(page_title="채권자목록 정리", page_icon="📋", layout="wide")


@st.cache_resource
def get_db() -> DB:
    return DB()


db = get_db()
ss = st.session_state
QUESTIONS = listing.questions()
Q_LABELS = {q["key"]: q["label"] for q in QUESTIONS}
CATEGORY_OPTIONS = [""] + [q["key"] for q in QUESTIONS]


# ---------------- 입력값 처리 ----------------
def parse_amount(s: str):
    """'1,234,000' → 1234000, 빈칸 → None(미확인). 숫자가 아니면 ValueError."""
    s = (s or "").replace(",", "").replace("원", "").strip()
    if not s:
        return None
    if not re.fullmatch(r"-?\d+", s):
        raise ValueError(f"금액은 숫자로 입력하세요: {s}")
    return int(s)


def parse_date(s: str):
    """'2024.1.5' / '2024-01-05' / '20240105' → '2024-01-05', 빈칸 → None."""
    s = (s or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})[.\-/ ]?\s*(\d{1,2})[.\-/ ]?\s*(\d{1,2})\.?", s)
    if not m:
        raise ValueError(f"날짜 형식이 아닙니다: {s} (예: 2024-01-05)")
    y, mo, d = map(int, m.groups())
    return date(y, mo, d).isoformat()


def amt_str(v) -> str:
    return "" if v is None else f"{int(v):,}"


def warnings_for(case_id):
    ws = rules.check(db.creditors(case_id), db.claims(case_id), db.credit_items(case_id),
                     db.get_questionnaire(case_id), Q_LABELS, db.latest_certs(case_id))
    acks = db.acks(case_id)
    return ws, acks, rules.open_warnings(ws, acks)


def status_badge(status: str) -> str:
    color = {STATUS_DRAFT: "gray", STATUS_REVIEW: "orange", STATUS_APPROVED: "green"}[status]
    return f":{color}[**{status}**]"


# ---------------- 로그인 / 최초 설정 ----------------
def first_run():
    st.title("처음 설정")
    st.write("사용할 계정을 만듭니다. **사무원 계정**과 **변호사 계정**을 각각 만들어 주세요. "
             "'변호사 확인 완료'는 변호사 계정에서만 할 수 있습니다.")
    with st.form("setup"):
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("사무원")
            sn = st.text_input("이름", key="sn")
            sp = st.text_input("비밀번호", type="password", key="sp")
        with c2:
            st.subheader("담당 변호사")
            ln = st.text_input("이름", key="ln")
            lp = st.text_input("비밀번호", type="password", key="lp")
        if st.form_submit_button("계정 만들기", type="primary"):
            if not all([sn.strip(), sp, ln.strip(), lp]):
                st.error("이름과 비밀번호를 모두 입력하세요.")
            elif sn.strip() == ln.strip():
                st.error("두 계정의 이름이 같습니다.")
            elif len(sp) < 4 or len(lp) < 4:
                st.error("비밀번호는 4자 이상으로 해 주세요.")
            else:
                db.add_user(sn, ROLE_STAFF, sp)
                db.add_user(ln, ROLE_LAWYER, lp)
                st.success("계정을 만들었습니다. 로그인하세요.")
                st.rerun()


def login_screen():
    st.title("로그인")
    with st.form("login"):
        names = [u["name"] for u in db.users() if u["active"]]
        name = st.selectbox("사용자", names)
        pw = st.text_input("비밀번호", type="password")
        if st.form_submit_button("로그인", type="primary"):
            u = db.login(name, pw)
            if u:
                ss.user = u
                st.rerun()
            else:
                st.error("비밀번호가 맞지 않습니다.")


# ---------------- 화면: 사건 목록 ----------------
def deadline_board():
    opens = db.open_corrections()
    if not opens:
        return
    st.subheader("⏰ 보정 기한")
    for o in opens[:10]:
        n = correction.d_day(o["deadline"])
        color = "red" if n is not None and n <= 3 else ("orange" if n is not None and n <= 7 else "gray")
        c1, c2 = st.columns([5, 1])
        c1.markdown(f":{color}[**{correction.d_day_label(n)}**] · {o['debtor_name']} ({o['case_no'] or '번호 없음'}) · "
                    f"{o['seq']}차 보정 · 기한 {o['deadline'] or '미정'} · {o['status']}")
        if c2.button("보정 열기", key=f"dl_{o['id']}"):
            ss.case_id = o["case_id"]
            ss.page = "보정명령"
            ss[f"corrsel_{o['case_id']}"] = o["id"]
            st.rerun()
    st.divider()


def page_cases():
    st.header("사건 목록")
    deadline_board()
    c1, c2 = st.columns([3, 1])
    kw = c1.text_input("검색 (채무자 이름, 사건번호)")
    stf = c2.selectbox("진행 상태", ["전체"] + STATUSES)
    cases = db.list_cases(kw, None if stf == "전체" else stf)
    if cases:
        df = pd.DataFrame(cases).rename(columns={
            "id": "ID", "case_no": "사건번호", "debtor_name": "채무자", "court": "법원", "status": "상태",
            "updated_at": "최근 수정", "lawyer_name": "담당 변호사", "creditor_count": "채권자 수"})
        st.dataframe(df[["ID", "사건번호", "채무자", "법원", "담당 변호사", "채권자 수", "상태", "최근 수정"]],
                     hide_index=True, width="stretch")
        opts = {f"{c['debtor_name']} ({c['case_no'] or '번호 없음'}) · {c['status']}": c["id"] for c in cases}
        pick = st.selectbox("열 사건", list(opts))
        if st.button("이 사건 열기", type="primary"):
            ss.case_id = opts[pick]
            ss.page = "사건 기본정보"
            st.rerun()
    else:
        st.info("등록된 사건이 없습니다.")

    with st.expander("새 사건 등록", expanded=not cases):
        lawyers = db.users(ROLE_LAWYER)
        with st.form("new_case", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("채무자 이름 *")
            no = c2.text_input("사건번호 (없으면 비워 두기)")
            court = c1.selectbox("관할 법원", listing.court_names())
            lw = c2.selectbox("담당 변호사", [l["name"] for l in lawyers]) if lawyers else None
            if st.form_submit_button("등록", type="primary"):
                if not name.strip():
                    st.error("채무자 이름을 입력하세요.")
                else:
                    lid = next((l["id"] for l in lawyers if l["name"] == lw), None)
                    ss.case_id = db.create_case(ss.user["id"], debtor_name=name.strip(), case_no=no.strip() or None,
                                                court=court, lawyer_id=lid)
                    ss.page = "사건 기본정보"
                    st.rerun()


# ---------------- 화면: 기본정보 ----------------
def page_basic(case):
    st.header("사건 기본정보")
    lawyers = db.users(ROLE_LAWYER)
    courts = listing.court_names()
    with st.form("basic"):
        c1, c2 = st.columns(2)
        name = c1.text_input("채무자 이름 *", case["debtor_name"])
        no = c2.text_input("사건번호", case["case_no"] or "")
        birth = c1.text_input("생년월일 (주민등록번호 전체는 넣지 마세요)", case["debtor_birth"] or "")
        phone = c2.text_input("연락처", case["debtor_phone"] or "")
        addr = st.text_input("주소", case["debtor_address"] or "")
        court = c1.selectbox("관할 법원", courts, index=courts.index(case["court"]) if case["court"] in courts else 0)
        lnames = [l["name"] for l in lawyers]
        lw = c2.selectbox("담당 변호사", lnames,
                          index=lnames.index(case["lawyer_name"]) if case["lawyer_name"] in lnames else 0) if lnames else None
        base = c1.text_input("채권자목록 기준일 (예: 2026-11-30)", case["list_base_date"] or "")
        memo = st.text_area("메모", case["memo"] or "")
        s = listing.court_settings(court)
        if s.get("note"):
            st.caption(f"법원 메모: {s['note']}")
        if st.form_submit_button("저장", type="primary"):
            try:
                db.update_case(case["id"], ss.user["id"], debtor_name=name.strip(), case_no=no.strip() or None,
                               debtor_birth=birth.strip() or None, debtor_phone=phone.strip() or None,
                               debtor_address=addr.strip() or None, court=court,
                               lawyer_id=next((l["id"] for l in lawyers if l["name"] == lw), None),
                               list_base_date=parse_date(base), memo=memo.strip() or None)
                st.success("저장했습니다.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    with st.expander("사건 삭제"):
        st.warning("사건과 그 안의 채권자·채권 정보가 모두 지워집니다. 되돌릴 수 없습니다.")
        confirm = st.text_input("삭제하려면 채무자 이름을 똑같이 입력하세요")
        if st.button("사건 삭제", disabled=confirm.strip() != case["debtor_name"]):
            db.delete_case(case["id"], ss.user["id"])
            ss.case_id = None
            ss.page = "사건 목록"
            st.rerun()


# ---------------- 화면: 채무 문진 ----------------
def page_questionnaire(case):
    st.header("채무 문진")
    st.caption("의뢰인에게 하나씩 물어보고 답을 체크하세요. '있음'으로 답한 항목에 채권이 등록되지 않으면 누락 점검에서 경고가 뜹니다.")
    cur = db.get_questionnaire(case["id"])
    with st.form("q"):
        answers = {}
        for q in QUESTIONS:
            row = cur.get(q["key"], {})
            c1, c2, c3 = st.columns([3, 2, 4])
            c1.markdown(f"**{q['label']}**")
            opts = ["모름", "있음", "없음"]
            a = c2.radio(q["label"], opts, index=opts.index(row.get("answer", "모름")), horizontal=True,
                         key=f"qa_{q['key']}", label_visibility="collapsed")
            n = c3.text_input("메모", row.get("note") or "", key=f"qn_{q['key']}", label_visibility="collapsed",
                              placeholder="메모 (예: 어머니에게 300만 원)")
            answers[q["key"]] = (a, n.strip() or None)
        if st.form_submit_button("저장", type="primary"):
            db.save_questionnaire(case["id"], ss.user["id"], answers)
            st.success("저장했습니다.")
            st.rerun()
    unknown = [q["label"] for q in QUESTIONS if cur.get(q["key"], {}).get("answer", "모름") == "모름"]
    if unknown:
        st.info("아직 답을 받지 못한 항목: " + ", ".join(unknown))


# ---------------- 화면: 채권자 입력 ----------------
def claim_form(prefix, creditor_id, claim=None):
    claim = claim or {}
    with st.form(f"{prefix}_form", clear_on_submit=not claim):
        c1, c2, c3 = st.columns(3)
        cat = c1.selectbox("분류 (문진 항목)", CATEGORY_OPTIONS,
                           index=CATEGORY_OPTIONS.index(claim.get("category") or ""),
                           format_func=lambda k: Q_LABELS.get(k, "선택 안 함"))
        cause = c2.text_input("발생원인 (예: 신용카드 이용대금)", claim.get("cause") or "")
        occ = c3.text_input("발생일 (예: 2023-05-10)", claim.get("occurred_date") or "")
        c4, c5, c6, c7 = st.columns(4)
        p = c4.text_input("원금", amt_str(claim.get("principal")))
        i = c5.text_input("이자", amt_str(claim.get("interest")))
        l = c6.text_input("지연손해금", amt_str(claim.get("late_fee")))
        base = c7.text_input("금액 기준일", claim.get("amount_base_date") or "")
        c8, c9 = st.columns(2)
        tr = c8.text_input("양도 이력: 원래 채권자 (양수한 경우만)", claim.get("transferred_from") or "")
        ev = c9.text_input("근거 서류 (예: 부채증명서 2026-11-20자)", claim.get("evidence") or "")
        note = st.text_input("비고", claim.get("note") or "")
        st.caption("금액을 모르면 비워 두세요. 0으로 계산하지 않고 '미확인'으로 표시됩니다. 이자가 정말 0원이면 0을 넣으세요.")
        if st.form_submit_button("채권 저장" if claim else "채권 추가", type="primary"):
            try:
                f = dict(category=cat or None, cause=cause.strip() or None, occurred_date=parse_date(occ),
                         principal=parse_amount(p), interest=parse_amount(i), late_fee=parse_amount(l),
                         amount_base_date=parse_date(base), transferred_from=tr.strip() or None,
                         evidence=ev.strip() or None, note=note.strip() or None)
            except ValueError as e:
                st.error(str(e))
                return
            if claim:
                db.update_claim(claim["id"], ss.user["id"], **f)
            else:
                db.add_claim(creditor_id, ss.user["id"], **f)
            st.rerun()


def page_creditors(case):
    st.header("채권자 입력")
    st.caption("채권자별로 계약(채권)을 따로 입력합니다. 한 채권자에 카드대금·카드론이 따로 있으면 채권을 두 개 추가하세요.")
    creditors = db.creditors(case["id"])
    claims = db.claims(case["id"])
    with st.expander("➕ 채권자 추가", expanded=not creditors):
        with st.form("add_cr", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("채권자명 *")
            biz = c2.text_input("사업자번호")
            addr = st.text_input("주소")
            c3, c4 = st.columns(2)
            ph = c3.text_input("전화")
            fx = c4.text_input("팩스")
            if st.form_submit_button("채권자 추가", type="primary"):
                if not name.strip():
                    st.error("채권자명을 입력하세요.")
                elif any(rules.same_name(name, c["name"]) for c in creditors):
                    st.error(f"비슷한 이름의 채권자가 이미 있습니다. 같은 채권자면 그 아래에 채권을 추가하세요.")
                else:
                    db.add_creditor(case["id"], ss.user["id"], name=name.strip(), biz_no=biz.strip() or None,
                                    address=addr.strip() or None, phone=ph.strip() or None, fax=fx.strip() or None)
                    st.rerun()

    for n, cr in enumerate(creditors, start=1):
        cls = [c for c in claims if c["creditor_id"] == cr["id"]]
        totals = [rules.claim_total(c) for c in cls]
        known = sum(t for t in totals if isinstance(t, int))
        unk = sum(1 for t in totals if not isinstance(t, int))
        label = f"{n}. {cr['name']} — 채권 {len(cls)}건, {known:,}원" + (f" (+ 미확인 {unk}건)" if unk else "")
        with st.expander(label):
            t1, t2, t3 = st.tabs(["채권(계약)", "채권자 정보", "삭제"])
            with t1:
                if cls:
                    df = pd.DataFrame([{
                        "분류": Q_LABELS.get(c["category"] or "", ""), "발생원인": c["cause"] or "",
                        "발생일": c["occurred_date"] or "미확인", "원금": amt_str(c["principal"]) or "미확인",
                        "이자": amt_str(c["interest"]) or "미확인", "지연손해금": amt_str(c["late_fee"]) or "미확인",
                        "합계": listing.fmt_amount(rules.claim_total(c)), "기준일": c["amount_base_date"] or "미확인",
                        "양도 이력": c["transferred_from"] or ""} for c in cls])
                    st.dataframe(df, hide_index=True, width="stretch")
                    ed = st.selectbox("수정할 채권", ["새 채권 추가"] + [f"{j}. {c['cause'] or '원인 미기재'}"
                                                                    for j, c in enumerate(cls, start=1)],
                                      key=f"pick_{cr['id']}")
                else:
                    ed = "새 채권 추가"
                if ed == "새 채권 추가":
                    claim_form(f"new_{cr['id']}", cr["id"])
                else:
                    c = cls[int(ed.split(".")[0]) - 1]
                    claim_form(f"edit_{c['id']}", cr["id"], c)
                    if st.button("이 채권 삭제", key=f"delcl_{c['id']}"):
                        db.delete_claim(c["id"], ss.user["id"])
                        st.rerun()
            with t2:
                with st.form(f"cr_{cr['id']}"):
                    c1, c2 = st.columns(2)
                    name = c1.text_input("채권자명", cr["name"])
                    biz = c2.text_input("사업자번호", cr["biz_no"] or "")
                    addr = st.text_input("주소", cr["address"] or "")
                    c3, c4, c5 = st.columns(3)
                    ph = c3.text_input("전화", cr["phone"] or "")
                    fx = c4.text_input("팩스", cr["fax"] or "")
                    order = c5.number_input("순서", value=int(cr["sort_order"]), step=1)
                    if st.form_submit_button("저장"):
                        if not name.strip():
                            st.error("채권자명을 입력하세요.")
                        else:
                            db.update_creditor(cr["id"], ss.user["id"], name=name.strip(), biz_no=biz.strip() or None,
                                               address=addr.strip() or None, phone=ph.strip() or None,
                                               fax=fx.strip() or None, sort_order=int(order))
                            st.rerun()
            with t3:
                st.warning(f"'{cr['name']}'과(와) 그 아래 채권 {len(cls)}건이 지워집니다.")
                if st.checkbox("삭제를 확인합니다", key=f"delok_{cr['id']}"):
                    if st.button("채권자 삭제", key=f"delcr_{cr['id']}"):
                        db.delete_creditor(cr["id"], ss.user["id"])
                        st.rerun()


# ---------------- 화면: 신용정보 대조 ----------------
def pdf_import(case):
    """2단계: 신용정보조회서 PDF 자동 읽기. 읽은 결과는 후보로만 보여 주고, 원문과 대조해 고른 것만 저장."""
    key = f"pdf_{case['id']}"
    flash = ss.pop(f"flash_{case['id']}", None)
    if flash:
        st.success(flash)
    stt = ss.get(key)
    with st.expander("📄 PDF에서 자동으로 읽기", expanded=stt is not None):
        if stt is None:
            up = st.file_uploader("신용정보조회서 PDF", type=["pdf"], key=f"up_{case['id']}")
            pw = st.text_input("PDF 비밀번호 (걸려 있을 때만)", type="password", key=f"pw_{case['id']}")
            st.caption("파일은 메모리에서만 읽고 저장하지 않습니다. 읽은 결과는 바로 저장되지 않고, "
                       "원문과 대조해 고른 항목만 저장됩니다.")
            if st.button("읽기", type="primary", disabled=up is None):
                data = up.getvalue()
                try:
                    res = credit_pdf.parse(data, pw or None)
                except credit_pdf.PasswordRequired as e:
                    st.error(f"{e} 비밀번호를 넣고 다시 '읽기'를 누르세요.")
                    return
                except ValueError as e:
                    st.error(str(e))
                    return
                ss[key] = {"name": up.name, "data": data, "pw": pw or None, "pages": res.page_count,
                           "notes": res.notes, "cands": [c.as_dict() for c in res.candidates]}
                st.rerun()
            return

        st.markdown(f"**{stt['name']}** · {stt['pages']}쪽 · 후보 {len(stt['cands'])}건")
        for n in stt["notes"]:
            st.warning(n)
        if not stt["cands"]:
            if st.button("다른 파일 올리기"):
                del ss[key]
                st.rerun()
            return
        st.caption("초록 칸은 가져올 항목, 회색 칸은 제외한 항목입니다. 원문 그림과 대조해 '가져오기'를 고르고, "
                   "잘못 읽은 칸은 표에서 바로 고치세요. 금액은 원 단위입니다.")
        df = pd.DataFrame([{"가져오기": c["include"], "기관명": c["institution"], "금액": c["amount"],
                            "메모": c["reason"], "상품": c["product"] or "", "일자": c["date"] or "",
                            "구분": c["section"], "쪽": c["page"], "원문": c["raw"]} for c in stt["cands"]])
        left, right = st.columns([3, 2])
        with left:
            edited = st.data_editor(
                df, hide_index=True, width="stretch", key=f"ed_{case['id']}",
                disabled=["구분", "쪽", "메모", "원문"],
                column_config={"가져오기": st.column_config.CheckboxColumn(width="small"),
                               "금액": st.column_config.NumberColumn("금액(원)", format="localized", step=1),
                               "쪽": st.column_config.NumberColumn(width="small")})
        with right:
            pages = sorted({c["page"] for c in stt["cands"]})
            pg = st.selectbox("원문 쪽", pages, format_func=lambda p: f"{p}쪽", key=f"pg_{case['id']}") \
                if len(pages) > 1 else pages[0]
            boxes = [(tuple(c["bbox"]) if c["bbox"] else None, bool(edited.iloc[i]["가져오기"]))
                     for i, c in enumerate(stt["cands"]) if c["page"] == pg]
            st.image(credit_pdf.render_page(stt["data"], pg, boxes, stt["pw"]), width="stretch")
        chosen = edited[edited["가져오기"]]
        c1, c2 = st.columns([1, 4])
        if c1.button(f"선택한 {len(chosen)}건 가져오기", type="primary", disabled=chosen.empty):
            items = []
            for i, r in chosen.iterrows():
                src = stt["cands"][i]
                amt = None if pd.isna(r["금액"]) else int(r["금액"])
                note = " · ".join(x for x in [f"PDF {src['page']}쪽", src["section"], r["일자"]] if x)
                items.append({"institution": str(r["기관명"]).strip(), "product": str(r["상품"]).strip() or None,
                              "amount": amt, "note": note})
            added, skipped = db.import_credit_items(case["id"], ss.user["id"], items, stt["name"])
            del ss[key]
            ss[f"flash_{case['id']}"] = (f"{added}건을 가져왔습니다." +
                                         (f" (같은 기관·금액이 이미 있어 {skipped}건은 건너뜀)" if skipped else ""))
            st.rerun()
        if c2.button("취소"):
            del ss[key]
            st.rerun()


def page_credit(case):
    st.header("신용정보조회서 항목")
    st.caption("신용정보조회서에 나온 기관을 입력합니다. 채권자목록에 없는 기관이 있으면 누락 점검에서 경고가 뜹니다.")
    pdf_import(case)
    items = db.credit_items(case["id"])
    creditors = db.creditors(case["id"])
    claims = db.claims(case["id"])
    if items:
        rows = []
        for it in items:
            matched = next((c["name"] for c in creditors if rules.same_name(it["institution"], c["name"])), None)
            if not matched:
                via = next((cl for cl in claims if rules.same_name(it["institution"], cl.get("transferred_from"))), None)
                matched = f"{via['creditor_name']} (양수)" if via else None
            rows.append({"기관": it["institution"], "상품": it["product"] or "", "금액": amt_str(it["amount"]),
                         "목록 대응": matched or "❌ 없음", "비고": it["note"] or ""})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        pick = st.selectbox("삭제할 항목", ["선택 안 함"] + [f"{i + 1}. {it['institution']}" for i, it in enumerate(items)])
        if pick != "선택 안 함" and st.button("삭제"):
            db.delete_credit_item(items[int(pick.split(".")[0]) - 1]["id"], ss.user["id"])
            st.rerun()
    with st.form("credit_add", clear_on_submit=True):
        c1, c2, c3 = st.columns([3, 2, 2])
        inst = c1.text_input("기관명 *")
        prod = c2.text_input("상품 (대출·카드 등)")
        amt = c3.text_input("금액")
        note = st.text_input("비고")
        if st.form_submit_button("항목 추가", type="primary"):
            try:
                if not inst.strip():
                    raise ValueError("기관명을 입력하세요.")
                db.add_credit_item(case["id"], ss.user["id"], inst, prod.strip() or None, parse_amount(amt),
                                   note.strip() or None)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    if items and st.button("목록에 없는 기관을 채권자로 한꺼번에 추가"):
        added = 0
        for it in items:
            if not any(rules.same_name(it["institution"], c["name"]) for c in db.creditors(case["id"])) and \
               not any(rules.same_name(it["institution"], cl.get("transferred_from")) for cl in claims):
                db.add_creditor(case["id"], ss.user["id"], name=it["institution"])
                added += 1
        st.success(f"{added}곳을 채권자로 추가했습니다. 채권자 입력 화면에서 채권 내용을 채워 주세요.")


# ---------------- 화면: 누락 점검 ----------------
def page_check(case):
    st.header("누락 점검")
    ws, acks, open_ = warnings_for(case["id"])
    c1, c2, c3 = st.columns(3)
    c1.metric("전체 경고", len(ws))
    c2.metric("처리 안 됨", len(open_))
    c3.metric("확인함", len(ws) - len(open_))
    if not ws:
        st.success("경고가 없습니다.")
        return
    if open_:
        st.warning(f"처리하지 않은 경고가 {len(open_)}건 있습니다. 모두 처리해야 '변호사 확인 완료'로 바꿀 수 있습니다. "
                   "문제를 고치면 경고가 사라지고, 고칠 필요가 없으면 사유를 적고 '확인함'을 누르세요.")
    for rule_key, rule_label in rules.RULE_LABELS.items():
        group = [w for w in ws if w.rule == rule_key]
        if not group:
            continue
        st.subheader(f"{rule_label} ({len(group)})")
        for w in group:
            ack = acks.get(w.id)
            with st.container(border=True):
                if ack:
                    st.markdown(f"✅ ~~{w.message}~~")
                    st.caption(f"확인함: {ack['user_name']}({ack['role']}) · {ack['acked_at']} · 사유: {ack['note']}")
                    if st.button("확인 취소", key=f"un_{w.id}"):
                        db.unack_warning(case["id"], ss.user["id"], w.id)
                        st.rerun()
                else:
                    st.markdown(f"⚠️ {w.message}")
                    c1, c2 = st.columns([5, 1])
                    note = c1.text_input("확인 사유", key=f"note_{w.id}", label_visibility="collapsed",
                                         placeholder="확인 사유 (예: 부채증명서 발급 지연, 신용정보 금액으로 기재)")
                    if c2.button("확인함", key=f"ack_{w.id}"):
                        try:
                            db.ack_warning(case["id"], ss.user["id"], w.id, note)
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))


# ---------------- 화면: 발급대행 요청 목록 ----------------
def page_agency(case):
    st.header("발급대행 요청 목록")
    st.caption("부채증명서 발급대행 업체에 보낼 기관 목록을 엑셀로 만들고, 기관별로 받았는지 체크합니다.")
    creditors = db.creditors(case["id"])
    if not creditors:
        st.info("먼저 채권자를 입력하세요.")
        return
    df = pd.DataFrame([{"id": c["id"], "기관명": c["name"], "상태": c["debt_cert_status"] or "미요청",
                        "요청일": c["debt_cert_requested_at"] or "", "수령일": c["debt_cert_received_at"] or ""}
                       for c in creditors])
    edited = st.data_editor(
        df, hide_index=True, width="stretch", disabled=["id", "기관명"],
        column_config={"id": None, "상태": st.column_config.SelectboxColumn("상태", options=DEBT_CERT_STATES, required=True),
                       "요청일": st.column_config.TextColumn("요청일 (예: 2026-11-20)"),
                       "수령일": st.column_config.TextColumn("수령일")},
        key=f"agency_{case['id']}")
    c1, c2, c3 = st.columns(3)
    if c1.button("상태 저장", type="primary"):
        try:
            for _, r in edited.iterrows():
                orig = next(c for c in creditors if c["id"] == r["id"])
                req, rec = parse_date(r["요청일"]), parse_date(r["수령일"])
                if r["상태"] == "수령" and not rec:
                    rec = date.today().isoformat()
                if r["상태"] in ("요청함", "수령") and not req:
                    req = orig["debt_cert_requested_at"] or date.today().isoformat()
                if (r["상태"], req, rec) != (orig["debt_cert_status"], orig["debt_cert_requested_at"],
                                             orig["debt_cert_received_at"]):
                    db.update_creditor(int(r["id"]), ss.user["id"], debt_cert_status=r["상태"],
                                       debt_cert_requested_at=req, debt_cert_received_at=rec)
            st.success("저장했습니다.")
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    if c2.button("미요청 기관을 '요청함'으로"):
        for c in creditors:
            if c["debt_cert_status"] == "미요청":
                db.update_creditor(c["id"], ss.user["id"], debt_cert_status="요청함",
                                   debt_cert_requested_at=date.today().isoformat())
        st.rerun()
    targets = [c for c in creditors if c["debt_cert_status"] != "수령"]
    only_pending = st.checkbox("아직 수령하지 않은 기관만 엑셀에 넣기", value=True)
    path = export.agency_request_xlsx(case, targets if only_pending else creditors)
    c3.download_button("요청 목록 엑셀 받기", path.read_bytes(), file_name=path.name,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    recv = sum(1 for c in creditors if c["debt_cert_status"] == "수령")
    st.progress(recv / len(creditors), text=f"수령 {recv} / {len(creditors)}")


# ---------------- 화면: 부채증명서 대조 (3단계) ----------------
NEW_CLAIM = "➕ 새 채권으로 추가"
CERT_COLS = {"principal": "원금", "interest": "이자", "late": "지연손해금", "other": "기타", "total": "합계"}


def _num(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) or v == "" else int(v)


def _claim_option(j, cl):
    return f"{j}. {cl['cause'] or '원인 미기재'} ({cl['occurred_date'] or '발생일 미확인'}, " \
           f"{listing.fmt_amount(rules.claim_total(cl))})"


def page_debt_cert(case):
    st.header("부채증명서 대조")
    st.caption("받은 부채증명서(스캔본 PDF·사진·전자 PDF)를 올리면 기준일과 금액을 읽어, 입력된 채권과 대조합니다. "
               "읽은 값은 원문과 대조해 확인한 뒤에만 반영됩니다. 파일은 저장하지 않습니다.")
    cid = case["id"]
    qkey = f"certq_{cid}"
    flash = ss.pop(f"certflash_{cid}", None)
    if flash:
        st.success(flash)
    creditors = db.creditors(cid)
    if not creditors:
        st.info("먼저 채권자를 입력하세요.")
        return
    ok, why = ocr.tesseract_status()
    if not ok:
        st.warning(why)

    queue = ss.get(qkey) or []
    if not queue:
        with st.form(f"cert_up_{cid}", clear_on_submit=True):
            files = st.file_uploader("부채증명서 파일 (여러 개 가능)", type=["pdf", "jpg", "jpeg", "png", "tif", "tiff"],
                                     accept_multiple_files=True)
            c1, c2 = st.columns(2)
            rot = c1.selectbox("회전 (옆으로 누운 스캔본)", [0, 90, 180, 270], format_func=lambda d: f"{d}°")
            split = c2.checkbox("한 파일에 여러 증명서가 있음 (쪽마다 따로 읽기)")
            if st.form_submit_button("읽기", type="primary"):
                if not files:
                    st.error("파일을 올리세요.")
                else:
                    claims = db.claims(cid)
                    items, errors = [], []
                    with st.spinner("읽는 중... (스캔본은 한 쪽에 몇 초 걸립니다)"):
                        for f in files:
                            try:
                                got = debt_cert.read_certificate(f.getvalue(), f.name, case["debtor_name"], rot, split)
                            except ocr.OcrUnavailable as e:
                                errors.append(f"{f.name}: {e}")
                                continue
                            except Exception as e:
                                errors.append(f"{f.name}: 읽지 못했습니다 ({e})")
                                continue
                            for n, (res, pages) in enumerate(got, start=1):
                                name = f.name + (f" ({n}쪽)" if split and len(got) > 1 else "")
                                guess = debt_cert.guess_creditor(res.text, creditors, claims)
                                items.append({"name": name, "res": res, "pages": pages, "guess": guess})
                    for e in errors:
                        st.error(e)
                    if items:
                        ss[qkey] = items
                        st.rerun()
    else:
        _review_cert(case, queue, creditors)

    st.divider()
    st.subheader("등록된 부채증명서")
    certs = db.debt_certs(cid)
    if not certs:
        st.caption("아직 없습니다.")
        return
    latest = db.latest_certs(cid)
    claims = db.claims(cid)
    rows = []
    for c in certs:
        mine = [rules.claim_total(cl) for cl in claims if cl["creditor_id"] == c["creditor_id"]]
        s = sum(mine) if mine and all(t != rules.UNKNOWN for t in mine) else None
        state = "" if latest.get(c["creditor_id"], {}).get("id") != c["id"] else (
            "✅ 일치" if s is not None and s == c["total"] else
            ("❓ 비교 불가" if s is None or c["total"] is None else f"❌ 차이 {c['total'] - s:+,}원"))
        rows.append({"채권자": c["creditor_name"], "기준일": c["base_date"] or "미확인",
                     "증명서 합계": listing.fmt_amount(c["total"]), "입력된 채권 합계": listing.fmt_amount(s),
                     "대조": state or "(이전 증명서)", "채권 반영": "예" if c["applied"] else "아니오",
                     "파일": c["source_name"], "등록": f"{c['user_name']} {c['created_at']}"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    with st.expander("증명서 기록 삭제"):
        pick = st.selectbox("삭제할 기록", [f"{c['id']}: {c['creditor_name']} · {c['source_name']}" for c in certs])
        if st.button("기록 삭제"):
            db.delete_debt_cert(int(pick.split(":")[0]), ss.user["id"])
            st.rerun()


def _review_cert(case, queue, creditors):
    cid = case["id"]
    qkey = f"certq_{cid}"
    item = queue[0]
    res = item["res"]
    st.markdown(f"#### 확인 {1}/{len(queue)} · {item['name']}")
    cr_names = {c["id"]: c["name"] for c in creditors}
    ids = list(cr_names)
    default = ids.index(item["guess"]) if item["guess"] in ids else 0
    left, right = st.columns([3, 2])
    with left:
        c1, c2 = st.columns(2)
        cr_id = c1.selectbox("어느 채권자의 증명서인가요?", ids, index=default, format_func=lambda i: cr_names[i],
                             key=f"cert_cr_{cid}_{item['name']}")
        if item["guess"] is None:
            c1.caption("⚠️ 증명서에서 채권자 이름을 찾지 못했습니다. 직접 고르세요.")
        elif res.issuer_line:
            c1.caption(f"증명서의 채권자 줄: {res.issuer_line}")
        base = c2.text_input("금액 기준일", res.base_date or "", key=f"cert_base_{cid}_{item['name']}")
        if res.debtor_found is False or res.checks:
            for m in res.checks:
                st.warning(m)
        for m in res.estimated:
            st.warning("추정값: " + m)

        claims = [cl for cl in db.claims(cid) if cl["creditor_id"] == cr_id]
        options = [NEW_CLAIM] + [_claim_option(j, cl) for j, cl in enumerate(claims, start=1)]
        targets = debt_cert.default_targets(res.contracts, claims)
        contracts = res.contracts or [debt_cert.Contract()]
        df = pd.DataFrame([{
            "반영할 채권": options[1 + [cl["id"] for cl in claims].index(t)] if t else NEW_CLAIM,
            "과목": c.label, "발생일": c.date or "", **{CERT_COLS[f]: getattr(c, f) for f in CERT_COLS}}
            for c, t in zip(contracts, targets + [None] * (len(contracts) - len(targets)))])
        for v in CERT_COLS.values():
            df[v] = pd.to_numeric(df[v], errors="coerce")   # 빈칸을 'None' 대신 빈칸으로 표시
        st.markdown("**증명서에서 읽은 계약** — 원문과 다르면 칸을 직접 고치세요. 계약을 더하거나 뺄 수도 있습니다.")
        edited = st.data_editor(
            df, hide_index=True, width="stretch", num_rows="dynamic", key=f"cert_ed_{cid}_{item['name']}_{cr_id}",
            column_config={**{v: st.column_config.NumberColumn(v, format="localized", step=1) for v in CERT_COLS.values()},
                           "반영할 채권": st.column_config.SelectboxColumn(options=options, required=True)})
        rows = []
        for _, r in edited.iterrows():
            row = {f: _num(r[CERT_COLS[f]]) for f in CERT_COLS}
            row.update(label=str(r["과목"] or "").strip(), date=str(r["발생일"] or "").strip() or None,
                       target=r["반영할 채권"])
            if any(row[f] is not None for f in CERT_COLS):
                rows.append(row)
        # 합계: 각 계약 합계(없으면 원금+이자+지연손해금+기타)를 더함
        sums = {}
        for f in ("principal", "interest", "late", "other"):
            vals = [r[f] for r in rows]
            sums[f] = sum(vals) if vals and all(v is not None for v in vals) else (
                None if f != "other" else sum(v or 0 for v in vals) or None)
        totals = [r["total"] if r["total"] is not None else (
            None if any(r[f] is None for f in ("principal", "interest", "late"))
            else r["principal"] + r["interest"] + r["late"] + (r["other"] or 0)) for r in rows]
        cert_total = sum(totals) if totals and all(t is not None for t in totals) else None
        summary = debt_cert.Contract(principal=sums["principal"], interest=sums["interest"], late=sums["late"],
                                     other=sums["other"], total=cert_total)
        for i, (r, t) in enumerate(zip(rows, totals), start=1):
            comp = None if any(r[f] is None for f in ("principal", "interest", "late")) else \
                r["principal"] + r["interest"] + r["late"] + (r["other"] or 0)
            if r["total"] is not None and comp is not None and comp != r["total"]:
                st.error(f"{i}번 계약: 원금+이자+지연손해금+기타 = {comp:,}원 ≠ 합계 {r['total']:,}원")
        st.markdown("**입력된 채권과 대조**")
        cmp = debt_cert.compare(summary, claims)
        st.dataframe(pd.DataFrame([{k: (listing.fmt_amount(v) if k != "항목" else v) if k != "차이" else
                                    ("" if v is None else ("✅ 0" if v == 0 else f"{v:+,}")) for k, v in r.items()}
                                   for r in cmp]), hide_index=True, width="stretch")
        if summary.other:
            st.info(f"증명서에 기타(비용·수수료 등) {summary.other:,}원이 있습니다. 채권 칸에는 원금·이자·지연손해금만 "
                    "들어가므로, 반영하면 이 금액만큼 차이가 남습니다. 처리 방법은 변호사와 정하세요.")
        targets_used = [r["target"] for r in rows if r["target"] != NEW_CLAIM]
        dup = len(targets_used) != len(set(targets_used))
        if dup:
            st.error("같은 채권에 계약 두 개를 반영하도록 되어 있습니다. '반영할 채권'을 고치세요.")
        try:
            base_iso = parse_date(base)
        except ValueError as e:
            st.error(str(e))
            base_iso = "invalid"
        b1, b2, b3 = st.columns(3)
        can = bool(rows) and not dup and base_iso != "invalid"
        if b1.button("확인하고 채권에 반영", type="primary", disabled=not can):
            _save_cert(case, item, cr_id, base_iso, rows, summary, claims, apply=True)
        if b2.button("증명서만 기록 (채권은 그대로)", disabled=not can):
            _save_cert(case, item, cr_id, base_iso, rows, summary, claims, apply=False)
        if b3.button("건너뛰기"):
            ss[qkey] = queue[1:]
            st.rerun()
    with right:
        pages = item["pages"]
        pn = st.selectbox("원문 쪽", list(range(1, len(pages) + 1)), format_func=lambda p: f"{p}쪽",
                          key=f"cert_pg_{cid}_{item['name']}") if len(pages) > 1 else 1
        marks = [(bbox, color) for p, bbox, color in res.marks if p == pn]
        st.image(ocr.draw_boxes(pages[pn - 1], marks), width="stretch")
        st.caption("초록 = 금액을 읽은 줄, 주황 = 기준일, 회색 = 표 머리글" +
                   (" · 글자 인식(OCR)으로 읽음" if pages[pn - 1].ocr else " · 전자 문서에서 바로 읽음"))
        with st.expander("읽은 글 전체"):
            st.text(res.text)


def _save_cert(case, item, cr_id, base_iso, rows, summary, claims, apply: bool):
    cid = case["id"]
    uid = ss.user["id"]
    by_option = {_claim_option(j, cl): cl for j, cl in enumerate(claims, start=1)}
    evidence = f"부채증명서 ({base_iso or '기준일 미확인'} 기준)"
    changed = 0
    if apply:
        cats = {cl["category"] for cl in claims}
        for r in rows:
            f = dict(principal=r["principal"], interest=r["interest"], late_fee=r["late"],
                     amount_base_date=base_iso, evidence=evidence)
            if r["other"]:
                f["note"] = f"증명서상 기타 비용 {r['other']:,}원 별도"
            cl = by_option.get(r["target"])
            if cl:
                if not cl["occurred_date"] and r["date"]:
                    f["occurred_date"] = r["date"]
                if not cl["cause"] and r["label"]:
                    f["cause"] = r["label"]
                db.update_claim(cl["id"], uid, **f)
            else:
                db.add_claim(cr_id, uid, cause=r["label"] or None, occurred_date=r["date"],
                             category=next(iter(cats)) if len(cats) == 1 else None, **f)
            changed += 1
    db.save_debt_cert(cid, cr_id, uid, source_name=item["name"], base_date=base_iso,
                      principal=summary.principal, interest=summary.interest, late_fee=summary.late,
                      other=summary.other, total=summary.total, contracts=rows, ocr_text=item["res"].text,
                      applied=apply)
    q = ss.get(f"certq_{cid}") or []
    ss[f"certq_{cid}"] = q[1:]
    ss[f"certflash_{cid}"] = (f"'{item['name']}'을(를) 기록했습니다" +
                              (f" · 채권 {changed}건에 반영" if apply else " (채권은 그대로)") +
                              " · 부채증명서 상태를 '수령'으로 바꿨습니다.")
    st.rerun()


# ---------------- 화면: 미리보기·출력 ----------------
def page_output(case):
    st.header("미리보기 · 출력")
    st.markdown(f"현재 상태: {status_badge(case['status'])}"
                + (f" — {case['approved_by_name']} 변호사 확인 ({case['approved_at']})" if case["approved_at"] else ""))
    rows, totals = listing.build_rows(db.creditors(case["id"]), db.claims(case["id"]), case["court"])
    ws, acks, open_ = warnings_for(case["id"])
    if not rows:
        st.info("채권자가 없습니다.")
        return
    st.dataframe(pd.DataFrame(listing.display_rows(rows)), hide_index=True, width="stretch")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("원금 합계", f"{totals['원금']:,}원")
    c2.metric("이자 합계", f"{totals['이자']:,}원")
    c3.metric("지연손해금 합계", f"{totals['지연손해금']:,}원")
    c4.metric("확인된 채무 합계", f"{totals['합계']:,}원")
    if totals["미확인"]:
        st.warning(f"금액이 비어 있는 채권이 {totals['미확인']}건 있습니다. 위 합계는 확인된 금액만 더한 값이라 실제 채무액은 더 클 수 있습니다.")

    st.divider()
    st.subheader("검토 · 확인")
    user = ss.user
    if open_:
        st.error(f"처리하지 않은 경고 {len(open_)}건이 있어 '변호사 확인 완료'로 바꿀 수 없습니다. 누락 점검 화면에서 처리하세요.")
    b1, b2, b3 = st.columns(3)
    if case["status"] == STATUS_DRAFT and b1.button("검토 요청", type="primary"):
        db.set_status(case["id"], user, STATUS_REVIEW)
        st.rerun()
    if case["status"] == STATUS_REVIEW:
        if user["role"] == ROLE_LAWYER:
            ok = st.checkbox("채권자목록 내용을 직접 확인했고, 위 내용으로 확정합니다.")
            if b2.button("변호사 확인 완료", type="primary", disabled=bool(open_) or not ok):
                try:
                    db.set_status(case["id"], user, STATUS_APPROVED, len(open_))
                    st.rerun()
                except PermissionError as e:
                    st.error(str(e))
        else:
            b2.info("변호사 확인을 기다리는 중입니다. (변호사 계정으로 로그인해야 확인할 수 있습니다)")
    if case["status"] != STATUS_DRAFT and b3.button("작성 중으로 되돌리기"):
        db.set_status(case["id"], user, STATUS_DRAFT)
        st.rerun()
    st.caption("확인 완료 뒤 채권 내용을 고치면 자동으로 '작성 중'으로 돌아갑니다.")

    st.divider()
    st.subheader("파일 출력")
    approved = case["status"] == STATUS_APPROVED
    draft = not approved
    if draft:
        st.info("변호사 확인 전이라 **초안**으로 출력됩니다 (파일 이름과 제목에 '초안' 표시). "
                "제출용 파일은 '변호사 확인 완료' 뒤에 출력하세요.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**엑셀**")
        path = export.creditor_list_xlsx(case, rows, totals, draft)
        st.download_button("채권자목록 엑셀 받기", path.read_bytes(), file_name=path.name,
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with c2:
        st.markdown("**한글(HWP)**")
        ok, why = export.hwp_available()
        has_tpl = export.HWP_TEMPLATE.exists()
        st.caption(f"양식 파일: {'있음 (templates/채권자목록_양식.hwp)' if has_tpl else '없음 → 기본 표 형식으로 만듭니다'}")
        if not ok:
            st.warning(why)
        use_tpl = st.checkbox("사무소 양식 사용", value=has_tpl, disabled=not has_tpl)
        if st.button("HWP 만들기", disabled=not ok):
            with st.spinner("한글 프로그램으로 만드는 중..."):
                try:
                    out, mode = export.creditor_list_hwp(case, rows, totals, draft, use_template=use_tpl)
                    db.log(case["id"], user["id"], "HWP 출력", {"파일": out.name, "방식": mode, "초안": draft})
                    db.conn.commit()
                    ss.last_hwp = str(out)
                    st.success(f"만들었습니다 ({mode}): {out}")
                except Exception as e:
                    st.error(f"HWP 출력 실패: {e}")
        if ss.get("last_hwp"):
            from pathlib import Path
            p = Path(ss.last_hwp)
            if p.exists():
                st.download_button("방금 만든 HWP 받기", p.read_bytes(), file_name=p.name)
    with st.expander("인쇄용 HTML 미리보기"):
        st.download_button("HTML 받기", export.creditor_list_html(case, rows, totals, draft).encode("utf-8"),
                           file_name="채권자목록.html", mime="text/html")


# ---------------- 화면: 변제계획안 (5단계) ----------------
P_DRAFT, P_REVIEW, P_FINAL = "작성 중", "검토 요청", "변호사 확정"
DEFAULT_ASSETS = ["예금·현금", "보험 해약환급금", "임차보증금", "자동차", "퇴직금(예상액의 1/2)", "기타"]


def page_plan(case):
    st.header("변제계획안")
    cid, user = case["id"], ss.user
    creditors, claims = db.creditors(cid), db.claims(cid)
    if not creditors:
        st.info("먼저 채권자를 입력하세요.")
        return
    fp = plan.claims_fingerprint(creditors, claims)
    if db.plan_list_changed(cid, user["id"], fp):
        st.warning("검토 요청·확정 뒤 채권자목록이 바뀌어 변제계획안을 '작성 중'으로 되돌렸습니다. 다시 확인하세요.")
    p = db.get_plan(cid)
    inp = plan.Inputs.from_json(p["inputs_json"] if p else None)
    cfg = plan.config()
    status = p["status"] if p else P_DRAFT
    st.markdown(f"상태: **{status}**" + (f" — {p['approved_by_name']} 변호사 확정 ({p['approved_at']})"
                                       if p and p["approved_at"] else ""))
    if case["status"] != STATUS_APPROVED:
        st.info("채권자목록이 아직 '변호사 확인 완료' 전입니다. 계산은 할 수 있지만, 변제계획안 확정은 채권자목록 확인 뒤에 됩니다.")

    years = sorted(cfg["median_income"])
    with st.form(f"plan_{cid}"):
        st.subheader("1. 소득과 생계비")
        c1, c2, c3 = st.columns(3)
        income = c1.text_input("월 평균 소득 (세금·4대보험 뺀 실수령액)", amt_str(inp.monthly_income))
        income_note = c2.text_input("소득 근거", inp.income_note, placeholder="예: 최근 1년 급여 평균")
        household = c3.number_input("생계비 가구원 수 (채무자 포함)", min_value=1, max_value=15, value=int(inp.household))
        year = c1.selectbox("기준중위소득 연도", years, index=years.index(inp.median_year) if inp.median_year in years
                            else len(years) - 1)
        auto_living = plan.living_cost(year, int(household), cfg)
        lo = c2.text_input("생계비 직접 입력 (비우면 자동 계산)", amt_str(inp.living_override),
                           help=f"자동: {year}년 {household}인 기준중위소득 × {cfg['living_ratio']:.0%} = "
                                f"{amt_str(auto_living)}원 (저장 뒤 반영)")
        extra = c3.text_input("추가 생계비 (주거비·의료비·교육비 등)", amt_str(inp.extra_living or None))
        extra_note = st.text_input("추가 생계비 사유", inp.extra_living_note,
                                   placeholder="법원이 인정할지 변호사 판단이 필요한 항목입니다")

        st.subheader("2. 변제 조건")
        c1, c2, c3, c4 = st.columns(4)
        months = c1.number_input("변제기간 (개월)", min_value=1, max_value=int(cfg["max_months"]), value=int(inp.months))
        start = c2.text_input("첫 변제일 (예: 2027-01-25)", inp.start_date or "")
        pay_o = c3.text_input("월 변제금 직접 정하기 (비우면 가용소득)", amt_str(inp.payment_override))
        basis = c4.radio("채권액 기준", [plan.BASIS_TOTAL, plan.BASIS_PRINCIPAL],
                         index=0 if inp.basis == plan.BASIS_TOTAL else 1)

        st.subheader("3. 채권 구분")
        st.caption("조세·공과금처럼 우선권 있는 채권은 '우선', 담보로 회수하는 채권은 '제외'. 우선·제외 여부는 변호사가 판단합니다.")
        by_cr: dict[int, list] = {}
        for cl in claims:
            by_cr.setdefault(cl["creditor_id"], []).append(cl)
        tdf = pd.DataFrame([{
            "id": c["id"], "채권자": c["name"],
            "채권액": listing.fmt_amount(sum(t for t in (rules.claim_total(x) for x in by_cr.get(c["id"], []))
                                          if t != rules.UNKNOWN) or None),
            "구분": inp.creditor_types.get(str(c["id"]),
                                         plan.TYPE_PRIORITY if any(x["category"] == "tax" for x in by_cr.get(c["id"], []))
                                         else plan.TYPE_GENERAL)} for c in creditors])
        types = st.data_editor(tdf, hide_index=True, width="stretch", disabled=["id", "채권자", "채권액"],
                               column_config={"id": None, "구분": st.column_config.SelectboxColumn(
                                   options=plan.CREDITOR_TYPES, required=True)}, key=f"ptypes_{cid}")

        st.subheader("4. 재산 (청산가치)")
        st.caption("청산가치 반영액 = 평가액 − 공제·면제액 (압류금지 예금, 소액임차보증금 최우선변제액 등). "
                   "퇴직금은 예상액의 1/2 등 법원 실무에 맞게 넣으세요.")
        assets = inp.assets or [{"name": n, "value": 0, "deduct": 0, "note": ""} for n in DEFAULT_ASSETS]
        adf = pd.DataFrame([{"재산": a.get("name", ""), "평가액": a.get("value") or 0, "공제·면제액": a.get("deduct") or 0,
                             "메모": a.get("note", "")} for a in assets])
        aed = st.data_editor(adf, hide_index=True, width="stretch", num_rows="dynamic", key=f"passets_{cid}",
                             column_config={"평가액": st.column_config.NumberColumn(format="localized", step=1, min_value=0),
                                            "공제·면제액": st.column_config.NumberColumn(format="localized", step=1, min_value=0)})
        if st.form_submit_button("저장하고 계산", type="primary"):
            try:
                new = plan.Inputs(
                    monthly_income=parse_amount(income), income_note=income_note.strip(), household=int(household),
                    median_year=year, living_override=parse_amount(lo), extra_living=parse_amount(extra) or 0,
                    extra_living_note=extra_note.strip(), months=int(months), start_date=parse_date(start),
                    payment_override=parse_amount(pay_o), basis=basis,
                    creditor_types={str(int(r["id"])): r["구분"] for _, r in types.iterrows()},
                    assets=[{"name": str(r["재산"] or "").strip(), "value": int(r["평가액"] or 0),
                             "deduct": int(r["공제·면제액"] or 0), "note": str(r["메모"] or "")}
                            for _, r in aed.iterrows() if str(r["재산"] or "").strip()])
                db.save_plan_inputs(cid, user["id"], new.to_json())
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    if not p:
        st.caption("값을 넣고 '저장하고 계산'을 누르면 결과가 나옵니다.")
        return

    r = plan.compute(inp, creditors, claims, cfg)
    st.divider()
    st.subheader("계산 결과")
    for e in r.errors:
        st.error(e)
    if r.errors:
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("월 변제금", f"{r.payment:,}원", help=f"월 소득 {inp.monthly_income:,} − 생계비 {r.living:,} = 가용소득 {r.available:,}")
    c2.metric("총 변제예정액", f"{r.total_pay:,}원", help=f"{r.months}개월")
    c3.metric("일반채권 변제율", f"{r.repay_rate * 100:.2f}%")
    c4.metric("무담보 채권 총액", f"{r.debt_total:,}원")
    checks = [
        (r.pv_ok, f"청산가치 보장: 총 변제예정액의 현재가치 {r.pv:,}원 {'≥' if r.pv_ok else '<'} 청산가치 {r.liquidation:,}원"),
        (r.min_ok, f"최저변제액: 총 변제예정액 {r.total_pay:,}원 {'≥' if r.min_ok else '<'} "
                   f"{min(r.min_required, r.debt_total):,}원"),
    ]
    for ok_, msg in checks:
        (st.success if ok_ else st.error)(("✅ " if ok_ else "❌ ") + msg)
    if r.suggestion:
        st.info("제안: " + r.suggestion)
    for w in r.warnings:
        st.warning(w)
    if inp.living_override is None:
        st.caption(f"⚠️ 생계비는 config/plan.json의 {inp.median_year}년 기준중위소득으로 계산했습니다. "
                   "보건복지부 고시와 같은지 확인하세요.")

    names = {x.creditor_id: x.name for x in r.rows}
    st.dataframe(pd.DataFrame([{"번호": x.no, "채권자": x.name, "구분": x.kind, "채권액": f"{x.claim:,}",
                                "비율": f"{x.share * 100:.2f}%" if x.kind != plan.TYPE_EXCLUDED else "-",
                                "평상시 월 변제액": f"{x.regular_month:,}", "총 변제예정액": f"{x.total_pay:,}",
                                "변제율": f"{x.total_pay / x.claim * 100:.2f}%" if x.claim else "-"} for x in r.rows]),
                 hide_index=True, width="stretch")
    with st.expander(f"월별 변제표 ({r.months}회)"):
        st.dataframe(pd.DataFrame([{"회차": m["회차"], "날짜": m["날짜"],
                                    **{names[i]: f"{m.get(i, 0):,}" for i in names if any(x.creditor_id == i and
                                       x.kind != plan.TYPE_EXCLUDED for x in r.rows)},
                                    "합계": f"{m['합계']:,}"} for m in r.schedule]), hide_index=True, width="stretch")

    st.divider()
    st.subheader("검토 · 확정")
    blockers = []
    if case["status"] != STATUS_APPROVED:
        blockers.append("채권자목록이 '변호사 확인 완료' 상태가 아닙니다.")
    if not r.pv_ok:
        blockers.append("청산가치 보장 기준을 넘지 못합니다.")
    if not r.min_ok:
        blockers.append("최저변제액 기준을 넘지 못합니다.")
    for b in blockers:
        st.markdown(f"- {b}")
    b1, b2, b3 = st.columns(3)
    try:
        if status == P_DRAFT and b1.button("검토 요청", type="primary", key="plan_req"):
            db.set_plan_status(cid, user, P_REVIEW, fp)
            st.rerun()
        if status == P_REVIEW:
            if user["role"] == ROLE_LAWYER:
                ok = st.checkbox("소득·생계비·채권 구분·청산가치를 직접 확인했고, 이 변제계획안으로 확정합니다.", key="plan_ok")
                if b1.button("변호사 확정", type="primary", disabled=bool(blockers) or not ok, key="plan_final"):
                    db.set_plan_status(cid, user, P_FINAL, fp, blockers)
                    st.rerun()
            else:
                b1.info("변호사 확정을 기다리는 중입니다.")
        if status != P_DRAFT and b2.button("작성 중으로 되돌리기", key="plan_back"):
            db.set_plan_status(cid, user, P_DRAFT, fp)
            st.rerun()
    except PermissionError as e:
        st.error(str(e))
    st.caption("확정 뒤 입력값이나 채권자목록이 바뀌면 자동으로 '작성 중'으로 돌아갑니다.")

    st.divider()
    st.subheader("파일 출력")
    draft = status != P_FINAL
    if draft:
        st.info("변호사 확정 전이라 **초안**으로 만들어집니다.")
    c1, c2, c3 = st.columns(3)
    xp = plan_export.plan_xlsx(case, inp, r, draft)
    c1.download_button("계산 근거표 엑셀 받기", xp.read_bytes(), file_name=xp.name,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    h = plan_export.plan_html(case, inp, r, draft)
    base = f"{case.get('case_no') or '사건'}_{case['debtor_name']}_변제계획안{'_초안' if draft else ''}"
    c2.download_button("변제계획안 HTML 받기", h.encode("utf-8"), file_name=f"{base}.html", mime="text/html")
    hok, why = export.hwp_available()
    if c3.button("변제계획안 HWP 만들기", disabled=not hok, key="plan_hwp"):
        try:
            out = export.html_to_hwp(h, export.OUTPUT_DIR / f"{base}.hwp")
            db.log(cid, user["id"], "변제계획안 HWP 출력", {"파일": out.name, "초안": draft})
            db.conn.commit()
            ss[f"planhwp_{cid}"] = str(out)
        except Exception as e:
            st.error(f"HWP 출력 실패: {e}")
    if not hok:
        c3.caption(why)
    hp = ss.get(f"planhwp_{cid}")
    if hp and Path(hp).exists():
        st.download_button("방금 만든 HWP 받기", Path(hp).read_bytes(), file_name=Path(hp).name)
    with st.expander("변제계획안 미리보기"):
        import streamlit.components.v1 as components
        components.html(f"<div style='background:#fff;color:#000;padding:16px'>{h}</div>", height=700, scrolling=True)


# ---------------- 화면: 보정명령 (4단계) ----------------
def _upload_text(f) -> str:
    pages = ocr.read_file(f.getvalue(), f.name)
    return "\n".join(l.text for p in pages for l in p.lines)


def page_corrections(case):
    st.header("보정명령")
    cid = case["id"]
    orders = db.corrections(cid)
    flash = ss.pop(f"corrflash_{cid}", None)
    if flash:
        st.success(flash)
    if orders:
        st.dataframe(pd.DataFrame([{
            "차수": f"{o['seq']}차", "명령일": o["order_date"] or "", "송달일": o["served_date"] or "",
            "기한": o["deadline"] or "미정",
            "남은 날": correction.d_day_label(correction.d_day(o["deadline"])) if o["status"] != correction.C_SUBMITTED else "-",
            "상태": o["status"], "항목": len(db.correction_items(o["id"]))} for o in orders]),
            hide_index=True, width="stretch")
    key = f"corrsel_{cid}"
    opts = {f"{o['seq']}차 보정명령 ({o['status']})": o["id"] for o in orders}
    labels = list(opts) + ["➕ 새 보정명령 등록"]
    cur = ss.get(key)
    idx = list(opts.values()).index(cur) if cur in opts.values() else (0 if orders else len(labels) - 1)
    pick = st.selectbox("보정명령 선택", labels, index=idx)
    if pick == labels[-1]:
        ss[key] = None
        _new_correction(case)
    else:
        ss[key] = opts[pick]
        _correction_detail(case, db.get_correction(opts[pick]))


def _new_correction(case):
    cid = case["id"]
    pkey = f"corrnew_{cid}"
    pv = ss.get(pkey)
    if pv is None:
        st.caption("법원 보정명령문을 붙여 넣거나 파일(전자소송 PDF, 스캔본, 사진)을 올리세요. 번호(1., 가., ①)를 기준으로 "
                   "항목을 나누고, 분류와 필요한 서류를 추천합니다.")
        with st.form(f"corr_in_{cid}"):
            f = st.file_uploader("보정명령 파일 (선택)", type=["pdf", "jpg", "jpeg", "png", "tif", "tiff"])
            text = st.text_area("또는 명령문 붙여 넣기", height=220)
            served = st.text_input("송달일 (받은 날, 예: 2026-11-20)", date.today().isoformat())
            if st.form_submit_button("명령문 읽기", type="primary"):
                try:
                    raw = text.strip()
                    if f is not None:
                        with st.spinner("파일을 읽는 중..."):
                            raw = (_upload_text(f) + "\n" + raw).strip()
                    if not raw:
                        raise ValueError("명령문을 붙여 넣거나 파일을 올리세요.")
                    served_iso = parse_date(served)
                    p = correction.parse_order(raw)
                    ss[pkey] = {"raw": raw, "name": f.name if f else None, "parsed": p, "served": served_iso}
                    st.rerun()
                except (ValueError, ocr.OcrUnavailable) as e:
                    st.error(str(e))
        return

    p = pv["parsed"]
    st.markdown("#### 읽은 내용 확인")
    if p.case_no and case["case_no"] and re.sub(r"\s", "", case["case_no"]) != p.case_no:
        st.error(f"명령문의 사건번호({p.case_no})가 이 사건({case['case_no']})과 다릅니다. 다른 사건의 명령이 아닌지 확인하세요.")
    elif p.case_no and not case["case_no"]:
        st.info(f"명령문의 사건번호는 {p.case_no}입니다. 등록하면 이 사건의 사건번호로 저장합니다.")
    c1, c2, c3, c4 = st.columns(4)
    od = c1.text_input("명령일", p.order_date or "", key=f"cn_od_{cid}")
    sv = c2.text_input("송달일", pv["served"] or "", key=f"cn_sv_{cid}")
    days = c3.number_input("보정 기간(일)", min_value=0, value=p.days or 14, step=1, key=f"cn_days_{cid}")
    try:
        auto = correction.compute_deadline(parse_date(sv), int(days)) if parse_date(sv) and days else ""
    except ValueError:
        auto = ""
    dl = c4.text_input("기한 (자동 계산, 고칠 수 있음)", auto, key=f"cn_dl_{cid}_{auto}")
    st.caption("기한 = 송달일 다음 날부터 센 날. 마지막 날이 토·일·공휴일(config/holidays.json)이면 다음 평일. 참고용이니 확인하세요.")
    cats = correction.categories()
    df = pd.DataFrame([{"번호": i.no, "큰 제목": i.heading, "명령 내용": i.text, "분류": i.category,
                        "필요 서류 (; 로 구분)": "; ".join(i.docs), "답변 초안": i.answer} for i in p.items])
    st.markdown(f"**항목 {len(p.items)}개** — 잘못 나뉜 곳은 고치고, 행을 더하거나 지울 수 있습니다.")
    ed = st.data_editor(df, hide_index=True, width="stretch", num_rows="dynamic", key=f"cn_ed_{cid}",
                        column_config={"분류": st.column_config.SelectboxColumn(options=cats, required=True),
                                       "명령 내용": st.column_config.TextColumn(width="large")})
    with st.expander("명령문 원문"):
        st.text(pv["raw"])
    b1, b2 = st.columns([1, 5])
    if b1.button("등록", type="primary"):
        try:
            items = []
            for _, r in ed.iterrows():
                text = str(r["명령 내용"] or "").strip()
                if not text:
                    continue
                docs = [d.strip() for d in str(r["필요 서류 (; 로 구분)"] or "").split(";") if d.strip()]
                items.append({"no": str(r["번호"] or len(items) + 1).strip(), "heading": str(r["큰 제목"] or "").strip(),
                              "text": text, "category": r["분류"] or "기타", "answer": str(r["답변 초안"] or ""),
                              "docs": docs})
            if not items:
                raise ValueError("항목이 없습니다.")
            oid = db.create_correction(cid, ss.user["id"], order_date=parse_date(od), served_date=parse_date(sv),
                                       days=int(days) or None, deadline=parse_date(dl), source_name=pv["name"],
                                       raw_text=pv["raw"], items=items)
            if p.case_no and not case["case_no"]:
                db.update_case(cid, ss.user["id"], case_no=p.case_no)
            del ss[pkey]
            ss[f"corrsel_{cid}"] = oid
            ss[f"corrflash_{cid}"] = f"보정명령을 등록했습니다 (항목 {len(items)}개)."
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    if b2.button("취소"):
        del ss[pkey]
        st.rerun()


def _correction_detail(case, o):
    cid = case["id"]
    oid = o["id"]
    items = db.correction_items(oid)
    docs = db.correction_docs(oid)
    n = correction.d_day(o["deadline"])
    head = f"**{o['seq']}차 보정명령** · 상태 {o['status']}"
    if o["status"] != correction.C_SUBMITTED:
        color = "red" if n is not None and n <= 3 else ("orange" if n is not None and n <= 7 else "gray")
        head += f" · 기한 {o['deadline'] or '미정'} :{color}[**{correction.d_day_label(n)}**]"
    else:
        head += f" · 제출 {o['submitted_at']}"
    st.markdown(head)
    if o["approved_at"]:
        st.caption(f"변호사 승인: {o['approved_by_name']} ({o['approved_at']})")
    t1, t2, t3, t4 = st.tabs(["항목·답변", "자료 요청", "보정서", "원문·정보"])
    with t1:
        _tab_items(case, o, items, docs)
    with t2:
        _tab_docs(case, o, items, docs)
    with t3:
        _tab_reply(case, o, items, docs)
    with t4:
        _tab_info(case, o)


def _tab_items(case, o, items, docs):
    cats = correction.categories()
    _, _, open_ = warnings_for(case["id"])
    for it in items:
        ds = docs.get(it["id"], [])
        state = correction.item_state(it["answer"], ds)
        icon = "✅" if state == "답변 작성됨" else ("📎" if "서류" in state or state == "자료 대기" else "✏️")
        with st.expander(f"{icon} {it['no']} [{it['category'] or '기타'}] {state} — {it['text'][:60]}"):
            if it["heading"]:
                st.caption(it["heading"])
            st.markdown(f"> {it['text']}")
            if it["category"] == "채권자목록":
                st.info(f"참고: 이 사건 채권자목록의 처리 안 된 누락 점검 경고 {len(open_)}건. "
                        "채권자목록을 고친 뒤 '미리보기·출력'에서 수정본을 출력해 첨부하세요.")
            if ds:
                st.markdown("서류: " + ", ".join(f"{d['name']} ({d['status']})" for d in ds))
            with st.form(f"it_{it['id']}"):
                ans = st.text_area("답변", it["answer"] or "", height=120)
                c1, c2 = st.columns(2)
                cat = c1.selectbox("분류", cats, index=cats.index(it["category"]) if it["category"] in cats else len(cats) - 1)
                txt = st.text_area("명령 내용 (잘못 읽었으면 고치기)", it["text"], height=80)
                if st.form_submit_button("저장", type="primary"):
                    db.update_item(it["id"], ss.user["id"], answer=ans, category=cat, text=txt.strip() or it["text"])
                    st.rerun()
            if st.checkbox("이 항목 삭제", key=f"itdelok_{it['id']}"):
                if st.button("삭제", key=f"itdel_{it['id']}"):
                    db.delete_item(it["id"], ss.user["id"])
                    st.rerun()
    with st.expander("➕ 항목 추가"):
        with st.form(f"additem_{o['id']}", clear_on_submit=True):
            c1, c2 = st.columns([1, 3])
            no = c1.text_input("번호")
            txt = c2.text_area("명령 내용")
            if st.form_submit_button("추가") and txt.strip():
                cat, _ = correction.classify(txt)
                iid = db.add_item(o["id"], ss.user["id"], no.strip() or str(len(items) + 1), txt.strip(), cat)
                for d in correction.suggest_docs(txt):
                    db.add_doc(iid, ss.user["id"], d)
                st.rerun()


def _tab_docs(case, o, items, docs):
    by_id = {it["id"]: it for it in items}
    all_docs = [d for it in items for d in docs.get(it["id"], [])]
    if all_docs:
        df = pd.DataFrame([{"id": d["id"], "항목": by_id[d["item_id"]]["no"], "서류": d["name"], "상태": d["status"],
                            "요청일": d["requested_at"] or "", "수령일": d["received_at"] or "", "메모": d["note"] or ""}
                           for d in all_docs])
        ed = st.data_editor(df, hide_index=True, width="stretch", disabled=["id", "항목"],
                            key=f"docs_{o['id']}",
                            column_config={"id": None, "상태": st.column_config.SelectboxColumn(
                                options=correction.DOC_STATES, required=True)})
        recv = sum(1 for d in all_docs if d["status"] in correction.DONE_DOC_STATES)
        st.progress(recv / len(all_docs), text=f"받음·생략 {recv} / {len(all_docs)}")
        c1, c2 = st.columns(2)
        if c1.button("서류 상태 저장", type="primary"):
            try:
                for _, r in ed.iterrows():
                    d = next(x for x in all_docs if x["id"] == r["id"])
                    req, rec = parse_date(r["요청일"]), parse_date(r["수령일"])
                    if r["상태"] == "받음" and not rec:
                        rec = date.today().isoformat()
                    if r["상태"] in ("요청함", "받음") and not req:
                        req = d["requested_at"] or date.today().isoformat()
                    if r["상태"] == "생략" and not str(r["메모"]).strip():
                        raise ValueError(f"'{r['서류']}'을(를) 생략하려면 메모에 사유를 적으세요.")
                    new = dict(name=str(r["서류"]).strip() or d["name"], status=r["상태"], requested_at=req,
                               received_at=rec, note=str(r["메모"]).strip() or None)
                    if any(new[k] != d[k] for k in new):
                        db.update_doc(d["id"], ss.user["id"], **new)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        pick = c2.selectbox("서류 삭제", ["선택 안 함"] + [f"{d['id']}: {d['name']}" for d in all_docs],
                            key=f"docdel_{o['id']}")
        if pick != "선택 안 함" and c2.button("삭제", key=f"docdelbtn_{o['id']}"):
            db.delete_doc(int(pick.split(":")[0]), ss.user["id"])
            st.rerun()
    else:
        st.info("필요한 서류가 없습니다.")
    with st.form(f"adddoc_{o['id']}", clear_on_submit=True):
        c1, c2 = st.columns([1, 3])
        it_no = c1.selectbox("항목", [it["no"] for it in items]) if items else None
        name = c2.text_input("서류 이름")
        if st.form_submit_button("서류 추가") and name.strip() and it_no:
            db.add_doc(next(it["id"] for it in items if it["no"] == it_no), ss.user["id"], name)
            st.rerun()

    st.divider()
    st.markdown("**의뢰인 안내문** (문자·카카오톡으로 보내기)")
    msg = correction.client_message(case["debtor_name"], o["deadline"], all_docs)
    if msg:
        st.code(msg, language=None)
        st.caption("오른쪽 위 복사 버튼으로 복사하세요. 마감일은 법원 기한보다 며칠 앞입니다(config/office.json).")
        if st.button("보냈음: '요청 전' 서류를 '요청함'으로"):
            for d in all_docs:
                if d["status"] == "요청 전":
                    db.update_doc(d["id"], ss.user["id"], status="요청함", requested_at=date.today().isoformat())
            st.rerun()
    else:
        st.caption("요청할 서류가 없습니다.")


def _tab_reply(case, o, items, docs):
    user = ss.user
    blockers = correction.approval_blockers(items, docs)
    st.markdown("**검토 · 승인**")
    if blockers and o["status"] in (correction.C_DRAFT, correction.C_REVIEW):
        with st.container(border=True):
            st.markdown("승인 전에 처리할 것:")
            for b in blockers:
                st.markdown(f"- {b}")
    c1, c2, c3 = st.columns(3)
    try:
        if o["status"] == correction.C_DRAFT and c1.button("검토 요청", type="primary"):
            db.set_correction_status(o["id"], user, correction.C_REVIEW)
            st.rerun()
        if o["status"] == correction.C_REVIEW:
            if user["role"] == ROLE_LAWYER:
                ok = st.checkbox("항목별 답변과 첨부 서류를 직접 확인했고, 이 내용으로 승인합니다.", key=f"capok_{o['id']}")
                if c1.button("변호사 승인", type="primary", disabled=bool(blockers) or not ok):
                    db.set_correction_status(o["id"], user, correction.C_APPROVED, blockers)
                    st.rerun()
            else:
                c1.info("변호사 승인을 기다리는 중입니다.")
        if o["status"] == correction.C_APPROVED and c2.button("제출 완료로 표시"):
            db.set_correction_status(o["id"], user, correction.C_SUBMITTED)
            st.rerun()
        if o["status"] != correction.C_DRAFT and c3.button("작성 중으로 되돌리기"):
            db.set_correction_status(o["id"], user, correction.C_DRAFT)
            st.rerun()
    except PermissionError as e:
        st.error(str(e))
    st.caption("승인 뒤 답변·서류를 고치면 자동으로 '작성 중'으로 돌아갑니다.")

    st.divider()
    draft = o["status"] not in (correction.C_APPROVED, correction.C_SUBMITTED)
    if draft:
        st.info("변호사 승인 전이라 **초안**으로 만들어집니다.")
    h = correction.reply_html(case, o, items, docs, draft)
    import streamlit.components.v1 as components
    components.html(f"<div style='background:#fff;color:#000;padding:16px'>{h}</div>", height=600, scrolling=True)
    tag = "_초안" if draft else ""
    base = f"{case.get('case_no') or '사건'}_{case['debtor_name']}_{o['seq']}차보정서{tag}"
    c1, c2, c3 = st.columns(3)
    c1.download_button("HTML 받기", h.encode("utf-8"), file_name=f"{base}.html", mime="text/html")
    c2.download_button("글(TXT) 받기 — 전자소송 붙여넣기용", correction.reply_text(case, o, items, docs).encode("utf-8"),
                       file_name=f"{base}.txt", mime="text/plain")
    ok, why = export.hwp_available()
    if c3.button("HWP 만들기", disabled=not ok, help=None if ok else why):
        try:
            out = export.html_to_hwp(h, export.OUTPUT_DIR / f"{base}.hwp")
            db.log(case["id"], user["id"], "보정서 HWP 출력", {"파일": out.name, "초안": draft})
            db.conn.commit()
            ss[f"corrhwp_{o['id']}"] = str(out)
        except Exception as e:
            st.error(f"HWP 출력 실패: {e}")
    if not ok:
        c3.caption(why)
    hp = ss.get(f"corrhwp_{o['id']}")
    if hp and Path(hp).exists():
        st.download_button("방금 만든 HWP 받기", Path(hp).read_bytes(), file_name=Path(hp).name)


def _tab_info(case, o):
    with st.form(f"corrinfo_{o['id']}"):
        c1, c2, c3, c4 = st.columns(4)
        od = c1.text_input("명령일", o["order_date"] or "")
        sv = c2.text_input("송달일", o["served_date"] or "")
        days = c3.number_input("보정 기간(일)", min_value=0, value=o["days"] or 0, step=1)
        dl = c4.text_input("기한 (비우면 송달일·기간으로 계산)", o["deadline"] or "")
        memo = st.text_area("메모", o["memo"] or "")
        if st.form_submit_button("저장"):
            try:
                sv_iso = parse_date(sv)
                dl_iso = parse_date(dl) or (correction.compute_deadline(sv_iso, int(days)) if sv_iso and days else None)
                db.update_correction(o["id"], ss.user["id"], order_date=parse_date(od), served_date=sv_iso,
                                     days=int(days) or None, deadline=dl_iso, memo=memo.strip() or None)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    with st.expander("명령문 원문"):
        st.text(o["raw_text"] or "")
        if o["source_name"]:
            st.caption(f"파일: {o['source_name']}")
    with st.expander("이 보정명령 삭제"):
        if st.checkbox("항목·서류 기록이 모두 지워집니다. 삭제를 확인합니다.", key=f"cdel_{o['id']}"):
            if st.button("보정명령 삭제"):
                db.delete_correction(o["id"], ss.user["id"])
                ss[f"corrsel_{case['id']}"] = None
                st.rerun()


# ---------------- 화면: 변경 기록 ----------------
def page_audit(case):
    st.header("변경 기록")
    st.caption("누가 언제 무엇을 바꿨는지, 경고를 누가 확인했는지, 변호사가 언제 확인했는지 남습니다. 감독 기록으로 쓸 수 있습니다.")
    log = db.audit(case["id"])
    if log:
        st.dataframe(pd.DataFrame(log).rename(columns={"at": "일시", "user": "사용자", "role": "역할",
                                                      "action": "작업", "detail": "내용"}),
                     hide_index=True, width="stretch")


# ---------------- 화면: 계정 관리 ----------------
def password_form(key: str, title: str, current_label: str = "지금 비밀번호") -> bool:
    """본인 비밀번호 바꾸기 양식. 바꾸면 True."""
    with st.form(key, clear_on_submit=True):
        st.markdown(f"**{title}**")
        cur = st.text_input(current_label, type="password")
        c1, c2 = st.columns(2)
        new = c1.text_input(f"새 비밀번호 ({DB.MIN_PW}자 이상)", type="password")
        new2 = c2.text_input("새 비밀번호 한 번 더", type="password")
        if st.form_submit_button("비밀번호 바꾸기", type="primary"):
            if new != new2:
                st.error("새 비밀번호 두 칸이 서로 다릅니다.")
                return False
            try:
                db.change_password(ss.user["id"], cur, new)
                return True
            except (PermissionError, ValueError) as e:
                st.error(str(e))
    return False


def forced_password_change():
    st.title("비밀번호 바꾸기")
    st.warning(f"{ss.user['name']}님의 비밀번호가 임시 비밀번호로 초기화되었습니다. 계속하려면 새 비밀번호로 바꾸세요.")
    if password_form("pw_forced", "새 비밀번호 정하기", "받은 임시 비밀번호"):
        ss.user["must_change"] = False
        ss["pwflash"] = "비밀번호를 바꿨습니다."
        st.rerun()
    if st.button("로그아웃"):
        for k in list(ss.keys()):
            del ss[k]
        st.rerun()


def page_users():
    st.header("계정 관리")
    flash = ss.pop("pwflash", None)
    if flash:
        st.success(flash)
    users = db.users()
    st.dataframe(pd.DataFrame([{"이름": u["name"], "역할": u["role"], "사용": "예" if u["active"] else "아니오",
                                "비밀번호": "변경 필요 (임시)" if u["must_change"] else ""} for u in users]),
                 hide_index=True)

    if password_form("pw_mine", f"내 비밀번호 바꾸기 ({ss.user['name']})"):
        ss["pwflash"] = "비밀번호를 바꿨습니다. 다음 로그인부터 새 비밀번호를 쓰세요."
        st.rerun()

    if ss.user["role"] not in (ROLE_LAWYER, ROLE_ADMIN):
        st.info("계정 추가와 다른 사람의 비밀번호 초기화는 변호사·관리자 계정에서 할 수 있습니다.")
        return

    st.divider()
    others = [u for u in users if u["id"] != ss.user["id"]]
    with st.form("pw_reset", clear_on_submit=True):
        st.markdown("**다른 사람 비밀번호 초기화** — 비밀번호를 잊은 사람에게 임시 비밀번호를 정해 주면, "
                    "그 사람은 다음 로그인 때 새 비밀번호로 바꿔야 합니다.")
        c1, c2 = st.columns(2)
        target = c1.selectbox("사용자", [f"{u['name']} ({u['role']})" for u in others]) if others else None
        temp = c2.text_input(f"임시 비밀번호 ({DB.MIN_PW}자 이상)", type="password")
        if st.form_submit_button("초기화") and target:
            t = others[[f"{u['name']} ({u['role']})" for u in others].index(target)]
            try:
                db.reset_password(ss.user, t["id"], temp)
                ss["pwflash"] = f"{t['name']}님의 비밀번호를 임시 비밀번호로 초기화했습니다. 임시 비밀번호를 직접 알려 주세요."
                st.rerun()
            except (PermissionError, ValueError) as e:
                st.error(str(e))

    st.divider()
    with st.form("add_user", clear_on_submit=True):
        st.markdown("**계정 추가**")
        c1, c2, c3 = st.columns(3)
        n = c1.text_input("이름")
        r = c2.selectbox("역할", ROLES)
        p = c3.text_input("비밀번호", type="password")
        if st.form_submit_button("계정 추가"):
            try:
                if not n.strip() or len(p) < DB.MIN_PW:
                    raise ValueError(f"이름과 {DB.MIN_PW}자 이상 비밀번호를 입력하세요.")
                db.add_user(n, r, p)
                st.success("추가했습니다.")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    log = db.password_log()
    if log:
        with st.expander("비밀번호 변경 기록"):
            st.dataframe(pd.DataFrame(log).rename(columns={"at": "일시", "user": "한 사람", "action": "작업",
                                                          "target": "대상"}), hide_index=True, width="stretch")


# ---------------- 메인 ----------------
CASE_PAGES = ["사건 기본정보", "채무 문진", "채권자 입력", "신용정보 대조", "발급대행 요청 목록", "부채증명서 대조",
              "누락 점검", "미리보기·출력", "변제계획안", "보정명령", "변경 기록"]
PAGE_FUNCS = {"사건 기본정보": page_basic, "채무 문진": page_questionnaire, "채권자 입력": page_creditors,
              "신용정보 대조": page_credit, "누락 점검": page_check, "발급대행 요청 목록": page_agency,
              "부채증명서 대조": page_debt_cert, "미리보기·출력": page_output, "변제계획안": page_plan, "보정명령": page_corrections,
              "변경 기록": page_audit}


def main():
    if db.user_count() == 0:
        first_run()
        return
    if "user" not in ss:
        login_screen()
        return
    if ss.user.get("must_change"):
        forced_password_change()
        return
    if ss.get("page") != "계정 관리" and ss.get("pwflash"):
        st.success(ss.pop("pwflash"))
    ss.setdefault("page", "사건 목록")
    ss.setdefault("case_id", None)
    user = ss.user
    with st.sidebar:
        st.markdown(f"**{user['name']}** ({user['role']})")
        if st.button("로그아웃"):
            for k in list(ss.keys()):
                del ss[k]
            st.rerun()
        st.divider()
        if st.button("📁 사건 목록", width="stretch"):
            ss.page = "사건 목록"
            st.rerun()
        case = db.get_case(ss.case_id) if ss.case_id else None
        if case:
            _, _, open_ = warnings_for(case["id"])
            st.markdown(f"### {case['debtor_name']}")
            st.caption(f"{case['case_no'] or '사건번호 없음'} · {case['court'] or ''}")
            st.markdown(status_badge(case["status"]))
            for p in CASE_PAGES:
                label = p + (f"  ⚠️{len(open_)}" if p == "누락 점검" and open_ else "")
                if p == "보정명령":
                    ds = [correction.d_day(o["deadline"]) for o in db.corrections(case["id"])
                          if o["status"] != correction.C_SUBMITTED]
                    ds = [d for d in ds if d is not None]
                    if ds:
                        label += f"  ⏰{correction.d_day_label(min(ds))}"
                if st.button(label, width="stretch", type="primary" if ss.page == p else "secondary",
                             key=f"nav_{p}"):
                    ss.page = p
                    st.rerun()
        st.divider()
        if st.button("👤 계정 관리", width="stretch"):
            ss.page = "계정 관리"
            st.rerun()
        st.caption("자료는 이 PC의 data/cases.db에만 저장됩니다. 백업은 이 파일을 복사하세요.")

    if ss.page == "계정 관리":
        page_users()
    elif ss.page in PAGE_FUNCS and case:
        PAGE_FUNCS[ss.page](case)
    else:
        page_cases()


main()
