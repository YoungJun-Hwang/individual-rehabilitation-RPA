"""엑셀·HTML·HWP 출력."""
from __future__ import annotations

import html
import sys
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .listing import AMOUNT_COLUMNS, COLUMNS, fmt_amount

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
TEMPLATE_DIR = BASE_DIR / "templates"
HWP_TEMPLATE = TEMPLATE_DIR / "채권자목록_양식.hwp"

THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _safe(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch not in '\\/:*?"<>|').strip() or "사건"


def output_name(case: dict, kind: str, ext: str, draft: bool) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    tag = "_초안" if draft else ""
    return OUTPUT_DIR / f"{_safe(case.get('case_no') or '')}_{_safe(case['debtor_name'])}_{kind}{tag}_{date.today():%Y%m%d}.{ext}"


def header_lines(case: dict, draft: bool) -> list[str]:
    lines = [
        f"사건번호: {case.get('case_no') or '(미부여)'}",
        f"채무자: {case['debtor_name']}",
        f"관할 법원: {case.get('court') or ''}",
        f"채권자목록 기준일: {case.get('list_base_date') or '미확인'}",
    ]
    if draft:
        lines.append("※ 초안 - 변호사 확인 전 문서입니다. 제출용으로 쓰지 마세요.")
    else:
        lines.append(f"변호사 확인: {case.get('approved_by_name') or ''} ({case.get('approved_at') or ''})")
    return lines


# ---------------- 엑셀 ----------------
def creditor_list_xlsx(case: dict, rows: list[dict], totals: dict, draft: bool, path: Path | None = None) -> Path:
    path = path or output_name(case, "채권자목록", "xlsx", draft)
    wb = Workbook()
    ws = wb.active
    ws.title = "채권자목록"
    ws.append(["채권자목록" + (" [초안]" if draft else "")])
    ws["A1"].font = Font(size=16, bold=True, color="C00000" if draft else "000000")
    for line in header_lines(case, draft):
        ws.append([line])
    ws.append([])
    hdr_row = ws.max_row + 1
    ws.append(COLUMNS)
    for c in ws[hdr_row]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E7E6E6")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
    for r in rows:
        ws.append([r[k] for k in COLUMNS])
        for idx, c in enumerate(ws[ws.max_row]):
            col = COLUMNS[idx]
            c.border = BORDER
            c.alignment = Alignment(vertical="top", wrap_text=True,
                                    horizontal="right" if col in AMOUNT_COLUMNS else "left")
            if col in AMOUNT_COLUMNS and isinstance(c.value, int):
                c.number_format = "#,##0"
            if c.value == "미확인":
                c.font = Font(color="C00000")
    tot = ["합계", "", "", "", "", "", totals["원금"], totals["이자"], totals["지연손해금"], totals["합계"], "",
           f"금액 미확인 {totals['미확인']}건 있음 (확인된 금액만 합산)" if totals["미확인"] else ""]
    ws.append(tot)
    for idx, c in enumerate(ws[ws.max_row]):
        c.font = Font(bold=True)
        c.border = BORDER
        if COLUMNS[idx] in AMOUNT_COLUMNS:
            c.number_format = "#,##0"
            c.alignment = Alignment(horizontal="right")
    widths = [8, 20, 30, 22, 20, 12, 14, 12, 12, 14, 12, 30]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=hdr_row + 1, column=1)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToHeight = 0
    wb.save(path)
    return path


def agency_request_xlsx(case: dict, creditors: list[dict], path: Path | None = None) -> Path:
    """부채증명서 발급대행 업체에 보낼 요청 목록."""
    path = path or OUTPUT_DIR / f"{_safe(case.get('case_no') or '')}_{_safe(case['debtor_name'])}_부채증명서요청목록_{date.today():%Y%m%d}.xlsx"
    OUTPUT_DIR.mkdir(exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "발급요청"
    ws.append(["부채증명서 발급 요청 목록"])
    ws["A1"].font = Font(size=14, bold=True)
    ws.append([f"채무자: {case['debtor_name']}   생년월일: {case.get('debtor_birth') or ''}   "
               f"요청일: {date.today():%Y-%m-%d}"])
    ws.append([])
    cols = ["번호", "기관명", "사업자번호", "주소", "전화", "팩스", "현재 상태", "요청일", "수령일", "비고"]
    ws.append(cols)
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E7E6E6")
        c.border = BORDER
    for i, cr in enumerate(creditors, start=1):
        ws.append([i, cr["name"], cr.get("biz_no") or "", cr.get("address") or "", cr.get("phone") or "",
                   cr.get("fax") or "", cr.get("debt_cert_status") or "미요청",
                   cr.get("debt_cert_requested_at") or "", cr.get("debt_cert_received_at") or "", ""])
        for c in ws[ws.max_row]:
            c.border = BORDER
    for i, w in enumerate([6, 24, 14, 34, 14, 14, 10, 12, 12, 20], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    wb.save(path)
    return path


# ---------------- HTML (미리보기, HWP 변환용) ----------------
def creditor_list_html(case: dict, rows: list[dict], totals: dict, draft: bool) -> str:
    e = html.escape
    head = "".join(f"<p>{e(l)}</p>" for l in header_lines(case, draft))
    th = "".join(f"<th>{e(c)}</th>" for c in COLUMNS)
    body = []
    for r in rows:
        tds = []
        for c in COLUMNS:
            v = r[c]
            txt = fmt_amount(v) if c in AMOUNT_COLUMNS else (v or "")
            cls = ' class="num"' if c in AMOUNT_COLUMNS else ""
            style = ' style="color:#c00000"' if txt == "미확인" else ""
            tds.append(f"<td{cls}{style}>{e(str(txt))}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    tot = (f"<tr class='total'><td colspan='6'>합계</td><td class='num'>{totals['원금']:,}</td>"
           f"<td class='num'>{totals['이자']:,}</td><td class='num'>{totals['지연손해금']:,}</td>"
           f"<td class='num'>{totals['합계']:,}</td><td></td>"
           f"<td>{'금액 미확인 ' + str(totals['미확인']) + '건 있음 (확인된 금액만 합산)' if totals['미확인'] else ''}</td></tr>")
    title = "채권자목록" + (" [초안]" if draft else "")
    return f"""<html><head><meta charset="utf-8"><title>{e(title)}</title>
<style>
body {{ font-family: '맑은 고딕', 'Malgun Gothic', sans-serif; font-size: 10pt; }}
h1 {{ font-size: 16pt; text-align: center; {'color:#c00000;' if draft else ''} }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #000; padding: 3px 4px; vertical-align: top; }}
th {{ background: #e7e6e6; }}
td.num {{ text-align: right; white-space: nowrap; }}
tr.total td {{ font-weight: bold; }}
p {{ margin: 2px 0; }}
</style></head><body>
<h1>{e(title)}</h1>
{head}
<table><thead><tr>{th}</tr></thead><tbody>
{''.join(body)}
{tot}
</tbody></table></body></html>"""


# ---------------- HWP (Windows + 한컴오피스 필요) ----------------
class HwpUnavailable(RuntimeError):
    pass


def hwp_available() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "HWP 출력은 한글(한컴오피스)이 설치된 Windows PC에서만 됩니다."
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return False, "pywin32가 설치되어 있지 않습니다. 실행.bat을 다시 실행하세요."
    return True, ""


def _open_hwp(visible: bool = False):
    import win32com.client as win32
    try:
        hwp = win32.gencache.EnsureDispatch("HWPFrame.HwpObject")
    except Exception as ex:  # 한글 미설치 등
        raise HwpUnavailable(f"한글 프로그램을 실행할 수 없습니다: {ex}")
    try:
        # 보안 모듈이 등록돼 있으면 '파일 접근 허용' 팝업이 뜨지 않습니다 (README 참고)
        hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")
    except Exception:
        pass
    try:
        hwp.XHwpWindows.Item(0).Visible = visible
    except Exception:
        pass
    return hwp


# 양식 파일에 넣을 누름틀(필드) 이름. 표의 한 행에 아래 행 필드를 넣고,
# 그 행을 필요한 만큼 복사해 두면 위에서부터 차례로 채워집니다.
HWP_HEADER_FIELDS = {"사건번호": "case_no", "채무자": "debtor_name", "법원": "court", "기준일": "list_base_date"}
HWP_ROW_FIELDS = {"번호": "채권번호", "채권자": "채권자", "주소": "주소", "연락처": "연락처",
                  "원인": "채권의 원인", "발생일": "발생일", "원금": "원금", "이자": "이자",
                  "지연손해금": "지연손해금", "합계": "합계", "산정기준일": "금액 기준일", "비고": "비고"}
HWP_TOTAL_FIELDS = {"합계_원금": "원금", "합계_이자": "이자", "합계_지연손해금": "지연손해금", "합계_합계": "합계"}


def _fill_template(hwp, case, rows, totals, draft):
    names = hwp.GetFieldList(1, 0).split("\x02") if hwp.GetFieldList(1, 0) else []
    capacity = sum(1 for n in names if n.startswith("채권자{{")) or (1 if "채권자" in names else 0)
    if capacity < len(rows):
        raise HwpUnavailable(
            f"양식의 표에 채권 행이 {capacity}줄밖에 없는데 {len(rows)}줄이 필요합니다. "
            "양식 표의 행을 더 복사해 두거나, 양식 없이 출력하세요.")
    for fname, key in HWP_HEADER_FIELDS.items():
        if hwp.FieldExist(fname):
            hwp.PutFieldText(fname, str(case.get(key) or ""))
    if hwp.FieldExist("작성일"):
        hwp.PutFieldText("작성일", f"{date.today():%Y. %m. %d.}")
    if hwp.FieldExist("초안표시"):
        hwp.PutFieldText("초안표시", "[초안 - 변호사 확인 전]" if draft else "")
    for i in range(capacity):
        r = rows[i] if i < len(rows) else None
        for fname, col in HWP_ROW_FIELDS.items():
            target = f"{fname}{{{{{i}}}}}" if capacity > 1 else fname
            if not hwp.FieldExist(target):
                continue
            if r is None:
                val = ""
            else:
                v = r[col]
                val = fmt_amount(v) if col in AMOUNT_COLUMNS else str(v or "")
            hwp.PutFieldText(target, val)
    for fname, key in HWP_TOTAL_FIELDS.items():
        if hwp.FieldExist(fname):
            hwp.PutFieldText(fname, f"{totals[key]:,}")
    if hwp.FieldExist("미확인건수"):
        hwp.PutFieldText("미확인건수", f"금액 미확인 {totals['미확인']}건" if totals["미확인"] else "")


def creditor_list_hwp(case: dict, rows: list[dict], totals: dict, draft: bool,
                      use_template: bool = True) -> tuple[Path, str]:
    """HWP 파일을 만듭니다. 양식이 있으면 양식의 누름틀을 채우고, 없으면 표를 새로 만듭니다.
    돌려주는 값: (파일 경로, 사용한 방식 설명)
    """
    ok, why = hwp_available()
    if not ok:
        raise HwpUnavailable(why)
    out = output_name(case, "채권자목록", "hwp", draft).resolve()
    hwp = _open_hwp()
    try:
        if use_template and HWP_TEMPLATE.exists():
            if not hwp.Open(str(HWP_TEMPLATE.resolve()), "HWP", "forceopen:true"):
                raise HwpUnavailable("양식 파일을 열지 못했습니다.")
            _fill_template(hwp, case, rows, totals, draft)
            mode = "사무소 양식에 채움"
        else:
            tmp = out.with_suffix(".html")
            tmp.write_text(creditor_list_html(case, rows, totals, draft), encoding="utf-8")
            if not hwp.Open(str(tmp), "HTML", "forceopen:true"):
                raise HwpUnavailable("HTML을 한글로 열지 못했습니다.")
            mode = "기본 표 형식 (양식 없음)"
        hwp.SaveAs(str(out), "HWP", "")
        return out, mode
    finally:
        try:
            hwp.Clear(1)
            hwp.Quit()
        except Exception:
            pass
        tmp = out.with_suffix(".html")
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
