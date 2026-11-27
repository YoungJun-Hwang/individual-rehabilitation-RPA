"""변제계획안 출력: 계산 근거표(엑셀)와 변제계획안 문서(HTML → HWP)."""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .export import BORDER, OUTPUT_DIR, _safe
from .plan import TYPE_EXCLUDED, TYPE_GENERAL, TYPE_PRIORITY, Inputs, PlanResult


def _n(v) -> str:
    return "" if v is None else f"{int(v):,}"


def summary_rows(inp: Inputs, r: PlanResult) -> list[tuple[str, str]]:
    rows = [
        ("월 평균 소득", f"{_n(inp.monthly_income)}원" + (f" ({inp.income_note})" if inp.income_note else "")),
        ("생계비", f"{_n(r.living_base)}원" + (" (직접 입력)" if inp.living_override is not None else
                                            f" (기준중위소득 {inp.median_year}년 {inp.household}인 가구 × 60%)")),
    ]
    if inp.extra_living:
        rows.append(("추가 생계비", f"{_n(inp.extra_living)}원" + (f" ({inp.extra_living_note})" if inp.extra_living_note else "")))
    rows += [
        ("월 가용소득", f"{_n(r.available)}원"),
        ("월 변제금", f"{_n(r.payment)}원" + (" (직접 정함)" if inp.payment_override else "")),
        ("변제기간", f"{r.months}개월" + (f" (첫 변제일 {inp.start_date})" if inp.start_date else "")),
        ("총 변제예정액", f"{_n(r.total_pay)}원"),
        ("무담보 채권 총액", f"{_n(r.debt_total)}원 (일반 {_n(r.general_total)}원, 우선 {_n(r.priority_total)}원)"),
        ("일반채권 변제율", f"{r.repay_rate * 100:.2f}%"),
        ("청산가치", f"{_n(r.liquidation)}원"),
        ("총 변제예정액의 현재가치", f"{_n(r.pv)}원 → {'청산가치 이상 ✔' if r.pv_ok else '청산가치 미달 ✘'}"),
        ("최저변제액", f"{_n(r.min_required)}원 → {'충족 ✔' if r.min_ok else '미달 ✘'}"),
    ]
    if r.excluded_total:
        rows.append(("변제계획에서 제외한 채권", f"{_n(r.excluded_total)}원 (별제권 등)"))
    return rows


def plan_xlsx(case: dict, inp: Inputs, r: PlanResult, draft: bool, path: Path | None = None) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    tag = "_초안" if draft else ""
    path = path or OUTPUT_DIR / f"{_safe(case.get('case_no') or '')}_{_safe(case['debtor_name'])}_변제계획_계산근거{tag}_{date.today():%Y%m%d}.xlsx"
    wb = Workbook()
    hdr_fill = PatternFill("solid", fgColor="E7E6E6")

    def header(ws, cols):
        ws.append(cols)
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
            c.fill = hdr_fill
            c.border = BORDER
            c.alignment = Alignment(horizontal="center", wrap_text=True)

    ws = wb.active
    ws.title = "요약"
    ws.append(["변제계획 계산 근거" + (" [초안]" if draft else "")])
    ws["A1"].font = Font(size=14, bold=True, color="C00000" if draft else "000000")
    ws.append([f"채무자: {case['debtor_name']}   사건번호: {case.get('case_no') or ''}   작성일: {date.today():%Y-%m-%d}"])
    ws.append([])
    for k, v in summary_rows(inp, r):
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
    for w in r.warnings:
        ws.append(["주의", w])
    if r.suggestion:
        ws.append(["제안", r.suggestion])
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 90

    ws = wb.create_sheet("채권자별")
    header(ws, ["번호", "채권자", "구분", "채권액", "비율(같은 구분 안)", "평상시 월 변제액", "총 변제예정액", "변제율"])
    for x in r.rows:
        ws.append([x.no, x.name, x.kind, x.claim, x.share if x.kind != TYPE_EXCLUDED else None,
                   x.regular_month, x.total_pay, x.total_pay / x.claim if x.claim else None])
        for i, c in enumerate(ws[ws.max_row], start=1):
            c.border = BORDER
            if i in (4, 6, 7):
                c.number_format = "#,##0"
            if i in (5, 8):
                c.number_format = "0.00%"
    ws.append(["", "합계", "", sum(x.claim for x in r.rows if x.kind != TYPE_EXCLUDED), None, r.payment, r.total_pay])
    for i, c in enumerate(ws[ws.max_row], start=1):
        c.font = Font(bold=True)
        c.border = BORDER
        if i in (4, 6, 7):
            c.number_format = "#,##0"
    for i, w in enumerate([6, 24, 14, 16, 14, 16, 16, 10], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws = wb.create_sheet("월별 변제표")
    pay_rows = [x for x in r.rows if x.kind != TYPE_EXCLUDED]
    header(ws, ["회차", "날짜"] + [x.name for x in pay_rows] + ["합계"])
    for m in r.schedule:
        ws.append([m["회차"], m["날짜"]] + [m.get(x.creditor_id, 0) for x in pay_rows] + [m["합계"]])
        for i, c in enumerate(ws[ws.max_row], start=1):
            c.border = BORDER
            if i > 2:
                c.number_format = "#,##0"
    ws.append(["합계", ""] + [x.total_pay for x in pay_rows] + [r.total_pay])
    for i, c in enumerate(ws[ws.max_row], start=1):
        c.font = Font(bold=True)
        c.border = BORDER
        if i > 2:
            c.number_format = "#,##0"
    ws.freeze_panes = "C2"
    for i in range(1, len(pay_rows) + 4):
        ws.column_dimensions[get_column_letter(i)].width = 14

    ws = wb.create_sheet("청산가치")
    header(ws, ["재산", "평가액", "공제·면제액", "청산가치 반영액", "메모"])
    for a in inp.assets:
        v, d = int(a.get("value") or 0), int(a.get("deduct") or 0)
        ws.append([a.get("name", ""), v, d, max(0, v - d), a.get("note", "")])
        for i, c in enumerate(ws[ws.max_row], start=1):
            c.border = BORDER
            if 2 <= i <= 4:
                c.number_format = "#,##0"
    ws.append(["합계", "", "", r.liquidation])
    ws.cell(ws.max_row, 4).number_format = "#,##0"
    for i, w in enumerate([24, 16, 16, 16, 40], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    wb.save(path)
    return path


def plan_html(case: dict, inp: Inputs, r: PlanResult, draft: bool) -> str:
    e = html.escape
    title = "변 제 계 획 안" + (" [초안]" if draft else "")
    summ = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in summary_rows(inp, r))
    rows = []
    for x in r.rows:
        if x.kind == TYPE_EXCLUDED:
            continue
        rows.append(f"<tr><td class='c'>{x.no}</td><td>{e(x.name)}</td><td class='c'>{e(x.kind)}</td>"
                    f"<td class='n'>{_n(x.claim)}</td><td class='n'>{x.share * 100:.2f}%</td>"
                    f"<td class='n'>{_n(x.regular_month)}</td><td class='n'>{_n(x.total_pay)}</td></tr>")
    pri = [x for x in r.rows if x.kind == TYPE_PRIORITY]
    excl = [x for x in r.rows if x.kind == TYPE_EXCLUDED]
    notes = []
    if pri:
        notes.append(f"우선권 있는 채권({', '.join(x.name for x in pri)})은 변제 시작 후 {r.priority_months}회차까지 "
                     "먼저 전액 변제하고, 남는 금액은 그 회차부터 일반 채권자에게 채권액 비율로 변제합니다.")
    if excl:
        notes.append(f"별제권 등으로 변제계획에서 제외한 채권: {', '.join(x.name for x in excl)} "
                     "(별제권 행사로 변제받지 못하는 부분은 확정되면 일반채권으로 변제합니다).")
    notes.append("각 채권자에 대한 월 변제액은 원 단위 미만을 버리고, 남는 금액은 채권액이 큰 채권자부터 1원씩 더했습니다.")
    if r.months > 36:
        notes.append(f"변제기간을 {r.months}개월로 정한 사유: ________________________________ (청산가치 보장 등)")
    today = date.today()
    return f"""<html><head><meta charset="utf-8"><title>{e(title)}</title>
<style>
body {{ font-family: '바탕', 'Batang', serif; font-size: 11pt; line-height: 1.6; }}
h1 {{ text-align: center; font-size: 20pt; letter-spacing: 6px; {'color:#c00000;' if draft else ''} }}
h2 {{ font-size: 13pt; margin-top: 18px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #000; padding: 4px 6px; vertical-align: top; }}
th {{ background: #eee; text-align: left; width: 32%; }}
td.n {{ text-align: right; white-space: nowrap; }} td.c {{ text-align: center; }}
table.list th {{ text-align: center; width: auto; }}
p {{ margin: 3px 0; }}
</style></head><body>
<h1>{e(title)}</h1>
<p>사　　건　{e(case.get('case_no') or '')}　개인회생</p>
<p>채 무 자　{e(case['debtor_name'])}</p>
<h2>1. 변제 조건 요약</h2>
<table>{summ}</table>
<h2>2. 채권자별 변제예정액</h2>
<table class="list"><tr><th>번호</th><th>채권자</th><th>구분</th><th>채권액</th><th>비율</th><th>월 변제액</th><th>총 변제예정액</th></tr>
{''.join(rows)}
<tr><td></td><td><b>합계</b></td><td></td><td class='n'><b>{_n(r.debt_total)}</b></td><td></td>
<td class='n'><b>{_n(r.payment)}</b></td><td class='n'><b>{_n(r.total_pay)}</b></td></tr></table>
<h2>3. 변제 방법</h2>
{''.join(f'<p>{i}. {e(n)}</p>' for i, n in enumerate(notes, start=1))}
<p class="c" style="text-align:center;margin-top:24px">{today.year}. {today.month}. {today.day}.</p>
<p style="text-align:right">채무자의 대리인 ________________ (인)</p>
<p style="text-align:center;margin-top:16px">{e(case.get('court') or '')} 귀중</p>
<p style="margin-top:20px;font-size:9pt">※ 월별 변제표는 별첨 계산 근거표(엑셀)를 참고하십시오.</p>
</body></html>"""
