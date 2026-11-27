"""부채증명서에서 기준일·원금·이자·지연손해금·합계를 뽑고, 입력된 채권과 대조합니다 (3단계).

읽은 값은 '후보'입니다. 화면에서 원문 그림과 대조해 확인·수정한 뒤에만 채권에 반영합니다.
양식이 기관마다 달라서 못 읽는 경우가 있으면 아래 COLUMN_WORDS 키워드를 고치면 됩니다.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .ocr import Line, Page
from .rules import UNKNOWN, claim_total, normalize_name

# 열 종류별 키워드 (긴 말을 먼저 찾음). ignore = 최초 대출금액처럼 지금 갚을 돈이 아닌 열
COLUMN_WORDS = {
    "late": ["지연손해금", "지연배상금", "연체이자", "지연이자", "연체료", "손해금"],
    "ignore": ["최초대출금액", "최초대출금", "대출금액", "약정금액", "대출한도", "한도금액", "대출금"],
    "principal": ["원금잔액", "대출잔액", "채권원금", "이용잔액", "잔여원금", "원금", "잔액"],
    "interest": ["미수이자", "정상이자", "약정이자", "이자"],
    "other": ["가지급금", "법적비용", "법비용", "수수료", "비용"],
    "total": ["채무합계", "합계금액", "합계", "총계", "총액", "총채무"],
}
FIELD_LABEL = {"principal": "원금", "interest": "이자", "late": "지연손해금", "other": "기타(비용 등)", "total": "합계"}
_ALL = sorted(((w, k) for k, ws in COLUMN_WORDS.items() for w in ws), key=lambda x: -len(x[0]))
KW_RE = re.compile("|".join(re.escape(w) for w, _ in _ALL))
KW_TYPE = {w: k for w, k in _ALL}
TOTAL_ROW_WORDS = ("합계", "총계", "총액", "소계")

DATE_RE = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*[.일]?")
AMOUNT_RE = re.compile(r"(?<!\d)(?<!\d[,.])(\d{1,3}(?:[,.]\s?\d{3})+|\d{4,})(?![\d,]|\.\d)|(?<![\w,.\-])-(?![\w,.\-])")
AMOUNT_LOOSE_RE = re.compile(r"(?<![\d,.])\d{1,3}(?![\d,.])")
CONTRACT_DATE_WORDS = ("계약일", "대출일", "약정일", "발생일", "개설일", "거래일")
BASE_DATE_WORDS = ("기준일", "현재", "기준")
PRODUCT_WORDS = ("대출과목", "과목", "상품", "계좌종류", "채권종류", "대출종류")
_CONFUSE = str.maketrans({"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8"})


def compact(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _clean_tail(s: str) -> str:
    """OCR이 줄 끝·앞에 붙이는 잡티(…, _, ~, ㆍ, — 등)를 지우고, '카 드 론'처럼 한 글자씩 띄운 말을 붙임."""
    s = re.sub(r"^[\s.,:;…_~ㆍ·\-—|]+|[\s.,:;…_~ㆍ·\-—|]+$", "", s or "")
    s = re.sub(r"(?<![가-힣])[가-힣](?: [가-힣](?![가-힣]))+", lambda m: m.group(0).replace(" ", ""), s)
    return re.sub(r"\s+", " ", s).strip()


def to_date(m: re.Match) -> str | None:
    y, mo, d = map(int, m.groups())
    return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 and 1950 <= y <= 2100 else None


def _fix_digits(text: str) -> str:
    """'5,OOO,000' 같은 OCR 오인식을 숫자로 고침 (숫자가 섞인 낱말에만)."""
    out = []
    for tok in re.split(r"(\s+)", text):
        core = tok.strip("|[]")
        if core and sum(ch.isdigit() for ch in core) >= 2 and re.fullmatch(r"[\dOolIDSB|,.]+원?", core):
            tok = tok.replace(core, core.translate(_CONFUSE))
        out.append(tok)
    return "".join(out)


@dataclass
class Amount:
    value: int
    start: int   # 줄 글자 위치
    x: float | None = None  # 그림에서 가로 위치(가운데)


def amounts_in(line_text: str, words: list | None = None, line_bbox=None, loose: bool = False) -> list[Amount]:
    """줄에서 금액을 왼쪽부터 찾습니다. 날짜는 먼저 지웁니다. 단독 '-'는 0원.
    loose=True면 '0', '500'처럼 짧은 숫자도 금액으로 봅니다 ('항목 : 금액' 줄에서만 씀)."""
    t = _fix_digits(line_text.replace("|", " ").replace("[", " ").replace("]", " "))
    t = DATE_RE.sub(lambda m: " " * len(m.group(0)), t)
    t = re.sub(r"\d{2,4}-\d{3,4}-\d{4}", lambda m: " " * len(m.group(0)), t)  # 전화번호
    out = []
    for m in AMOUNT_RE.finditer(t):
        tok = m.group(0)
        val = 0 if tok == "-" else int(re.sub(r"[,.\s]", "", tok))
        out.append(Amount(val, m.start()))
    if loose:
        for m in AMOUNT_LOOSE_RE.finditer(t):
            if not any(a.start <= m.start() < a.start + 15 for a in out):
                out.append(Amount(int(m.group(0)), m.start()))
        out.sort(key=lambda a: a.start)
    # 가로 위치: 글자 비율로 줄 bbox에서 추정 (단어 위치가 있으면 그 값을 씀)
    if line_bbox:
        n = max(len(t), 1)
        x0, _, x1, _ = line_bbox
        for a in out:
            a.x = x0 + (x1 - x0) * (a.start + 1) / n
        if words:
            pos = 0
            spans = []
            for wt, wb in words:
                i = line_text.find(wt, pos)
                if i < 0:
                    spans = []
                    break
                spans.append((i, i + len(wt), (wb[0] + wb[2]) / 2))
                pos = i + len(wt)
            for a in out:
                for s, e, cx in spans:
                    if s <= a.start < e:
                        a.x = cx
    return out


@dataclass
class Contract:
    label: str = ""
    date: str | None = None
    principal: int | None = None
    interest: int | None = None
    late: int | None = None
    other: int | None = None
    total: int | None = None
    page: int = 0
    bbox: tuple | None = None

    def computed(self) -> int | None:
        parts = [self.principal, self.interest, self.late]
        if any(p is None for p in parts):
            return None
        return sum(parts) + (self.other or 0)

    def best_total(self) -> int | None:
        return self.total if self.total is not None else self.computed()


@dataclass
class CertResult:
    base_date: str | None = None
    contracts: list[Contract] = field(default_factory=list)
    total_row: Contract | None = None
    debtor_found: bool | None = None
    issuer_line: str | None = None
    checks: list[str] = field(default_factory=list)
    estimated: list[str] = field(default_factory=list)
    mode: str = ""
    marks: list[tuple[int, tuple, str]] = field(default_factory=list)  # (쪽, bbox, 색)
    text: str = ""

    def summary(self) -> Contract:
        """증명서 전체 금액. 합계 줄이 있으면 그 값을, 없으면 계약들을 더한 값."""
        s = Contract(label="증명서 합계")
        for f in ("principal", "interest", "late", "other"):
            vals = [getattr(c, f) for c in self.contracts]
            row = getattr(self.total_row, f) if self.total_row else None
            if row is not None:
                setattr(s, f, row)
            elif vals and all(v is not None for v in vals):
                setattr(s, f, sum(vals))
            elif f == "other" and all(v is None for v in vals):
                setattr(s, f, None)
        tot = self.total_row.total if self.total_row and self.total_row.total is not None else None
        if tot is None:
            ts = [c.best_total() for c in self.contracts]
            tot = sum(ts) if ts and all(t is not None for t in ts) else None
        s.total = tot
        return s

    def as_dict(self):
        return asdict(self)


def _header_columns(line: Line) -> list[tuple[str, float | None]]:
    """머리글 줄에서 (열 종류, 가로 위치) 목록. 두 종류 이상 금액 열이 있어야 머리글로 봄."""
    t = compact(line.text)
    cols = [KW_TYPE[m.group(0)] for m in KW_RE.finditer(t)]
    if len({c for c in cols if c != "ignore"}) < 2:
        return []
    xs: list[float | None] = [None] * len(cols)
    if line.words:
        # 낱말마다 키워드를 찾아 가운데 위치를 기록 (순서대로)
        found = []
        for wt, wb in line.words:
            for m in KW_RE.finditer(compact(wt)):
                found.append((wb[0] + wb[2]) / 2)
        if len(found) == len(cols):
            xs = found
    return list(zip(cols, xs))


def _assign(cols, amts: list[Amount]) -> dict[str, int]:
    """금액을 열에 배정. 열 위치를 알면 가장 가까운 열로, 모르면 오른쪽부터 맞춤."""
    out: dict[str, int] = {}
    if cols and all(x is not None for _, x in cols) and all(a.x is not None for a in amts):
        for a in amts:
            kind = min(cols, key=lambda c: abs(c[1] - a.x))[0]
            if kind != "ignore" and kind not in out:
                out[kind] = a.value
        return out
    kinds = [k for k, _ in cols][-len(amts):] if len(amts) <= len(cols) else [k for k, _ in cols]
    vals = amts[-len(kinds):]
    for k, a in zip(kinds, vals):
        if k != "ignore" and k not in out:
            out[k] = a.value
    return out


def extract(pages: list[Page], debtor_name: str | None = None) -> CertResult:
    res = CertResult()
    all_lines = [(pi, ln) for pi, p in enumerate(pages, start=1) for ln in p.lines]
    res.text = "\n".join(ln.text for _, ln in all_lines)
    kv = Contract()
    kv_used = False
    cols = None
    for pi, ln in all_lines:
        t = ln.text
        ct = compact(t)
        dates = [d for d in (to_date(m) for m in DATE_RE.finditer(t)) if d]
        # 기준일
        if res.base_date is None and dates and any(w in ct for w in BASE_DATE_WORDS):
            res.base_date = dates[0]
            res.marks.append((pi, ln.bbox, "orange"))
        if res.issuer_line is None and re.search(r"채권자|발급기관|금융기관명", ct):
            res.issuer_line = _clean_tail(re.sub(r"^.*?(채\s*권\s*자|발\s*급\s*기\s*관|금융기관명)\s*[:：]?", "", t)) or None
        # 표 머리글
        hdr = _header_columns(ln)
        amts = amounts_in(t, ln.words, ln.bbox)
        if hdr and len([a for a in amts if a.value]) < 2:
            cols = hdr
            res.marks.append((pi, ln.bbox, "gray"))
            continue
        if cols and amts:
            vals = _assign(cols, amts)
            if not vals:
                continue
            label = DATE_RE.sub(" ", t[:amts[0].start] if amts[0].start < len(t) else t)
            label = re.sub(r"[|\[\]]", " ", label).strip()
            row = Contract(label=_clean_tail(label), date=dates[0] if dates else None, page=pi, bbox=ln.bbox,
                           **vals)
            if any(w in compact(label) for w in TOTAL_ROW_WORDS):
                res.total_row = row
            else:
                res.contracts.append(row)
            res.marks.append((pi, ln.bbox, "green"))
            continue
        if cols and not amts and ct and res.contracts:
            cols = None   # 표가 끝남
        # 항목: 금액 형식 (표가 아닌 경우)
        head = re.sub(r"^[^0-9A-Za-z가-힣]+", "", ct)
        m = KW_RE.search(head)
        if m and head.find(m.group(0)) < 6:
            kv_amts = [a for a in amounts_in(t, loose=True) if a.start > t.find(m.group(0)[-1])]
            kind = KW_TYPE[m.group(0)]
            if kv_amts and kind != "ignore" and getattr(kv, kind) is None:
                setattr(kv, kind, kv_amts[-1].value)
                kv_used = True
                res.marks.append((pi, ln.bbox, "green"))
                continue
        if dates and kv.date is None and any(w in ct for w in CONTRACT_DATE_WORDS):
            kv.date = dates[0]
        head = re.sub(r"^[^0-9A-Za-z가-힣]+", "", ct)
        if kv.label == "" and any(head.startswith(w) for w in PRODUCT_WORDS):
            kv.label = _clean_tail(re.sub(r"^.*?[:：]", "", t))
    if kv_used and not res.contracts:
        res.contracts.append(kv)
    elif kv_used and kv.total is not None and res.total_row is None:
        res.total_row = Contract(label="합계", total=kv.total)

    # 한 칸만 비었고 합계를 알면 나머지로 계산해 채움 (추정 표시)
    for i, c in enumerate(res.contracts + ([res.total_row] if res.total_row else []), start=1):
        missing = [f for f in ("principal", "interest", "late") if getattr(c, f) is None]
        if len(missing) == 1 and c.total is not None:
            f = missing[0]
            val = c.total - sum(getattr(c, x) for x in ("principal", "interest", "late") if x != f) - (c.other or 0)
            if val >= 0:
                setattr(c, f, val)
                res.estimated.append(f"{c.label or f'{i}번 계약'}: {FIELD_LABEL[f]} {val:,}원은 글자를 못 읽어 "
                                     f"합계에서 빼서 계산한 값입니다. 원문과 꼭 대조하세요.")

    # 채무자 이름
    if debtor_name:
        name = compact(re.sub(r"\(.*?\)", "", debtor_name))
        res.debtor_found = bool(name) and name in compact(res.text)
        if not res.debtor_found:
            res.checks.append(f"채무자 이름 '{debtor_name}'을(를) 찾지 못했습니다. 다른 사람의 서류가 아닌지 확인하세요 "
                              "(글자 인식 오류일 수도 있습니다).")
    # 계산 검증
    if not res.contracts and not res.total_row:
        res.checks.append("금액을 찾지 못했습니다. 원문을 보고 직접 입력하세요.")
    if res.base_date is None:
        res.checks.append("금액 기준일(○○ 현재)을 찾지 못했습니다. 직접 입력하세요.")
    for i, c in enumerate(res.contracts, start=1):
        comp = c.computed()
        if c.total is not None and comp is not None and comp != c.total:
            res.checks.append(f"{i}번 계약({c.label or '과목 미상'}): 원금+이자+지연손해금{'+기타' if c.other else ''} = "
                              f"{comp:,}원인데 합계는 {c.total:,}원입니다. 잘못 읽은 칸이 있는지 확인하세요.")
        if c.principal is None:
            res.checks.append(f"{i}번 계약({c.label or '과목 미상'}): 원금을 찾지 못했습니다.")
    if res.total_row and res.total_row.total is not None and len(res.contracts) > 1:
        ts = [c.best_total() for c in res.contracts]
        if all(t is not None for t in ts) and sum(ts) != res.total_row.total:
            res.checks.append(f"계약별 합계를 더하면 {sum(ts):,}원인데 증명서 합계 줄은 {res.total_row.total:,}원입니다.")
    return res


def guess_creditor(text: str, creditors: list[dict], claims: list[dict]) -> int | None:
    """증명서 글에 이름이 가장 길게 들어 있는 채권자 (양도 전 기관명도 봄)."""
    t = normalize_name(text)
    best, best_len = None, 0
    for c in creditors:
        names = [c["name"]] + [cl["transferred_from"] for cl in claims
                               if cl["creditor_id"] == c["id"] and cl.get("transferred_from")]
        for n in names:
            nn = normalize_name(n)
            if len(nn) >= 2 and nn in t and len(nn) > best_len:
                best, best_len = c["id"], len(nn)
    return best


def score(r: CertResult) -> int:
    """두 번 읽은 결과 중 더 나은 것을 고르는 점수."""
    s = 0
    for c in r.contracts + ([r.total_row] if r.total_row else []):
        s += sum(2 for f in ("principal", "interest", "late", "total") if getattr(c, f) is not None)
    s += 3 if r.debtor_found else 0
    s += 2 if r.base_date else 0
    s -= 3 * len(r.checks) + 2 * len(r.estimated)
    return s


def read_certificate(data: bytes, filename: str, debtor_name: str | None, rotate: int = 0,
                     split_pages: bool = False) -> list[tuple[CertResult, list]]:
    """파일을 읽어 금액을 뽑습니다. 스캔본이면 두 가지 방식으로 읽어 점수가 높은 쪽을 씁니다.
    split_pages=True면 쪽마다 다른 증명서로 보고 따로 뽑습니다 (대행 업체가 한 파일로 묶어 보낸 경우).
    돌려주는 값: [(결과, 그 결과의 쪽 목록)]"""
    from . import ocr
    modes = ["table", "plain"] if ocr.needs_ocr(data, filename) else ["table"]
    best: list | None = None
    for m in modes:
        pages = ocr.read_file(data, filename, rotate=rotate, mode=m)
        groups = [[p] for p in pages] if split_pages else [pages]
        cur = []
        for g in groups:
            r = extract(g, debtor_name)
            r.mode = m
            cur.append((r, g))
        if best is None:
            best = cur
        else:
            best = [b if score(b[0]) >= score(c[0]) else c for b, c in zip(best, cur)]
        if all(score(r) >= 20 and not r.checks and not r.estimated for r, _ in best):
            break   # 충분히 잘 읽힘
    return best


# ---------------- 대조 ----------------
def compare(summary: Contract, claims: list[dict]) -> list[dict]:
    """증명서 금액과 입력된 채권 합계를 항목별로 비교."""
    rows = []
    for f, cf in (("principal", "principal"), ("interest", "interest"), ("late", "late_fee")):
        vals = [c.get(cf) for c in claims]
        mine = sum(vals) if vals and all(v is not None for v in vals) else None
        cert = getattr(summary, f)
        rows.append({"항목": FIELD_LABEL[f], "증명서": cert, "입력된 채권": mine,
                     "차이": (cert - mine) if cert is not None and mine is not None else None})
    totals = [claim_total(c) for c in claims]
    mine = sum(totals) if totals and all(t != UNKNOWN for t in totals) else None
    rows.append({"항목": "합계", "증명서": summary.total, "입력된 채권": mine,
                 "차이": (summary.total - mine) if summary.total is not None and mine is not None else None})
    return rows


def default_targets(contracts: list[Contract], claims: list[dict]) -> list[int | None]:
    """증명서의 계약마다 반영할 기존 채권 id (없으면 None = 새 채권).
    발생일이 같은 것 → 과목 이름이 겹치는 것 → 개수가 같으면 순서대로."""
    used: set[int] = set()
    out: list[int | None] = [None] * len(contracts)
    for i, c in enumerate(contracts):
        for cl in claims:
            if cl["id"] not in used and c.date and cl.get("occurred_date") == c.date:
                out[i] = cl["id"]
                used.add(cl["id"])
                break
    for i, c in enumerate(contracts):
        if out[i] is None and c.label:
            lab = compact(c.label)
            for cl in claims:
                cause = compact(cl.get("cause") or "")
                if cl["id"] not in used and cause and (lab in cause or cause in lab):
                    out[i] = cl["id"]
                    used.add(cl["id"])
                    break
    if len(contracts) == len(claims):
        rest = [cl["id"] for cl in claims if cl["id"] not in used]
        for i in range(len(contracts)):
            if out[i] is None and rest:
                out[i] = rest.pop(0)
    return out
