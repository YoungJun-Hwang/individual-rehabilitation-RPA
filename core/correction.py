"""보정명령 관리 (4단계): 명령문을 항목별로 나누고, 분류·필요 서류를 추천하고, 기한을 계산하고,
의뢰인 자료 요청 안내문과 보정서 초안을 만듭니다.

명령문 분해는 '1.', '가.', '①' 같은 번호를 기준으로 합니다. 결과는 화면에서 고칠 수 있습니다.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

DOC_STATES = ["요청 전", "요청함", "받음", "생략"]
DONE_DOC_STATES = {"받음", "생략"}

C_DRAFT, C_REVIEW, C_APPROVED, C_SUBMITTED = "작성 중", "검토 요청", "변호사 승인", "제출 완료"
C_STATUSES = [C_DRAFT, C_REVIEW, C_APPROVED, C_SUBMITTED]

_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_HANGUL_ORDER = "가나다라마바사아자차카타파하"
TOP_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]\s*(.*)$")
SUB_RE = re.compile(rf"^\s*([{_HANGUL_ORDER}])\s*[.)]\s*(.*)$")
CIRC_RE = re.compile(rf"^\s*([{_CIRCLED}])\s*(.*)$")
START_RE = re.compile(r"^\s*다\s*음\s*$|다음\s*사항을?\s*보정|아래\s*사항을?\s*보정")
END_RE = re.compile(r"^\s*(판\s*사|사\s*법\s*보\s*좌\s*관|법원\s*주사|주\s*의\s*사\s*항|※)")
DATE_RE = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
DAYS_RE = re.compile(r"(\d{1,2})\s*일\s*(?:안에|이내|내에|내)")
CASE_NO_RE = re.compile(r"\d{4}\s*개회\s*\d+")


def load_json(name: str):
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return json.load(f)


# ---------------- 명령문 분해 ----------------
@dataclass
class Item:
    no: str
    text: str
    heading: str = ""
    category: str = "기타"
    docs: list[str] = field(default_factory=list)
    answer: str = ""


@dataclass
class ParsedOrder:
    case_no: str | None = None
    order_date: str | None = None
    days: int | None = None
    items: list[Item] = field(default_factory=list)


def _to_date(m) -> str | None:
    y, mo, d = map(int, m.groups())
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def parse_order(text: str) -> ParsedOrder:
    res = ParsedOrder()
    t = text or ""
    m = CASE_NO_RE.search(t)
    res.case_no = re.sub(r"\s+", "", m.group(0)) if m else None
    m = DAYS_RE.search(t)
    res.days = int(m.group(1)) if m else None
    lines = [l.rstrip() for l in t.splitlines()]
    # 본문 범위: '다음' 뒤부터 '판사' 앞까지 (없으면 전체)
    start = next((i + 1 for i, l in enumerate(lines) if START_RE.search(l)), 0)
    body = []
    for i, l in enumerate(lines[start:], start=start):
        if END_RE.match(l):
            break
        dm = DATE_RE.search(l)
        if dm and len(re.sub(r"[\d\s.\-/년월일]", "", l)) <= 2:   # 날짜만 있는 줄 = 명령일
            res.order_date = _to_date(dm)
            continue
        body.append(l)
    if res.order_date is None:
        dates = [d for d in (_to_date(m) for m in DATE_RE.finditer(t)) if d]
        res.order_date = dates[-1] if dates else None

    tops: list[dict] = []
    cur = None
    for l in body:
        if not l.strip():
            continue
        mt, ms, mc = TOP_RE.match(l), SUB_RE.match(l), CIRC_RE.match(l)
        if mt and not DATE_RE.match(l.strip()):
            cur = {"no": mt.group(1), "text": mt.group(2).strip(), "subs": [], "plain": False}
            tops.append(cur)
        elif (ms or mc) and (cur is None or cur.get("plain")):
            # 숫자 없이 가., 나. 또는 ①, ②로만 된 명령: 그 자체가 항목
            mm = ms or mc
            no = mm.group(1) if ms else str(_CIRCLED.index(mm.group(1)) + 1)
            cur = {"no": no, "text": mm.group(2).strip(), "subs": [], "plain": True}
            tops.append(cur)
        elif (ms or mc) and cur is not None:
            mm = ms or mc
            no = mm.group(1) if ms else str(_CIRCLED.index(mm.group(1)) + 1)
            cur["subs"].append({"no": no, "text": mm.group(2).strip()})
        elif cur is not None:
            target = cur["subs"][-1] if cur["subs"] else cur
            target["text"] = (target["text"] + " " + l.strip()).strip()
    if not tops:
        whole = " ".join(l.strip() for l in body if l.strip())
        if whole:
            tops = [{"no": "1", "text": whole, "subs": []}]
    for tp in tops:
        if tp["subs"]:
            for s in tp["subs"]:
                res.items.append(Item(no=f"{tp['no']}-{s['no']}", text=s["text"], heading=tp["text"]))
        else:
            res.items.append(Item(no=tp["no"], text=tp["text"]))
    rules = load_json("correction_rules.json")
    for it in res.items:
        it.category, it.answer = classify(f"{it.heading} {it.text}", rules)
        it.docs = suggest_docs(it.text, rules)
    return res


def classify(text: str, rules: dict | None = None) -> tuple[str, str]:
    rules = rules or load_json("correction_rules.json")
    compact = re.sub(r"\s+", "", text)
    for c in rules["categories"]:
        if any(re.sub(r"\s+", "", k) in compact for k in c["keywords"]):
            return c["name"], c.get("answer", "")
    last = rules["categories"][-1]
    return last["name"], last.get("answer", "")


def categories() -> list[str]:
    return [c["name"] for c in load_json("correction_rules.json")["categories"]]


def suggest_docs(text: str, rules: dict | None = None) -> list[str]:
    rules = rules or load_json("correction_rules.json")
    compact = re.sub(r"\s+", "", text)
    out = []
    for d in rules["documents"]:
        if any(re.sub(r"\s+", "", k) in compact for k in d["keywords"]) and d["doc"] not in out:
            out.append(d["doc"])
    return out


# ---------------- 기한 ----------------
def holidays() -> set[str]:
    try:
        return set(load_json("holidays.json")["dates"])
    except FileNotFoundError:
        return set()


def compute_deadline(served: str, days: int, extra_holidays: set[str] | None = None) -> str:
    """송달일 다음 날부터 세어 days일째 (초일 불산입). 끝나는 날이 토·일·공휴일이면 다음 평일."""
    hs = holidays() if extra_holidays is None else extra_holidays
    d = date.fromisoformat(served) + timedelta(days=days)
    while d.weekday() >= 5 or d.isoformat() in hs:
        d += timedelta(days=1)
    return d.isoformat()


def d_day(deadline: str | None, today: date | None = None) -> int | None:
    if not deadline:
        return None
    return (date.fromisoformat(deadline) - (today or date.today())).days


def d_day_label(n: int | None) -> str:
    if n is None:
        return "기한 미정"
    if n < 0:
        return f"기한 {-n}일 지남"
    return "오늘 마감" if n == 0 else f"D-{n}"


def client_due(deadline: str | None) -> str | None:
    """의뢰인에게 알려 줄 자료 마감일: 법원 기한보다 며칠 앞 (config/office.json)."""
    if not deadline:
        return None
    lead = int(load_json("office.json").get("client_lead_days", 4))
    d = date.fromisoformat(deadline) - timedelta(days=lead)
    return max(d, date.today()).isoformat()


# ---------------- 상태 ----------------
def item_state(answer: str | None, docs: list[dict]) -> str:
    pending = [d for d in docs if d["status"] not in DONE_DOC_STATES]
    if (answer or "").strip():
        return "답변 작성됨" + (" (서류 대기)" if pending else "")
    return "자료 대기" if pending else "답변 필요"


def approval_blockers(items: list[dict], docs_by_item: dict[int, list[dict]]) -> list[str]:
    out = []
    for it in items:
        if not (it.get("answer") or "").strip():
            out.append(f"{it['no']}번 항목에 답변이 없습니다.")
        pend = [d["name"] for d in docs_by_item.get(it["id"], []) if d["status"] not in DONE_DOC_STATES]
        if pend:
            out.append(f"{it['no']}번 항목 서류가 아직 없습니다: {', '.join(pend)} ('받음' 또는 '생략'으로 처리)")
    if not items:
        out.append("항목이 없습니다.")
    return out


# ---------------- 의뢰인 안내문 ----------------
def client_message(debtor: str, deadline: str | None, docs: list[dict]) -> str:
    office = load_json("office.json").get("office_name") or "저희 사무소"
    due = client_due(deadline)
    want = [d for d in docs if d["status"] in ("요청 전", "요청함")]
    if not want:
        return ""
    when = ""
    if due:
        dd = date.fromisoformat(due)
        when = f" {dd.month}월 {dd.day}일까지"
    lines = [f"[{office}] {debtor}님, 법원에서 개인회생 서류 보완(보정) 요청이 왔습니다.",
             f"아래 서류를{when} 보내 주세요.", ""]
    seen = []
    for d in want:
        if d["name"] not in seen:
            seen.append(d["name"])
    lines += [f"{i}. {n}" for i, n in enumerate(seen, start=1)]
    lines += ["", "발급 방법을 모르시거나 준비가 어려운 서류는 미리 말씀해 주세요. 감사합니다."]
    return "\n".join(lines)


# ---------------- 보정서 ----------------
def attachments(items: list[dict], docs_by_item: dict[int, list[dict]]) -> list[str]:
    out = []
    for it in items:
        for d in docs_by_item.get(it["id"], []):
            if d["status"] == "받음" and d["name"] not in out:
                out.append(d["name"])
    return out


def reply_html(case: dict, order: dict, items: list[dict], docs_by_item: dict[int, list[dict]], draft: bool) -> str:
    e = html.escape
    od = order.get("order_date")
    od_txt = f"{od[:4]}. {int(od[5:7])}. {int(od[8:10])}.자 " if od else ""
    att = attachments(items, docs_by_item)
    parts = []
    last_heading = None
    for it in items:
        if it.get("heading") and it["heading"] != last_heading:
            parts.append(f"<p class='h'>{e(it['no'].split('-')[0])}. {e(it['heading'])}</p>")
            last_heading = it["heading"]
        parts.append(f"<p class='q'>{e(it['no'])}. 보정명령: {e(it['text'])}</p>")
        ans = (it.get("answer") or "").strip() or "(답변 미작성)"
        parts.append("".join(f"<p class='a'>{e(line)}</p>" for line in ans.splitlines() if line.strip()))
    att_html = "".join(f"<p>{i}. {e(a)}</p>" for i, a in enumerate(att, start=1)) or "<p>없음</p>"
    today = date.today()
    title = "보 정 서" + (" [초안]" if draft else "")
    return f"""<html><head><meta charset="utf-8"><title>{e(title)}</title>
<style>
body {{ font-family: '바탕', 'Batang', serif; font-size: 12pt; line-height: 1.7; }}
h1 {{ text-align: center; font-size: 20pt; letter-spacing: 6px; {'color:#c00000;' if draft else ''} }}
p {{ margin: 3px 0; }} p.h {{ font-weight: bold; margin-top: 12px; }}
p.q {{ margin-top: 8px; font-weight: bold; }} p.a {{ margin-left: 2em; }}
.right {{ text-align: right; }} .center {{ text-align: center; }}
</style></head><body>
<h1>{e(title)}</h1>
<p>사　　건　{e(case.get('case_no') or '')}　개인회생</p>
<p>채 무 자　{e(case['debtor_name'])}</p>
<p style="margin-top:14px">위 사건에 관하여 채무자는 귀원의 {e(od_txt)}보정명령({e(str(order.get('seq') or 1))}차)에 대하여 다음과 같이 보정합니다.</p>
<p class="center" style="margin:12px 0">다　　음</p>
{''.join(parts)}
<p class="h">첨부서류</p>
{att_html}
<p class="center" style="margin-top:24px">{today.year}. {today.month}. {today.day}.</p>
<p class="right">채무자의 대리인 ________________ (인)</p>
<p class="center" style="margin-top:18px">{e(case.get('court') or '')} 귀중</p>
</body></html>"""


def reply_text(case: dict, order: dict, items: list[dict], docs_by_item: dict[int, list[dict]]) -> str:
    """전자소송에 붙여 넣기 좋은 글 형태."""
    lines = []
    last_heading = None
    for it in items:
        if it.get("heading") and it["heading"] != last_heading:
            lines += ["", f"{it['no'].split('-')[0]}. {it['heading']}"]
            last_heading = it["heading"]
        lines += [f"{it['no']}. 보정명령: {it['text']}", (it.get("answer") or "(답변 미작성)").strip(), ""]
    att = attachments(items, docs_by_item)
    lines += ["첨부서류"] + [f"{i}. {a}" for i, a in enumerate(att, start=1)]
    return "\n".join(lines).strip()
