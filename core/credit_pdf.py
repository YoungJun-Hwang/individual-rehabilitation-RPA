"""신용정보조회서 PDF 자동 읽기 (2단계).

방식
1. 표가 있으면 표에서 읽습니다. 머리글(기관명·잔액·일자 등)로 열을 찾습니다.
2. 표가 없는 쪽은 한 줄씩 읽어 '기관명 … 금액' 형태의 줄을 찾습니다.
3. 결과는 바로 저장하지 않고 '후보'로 돌려줍니다. 사람이 원문과 대조해 고른 것만 저장합니다.

PDF 파일은 디스크에 저장하지 않고 메모리에서만 읽습니다.
양식은 발급처(한국신용정보원, NICE, KCB 등)와 시기에 따라 다르므로, 실제 조회서로 시험해 보고
안 맞는 부분은 아래 키워드 목록을 고치면 됩니다.
"""
from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field

import pdfplumber

from .rules import normalize_name

# ---- 키워드 (양식에 맞춰 고칠 수 있음) ----
INST_HEADERS = ["기관명", "기관", "카드사", "금융회사", "회사명", "채권자", "업체"]
AMOUNT_HEADERS = ["잔액", "연체금액", "채무액", "체납액", "이용잔액", "금액"]  # 앞에 있을수록 우선
DATE_HEADERS = ["일자", "발생일", "개설일", "대출일", "날짜"]
PRODUCT_HEADERS = ["과목", "상품", "종류", "구분", "계정"]
SKIP_SECTION_WORDS = ["조회기록", "조회 기록", "조회정보", "조회 정보", "조회내역", "조회 내역"]
SKIP_ROW_WORDS = ["합계", "소계", "총계", "총 합계"]
INST_WORDS = ["은행", "뱅크", "카드", "캐피탈", "저축", "대부", "보험", "생명", "화재", "손해", "공단", "공사", "금고",
              "조합", "신협", "농협", "수협", "축협", "산림", "파이낸", "증권", "머니", "크레디트", "자산",
              "에셋", "펀딩", "세무서", "구청", "시청", "군청", "통신", "텔레콤", "모바일", "유플러스", "KT", "SK"]

UNIT_RE = re.compile(r"단위\s*[:：]?\s*(백만원|천원|만원|원)")
UNIT_MULT = {"원": 1, "천원": 1_000, "만원": 10_000, "백만원": 1_000_000}
AMOUNT_RE = re.compile(r"^-?\d{1,3}(,\d{3})+$|^-?\d+$")
DATE_RE = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")


class PasswordRequired(Exception):
    pass


@dataclass
class Candidate:
    institution: str
    product: str | None
    amount: int | None          # 원 단위로 바꾼 값
    date: str | None            # YYYY-MM-DD
    section: str
    page: int                   # 1부터
    raw: str                    # 원문 한 줄/행
    bbox: tuple | None = None   # 원문 위치 (x0, top, x1, bottom)
    include: bool = True
    reason: str = ""

    def as_dict(self):
        return asdict(self)


@dataclass
class ParseResult:
    candidates: list[Candidate] = field(default_factory=list)
    page_count: int = 0
    scanned_pages: list[int] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------- 작은 도우미 ----------------
def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def parse_amount(s, mult: int = 1) -> int | None:
    s = _clean(s).replace(" ", "").replace("원", "")
    if not s or not AMOUNT_RE.match(s):
        return None
    return int(s.replace(",", "")) * mult


def parse_date(s) -> str | None:
    m = DATE_RE.search(_clean(s))
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def find_unit(text: str) -> int | None:
    found = UNIT_RE.findall(text or "")
    return UNIT_MULT[found[-1]] if found else None


def is_skip_section(text: str) -> bool:
    t = _clean(text)
    return any(w in t for w in SKIP_SECTION_WORDS)


def looks_like_institution(tok: str) -> bool:
    return any(w.lower() in tok.lower() for w in INST_WORDS)


def _last_heading(text: str) -> str:
    """표 위쪽 글에서 섹션 제목으로 쓸 마지막 줄 (단위 표시 줄은 건너뜀)."""
    lines = [l for l in (_clean(x) for x in (text or "").splitlines()) if l]
    for l in reversed(lines):
        if not UNIT_RE.search(l) or len(UNIT_RE.sub("", l).strip(" ()")) > 2:
            return UNIT_RE.sub("", l).strip(" ()") or l
    return ""


def _col(headers: list[str], keys: list[str]) -> int | None:
    for k in keys:                       # 키워드 우선순위대로
        for i, h in enumerate(headers):
            if k in h:
                return i
    return None


# ---------------- 표에서 읽기 ----------------
def _parse_tables(page, pno: int, state: dict) -> tuple[list[Candidate], bool]:
    out: list[Candidate] = []
    tables = sorted(page.find_tables(), key=lambda t: t.bbox[1])
    if not tables:
        return out, False
    prev_bottom = 0
    for t in tables:
        top = t.bbox[1]
        above = page.crop((0, prev_bottom, page.width, max(prev_bottom + 0.1, top))).extract_text() or ""
        prev_bottom = t.bbox[3]
        heading = _last_heading(above)
        if heading:
            state["section"] = heading
        unit = find_unit(above)
        if unit:
            state["unit"] = unit
        rows = t.extract()
        if not rows:
            continue
        headers = [_clean(h) for h in rows[0]]
        inst_i = _col(headers, INST_HEADERS)
        amt_i = _col(headers, AMOUNT_HEADERS)
        if inst_i is None:
            # 머리글이 없는 이어지는 표(앞 쪽 표의 계속): 앞 표의 열 위치를 씀
            if state.get("cols") and len(headers) == state["cols"]["n"]:
                inst_i, amt_i = state["cols"]["inst"], state["cols"]["amt"]
                date_i, prod_i = state["cols"]["date"], state["cols"]["prod"]
                body = list(zip(rows, t.rows))
            else:
                continue
        else:
            date_i, prod_i = _col(headers, DATE_HEADERS), _col(headers, PRODUCT_HEADERS)
            state["cols"] = {"n": len(headers), "inst": inst_i, "amt": amt_i, "date": date_i, "prod": prod_i}
            body = list(zip(rows[1:], t.rows[1:]))
        section = state.get("section", "")
        if is_skip_section(section) or any(is_skip_section(h) for h in headers):
            state["skip"] = True
            continue
        state["skip"] = False
        mult = state.get("unit") or 1
        for cells, row in body:
            cells = [_clean(c) for c in cells]
            inst = cells[inst_i] if inst_i < len(cells) else ""
            if not inst or any(w in inst for w in SKIP_ROW_WORDS):
                continue
            amt = parse_amount(cells[amt_i], mult) if amt_i is not None and amt_i < len(cells) else None
            out.append(Candidate(
                institution=inst,
                product=(cells[prod_i] or None) if prod_i is not None and prod_i < len(cells) else None,
                amount=amt,
                date=parse_date(cells[date_i]) if date_i is not None and date_i < len(cells) else None,
                section=section, page=pno, raw=" | ".join(c for c in cells if c),
                bbox=tuple(round(v, 1) for v in row.bbox) if row.bbox else None))
    return out, True


# ---------------- 줄 단위로 읽기 (표가 없을 때) ----------------
def _parse_lines(page, pno: int, state: dict) -> list[Candidate]:
    out: list[Candidate] = []
    for ln in page.extract_text_lines():
        text = _clean(ln["text"])
        if not text:
            continue
        unit = find_unit(text)
        if unit:
            state["unit"] = unit
        toks = text.split(" ")
        amount_tok_idx = next((i for i in range(len(toks) - 1, -1, -1)
                               if AMOUNT_RE.match(toks[i].replace("원", "")) and not DATE_RE.match(toks[i])), None)
        has_amount = amount_tok_idx is not None and amount_tok_idx > 0
        if not has_amount:
            # 금액이 없는 줄은 섹션 제목으로 봄
            if len(text) <= 40:
                state["section"] = UNIT_RE.sub("", text).strip(" ()") or text
                state["skip"] = is_skip_section(text)
            continue
        if state.get("skip") or any(toks[0].startswith(w) for w in SKIP_ROW_WORDS):
            continue
        # 기관명: 첫 토큰 (앞이 '주식회사' 같은 말이면 다음 토큰과 합침)
        inst_end = 1
        if toks[0] in ("주식회사", "(주)", "㈜", "유한회사") and len(toks) > 2:
            inst_end = 2
        inst = " ".join(toks[:inst_end])
        if not looks_like_institution(inst):
            continue
        date_idx = next((i for i, t in enumerate(toks) if DATE_RE.match(t)), None)
        prod_end = min(x for x in [date_idx, amount_tok_idx] if x is not None)
        product = " ".join(toks[inst_end:prod_end]) or None
        out.append(Candidate(
            institution=inst, product=product,
            amount=parse_amount(toks[amount_tok_idx], state.get("unit") or 1),
            date=parse_date(toks[date_idx]) if date_idx is not None else None,
            section=state.get("section", ""), page=pno, raw=text,
            bbox=(round(ln["x0"], 1), round(ln["top"], 1), round(ln["x1"], 1), round(ln["bottom"], 1))))
    return out


# ---------------- 기본값 정하기 ----------------
def _set_defaults(cands: list[Candidate]) -> None:
    seen: dict[str, Candidate] = {}
    for c in cands:
        key = normalize_name(c.institution)
        if c.amount is None:
            c.reason = "금액을 읽지 못함 — 원문 확인"
        elif c.amount == 0:
            c.include, c.reason = False, "잔액 0원"
        if "연체" in c.section and key in seen:
            c.include, c.reason = False, f"연체정보 — 같은 기관이 '{seen[key].section}'에 이미 있음"
        if c.include:
            for prev in cands:
                if prev is c:
                    break
                if prev.include and normalize_name(prev.institution) == key and prev.amount == c.amount \
                        and prev.product == c.product:
                    c.include, c.reason = False, "같은 내용이 이미 있음 (중복)"
                    break
        seen.setdefault(key, c)


# ---------------- 진입점 ----------------
def parse(data: bytes, password: str | None = None) -> ParseResult:
    res = ParseResult()
    try:
        pdf = pdfplumber.open(io.BytesIO(data), password=password or "")
        _ = pdf.pages  # 암호가 틀리면 여기서 오류
    except Exception as e:  # pdfminer의 PDFPasswordIncorrect 등
        # pdfplumber는 PdfminerException(PDFPasswordIncorrect()) 처럼 감싸서 던짐
        name = type(e).__name__ + " ".join(type(a).__name__ for a in getattr(e, "args", ()))
        if "Password" in name or "password" in str(e).lower() or "encrypt" in str(e).lower():
            raise PasswordRequired("PDF에 비밀번호가 걸려 있습니다." if not password else "비밀번호가 맞지 않습니다.")
        raise ValueError(f"PDF를 열 수 없습니다: {e}")
    state: dict = {}
    with pdf:
        res.page_count = len(pdf.pages)
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if len(text.strip()) < 20:
                res.scanned_pages.append(pno)
                continue
            got, had_tables = _parse_tables(page, pno, state)
            if not had_tables or not got:
                # 표가 없거나 표에서 하나도 못 찾은 쪽은 줄 단위로 다시 읽음
                got = _parse_lines(page, pno, dict(state)) if not had_tables else got
            res.candidates.extend(got)
    _set_defaults(res.candidates)
    if res.scanned_pages:
        res.notes.append(f"{', '.join(map(str, res.scanned_pages))}쪽은 글자가 없는 이미지(스캔본)입니다. "
                         "이 쪽은 직접 입력하세요 (스캔본 글자 인식은 3단계 예정).")
    if not res.candidates and not res.scanned_pages:
        res.notes.append("읽어 낸 항목이 없습니다. 조회서 양식이 예상과 다를 수 있습니다. 직접 입력하거나, "
                         "가린 샘플을 주시면 양식에 맞춰 고칠 수 있습니다.")
    return res


def render_page(data: bytes, page_no: int, boxes: list[tuple[tuple, bool]], password: str | None = None,
                resolution: int = 110):
    """원문 대조용: 해당 쪽 그림에 후보 위치를 표시합니다 (초록=가져옴, 회색=제외)."""
    with pdfplumber.open(io.BytesIO(data), password=password or "") as pdf:
        page = pdf.pages[page_no - 1]
        im = page.to_image(resolution=resolution)
        for bbox, inc in boxes:
            if bbox:
                im.draw_rect(bbox, fill=(0, 160, 80, 40) if inc else (120, 120, 120, 30),
                             stroke=(0, 140, 60) if inc else (150, 150, 150), stroke_width=2)
        return im.annotated
