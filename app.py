"""개인회생 채권자목록 정리·누락 점검 프로그램 (1단계)

실행: 실행.bat 더블클릭 (또는 `streamlit run app.py`)
모든 자료는 이 PC의 data/cases.db 파일에만 저장되며 인터넷으로 나가지 않습니다.
"""
from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd
import streamlit as st

from core import credit_pdf, debt_cert, export, listing, ocr, rules
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
def page_cases():
    st.header("사건 목록")
    c1, c2 = st.columns([3, 1])
    kw = c1.text_input("검색 (채무자 이름, 사건번호)")
    stf = c2.selectbox("진행 상태", ["전체"] + STATUSES)
    cases = db.list_cases(kw, None if stf == "전체" else stf)
    if cases:
        df = pd.DataFrame(cases).rename(columns={
            "id": "ID", "case_no": "사건번호", "debtor_name": "채무자", "court": "법원", "status": "상태",
            "updated_at": "최근 수정", "lawyer_name": "담당 변호사", "creditor_count": "채권자 수"})
        st.dataframe(df[["ID", "사건번호", "채무자", "법원", "담당 변호사", "채권자 수", "상태", "최근 수정"]],
                     hide_index=True, use_container_width=True)
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
                    st.dataframe(df, hide_index=True, use_container_width=True)
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
                df, hide_index=True, use_container_width=True, key=f"ed_{case['id']}",
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
            st.image(credit_pdf.render_page(stt["data"], pg, boxes, stt["pw"]), use_container_width=True)
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
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
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
        df, hide_index=True, use_container_width=True, disabled=["id", "기관명"],
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
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
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
            df, hide_index=True, use_container_width=True, num_rows="dynamic", key=f"cert_ed_{cid}_{item['name']}_{cr_id}",
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
                                   for r in cmp]), hide_index=True, use_container_width=True)
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
        st.image(ocr.draw_boxes(pages[pn - 1], marks), use_container_width=True)
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
    st.dataframe(pd.DataFrame(listing.display_rows(rows)), hide_index=True, use_container_width=True)
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


# ---------------- 화면: 변경 기록 ----------------
def page_audit(case):
    st.header("변경 기록")
    st.caption("누가 언제 무엇을 바꿨는지, 경고를 누가 확인했는지, 변호사가 언제 확인했는지 남습니다. 감독 기록으로 쓸 수 있습니다.")
    log = db.audit(case["id"])
    if log:
        st.dataframe(pd.DataFrame(log).rename(columns={"at": "일시", "user": "사용자", "role": "역할",
                                                      "action": "작업", "detail": "내용"}),
                     hide_index=True, use_container_width=True)


# ---------------- 화면: 계정 관리 ----------------
def page_users():
    st.header("계정 관리")
    st.dataframe(pd.DataFrame(db.users()).rename(columns={"name": "이름", "role": "역할", "active": "사용"}),
                 hide_index=True)
    if ss.user["role"] not in (ROLE_LAWYER, ROLE_ADMIN):
        st.info("계정 추가는 변호사·관리자 계정에서 할 수 있습니다.")
        return
    with st.form("add_user", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        n = c1.text_input("이름")
        r = c2.selectbox("역할", ROLES)
        p = c3.text_input("비밀번호", type="password")
        if st.form_submit_button("계정 추가"):
            try:
                if not n.strip() or len(p) < 4:
                    raise ValueError("이름과 4자 이상 비밀번호를 입력하세요.")
                db.add_user(n, r, p)
                st.success("추가했습니다.")
                st.rerun()
            except Exception as e:
                st.error(str(e))


# ---------------- 메인 ----------------
CASE_PAGES = ["사건 기본정보", "채무 문진", "채권자 입력", "신용정보 대조", "발급대행 요청 목록", "부채증명서 대조",
              "누락 점검", "미리보기·출력", "변경 기록"]
PAGE_FUNCS = {"사건 기본정보": page_basic, "채무 문진": page_questionnaire, "채권자 입력": page_creditors,
              "신용정보 대조": page_credit, "누락 점검": page_check, "발급대행 요청 목록": page_agency,
              "부채증명서 대조": page_debt_cert, "미리보기·출력": page_output, "변경 기록": page_audit}


def main():
    if db.user_count() == 0:
        first_run()
        return
    if "user" not in ss:
        login_screen()
        return
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
        if st.button("📁 사건 목록", use_container_width=True):
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
                if st.button(label, use_container_width=True, type="primary" if ss.page == p else "secondary",
                             key=f"nav_{p}"):
                    ss.page = p
                    st.rerun()
        st.divider()
        if st.button("👤 계정 관리", use_container_width=True):
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
