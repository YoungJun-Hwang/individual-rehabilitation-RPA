"""SQLite 저장소. 파일 하나(data/cases.db)에 모든 사건이 들어갑니다. 백업은 이 파일을 복사하면 됩니다."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path(os.environ.get("CREDITOR_DB") or BASE_DIR / "data" / "cases.db")

STATUS_DRAFT = "작성 중"
STATUS_REVIEW = "검토 요청"
STATUS_APPROVED = "변호사 확인 완료"
STATUSES = [STATUS_DRAFT, STATUS_REVIEW, STATUS_APPROVED]

ROLE_STAFF = "사무원"
ROLE_LAWYER = "변호사"
ROLE_ADMIN = "관리자"
ROLES = [ROLE_STAFF, ROLE_LAWYER, ROLE_ADMIN]

DEBT_CERT_STATES = ["미요청", "요청함", "수령"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    pw_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY,
    case_no TEXT,
    debtor_name TEXT NOT NULL,
    debtor_birth TEXT,
    debtor_address TEXT,
    debtor_phone TEXT,
    court TEXT,
    lawyer_id INTEGER REFERENCES users(id),
    list_base_date TEXT,
    status TEXT NOT NULL DEFAULT '작성 중',
    approved_by INTEGER REFERENCES users(id),
    approved_at TEXT,
    memo TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questionnaire (
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    answer TEXT NOT NULL DEFAULT '모름',
    note TEXT,
    PRIMARY KEY (case_id, item_key)
);
CREATE TABLE IF NOT EXISTS creditors (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    biz_no TEXT,
    address TEXT,
    phone TEXT,
    fax TEXT,
    debt_cert_status TEXT NOT NULL DEFAULT '미요청',
    debt_cert_requested_at TEXT,
    debt_cert_received_at TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    creditor_id INTEGER NOT NULL REFERENCES creditors(id) ON DELETE CASCADE,
    category TEXT,
    cause TEXT,
    occurred_date TEXT,
    principal INTEGER,
    interest INTEGER,
    late_fee INTEGER,
    amount_base_date TEXT,
    transferred_from TEXT,
    evidence TEXT,
    note TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS credit_report_items (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    institution TEXT NOT NULL,
    product TEXT,
    amount INTEGER,
    note TEXT
);
CREATE TABLE IF NOT EXISTS debt_certs (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    creditor_id INTEGER NOT NULL REFERENCES creditors(id) ON DELETE CASCADE,
    source_name TEXT,
    base_date TEXT,
    principal INTEGER,
    interest INTEGER,
    late_fee INTEGER,
    other INTEGER,
    total INTEGER,
    contracts_json TEXT,
    ocr_text TEXT,
    applied INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL DEFAULT 1,
    order_date TEXT,
    served_date TEXT,
    days INTEGER,
    deadline TEXT,
    source_name TEXT,
    raw_text TEXT,
    status TEXT NOT NULL DEFAULT '작성 중',
    approved_by INTEGER REFERENCES users(id),
    approved_at TEXT,
    submitted_at TEXT,
    memo TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS correction_items (
    id INTEGER PRIMARY KEY,
    correction_id INTEGER NOT NULL REFERENCES corrections(id) ON DELETE CASCADE,
    no TEXT NOT NULL,
    heading TEXT,
    text TEXT NOT NULL,
    category TEXT,
    answer TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS correction_docs (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES correction_items(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '요청 전',
    requested_at TEXT,
    received_at TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS repayment_plans (
    case_id INTEGER PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
    inputs_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '작성 중',
    fingerprint TEXT,
    approved_by INTEGER REFERENCES users(id),
    approved_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS warning_acks (
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    warning_id TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    note TEXT,
    acked_at TEXT NOT NULL,
    PRIMARY KEY (case_id, warning_id)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    case_id INTEGER,
    user_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DB:
    def __init__(self, path: str | os.PathLike = DEFAULT_DB):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @contextmanager
    def tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def q(self, sql: str, args=()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        r = self.conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    # ---------- 감사 기록 ----------
    def log(self, case_id, user_id, action, detail=None):
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO audit_log(case_id,user_id,action,detail,at) VALUES(?,?,?,?,?)",
            (case_id, user_id, action, detail, now()),
        )

    def audit(self, case_id) -> list[dict]:
        return self.q(
            "SELECT a.at, u.name AS user, u.role, a.action, a.detail FROM audit_log a "
            "LEFT JOIN users u ON u.id=a.user_id WHERE a.case_id=? ORDER BY a.id DESC",
            (case_id,),
        )

    # ---------- 사용자 ----------
    @staticmethod
    def _hash(pw: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()

    def user_count(self) -> int:
        return self.one("SELECT COUNT(*) c FROM users")["c"]

    def add_user(self, name: str, role: str, pw: str) -> int:
        if role not in ROLES:
            raise ValueError("알 수 없는 역할")
        salt = secrets.token_hex(16)
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO users(name,role,pw_hash,salt) VALUES(?,?,?,?)",
                (name.strip(), role, self._hash(pw, salt), salt),
            )
            return cur.lastrowid

    def login(self, name: str, pw: str) -> dict | None:
        u = self.one("SELECT * FROM users WHERE name=? AND active=1", (name.strip(),))
        if u and secrets.compare_digest(u["pw_hash"], self._hash(pw, u["salt"])):
            return {"id": u["id"], "name": u["name"], "role": u["role"]}
        return None

    def users(self, role: str | None = None) -> list[dict]:
        if role:
            return self.q("SELECT id,name,role,active FROM users WHERE role=? ORDER BY name", (role,))
        return self.q("SELECT id,name,role,active FROM users ORDER BY role,name")

    # ---------- 사건 ----------
    def create_case(self, user_id, **f) -> int:
        t = now()
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO cases(case_no,debtor_name,debtor_birth,debtor_address,debtor_phone,court,"
                "lawyer_id,list_base_date,memo,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (f.get("case_no"), f["debtor_name"], f.get("debtor_birth"), f.get("debtor_address"),
                 f.get("debtor_phone"), f.get("court"), f.get("lawyer_id"), f.get("list_base_date"),
                 f.get("memo"), t, t),
            )
            cid = cur.lastrowid
            self.log(cid, user_id, "사건 등록", {"채무자": f["debtor_name"]})
            return cid

    def get_case(self, case_id) -> dict | None:
        return self.one(
            "SELECT c.*, l.name AS lawyer_name, a.name AS approved_by_name FROM cases c "
            "LEFT JOIN users l ON l.id=c.lawyer_id LEFT JOIN users a ON a.id=c.approved_by WHERE c.id=?",
            (case_id,),
        )

    def list_cases(self, keyword: str = "", status: str | None = None) -> list[dict]:
        sql = ("SELECT c.id,c.case_no,c.debtor_name,c.court,c.status,c.updated_at,l.name AS lawyer_name,"
               "(SELECT COUNT(*) FROM creditors WHERE case_id=c.id) AS creditor_count "
               "FROM cases c LEFT JOIN users l ON l.id=c.lawyer_id WHERE 1=1")
        args: list = []
        if keyword:
            sql += " AND (c.debtor_name LIKE ? OR c.case_no LIKE ?)"
            args += [f"%{keyword}%", f"%{keyword}%"]
        if status:
            sql += " AND c.status=?"
            args.append(status)
        return self.q(sql + " ORDER BY c.updated_at DESC", args)

    CASE_FIELDS = ("case_no", "debtor_name", "debtor_birth", "debtor_address", "debtor_phone",
                   "court", "lawyer_id", "list_base_date", "memo")

    def update_case(self, case_id, user_id, **f):
        cols = {k: v for k, v in f.items() if k in self.CASE_FIELDS}
        if not cols:
            return
        sets = ",".join(f"{k}=?" for k in cols)
        with self.tx() as c:
            c.execute(f"UPDATE cases SET {sets}, updated_at=? WHERE id=?", (*cols.values(), now(), case_id))
            self._invalidate_approval(case_id, user_id, "기본정보 수정")
            self.log(case_id, user_id, "기본정보 수정", list(cols))

    def delete_case(self, case_id, user_id):
        with self.tx() as c:
            c.execute("DELETE FROM cases WHERE id=?", (case_id,))
            self.log(case_id, user_id, "사건 삭제")

    def touch(self, case_id, user_id, reason):
        """사건 내용이 바뀌면 호출: 수정 시각 갱신, 승인 상태였다면 '작성 중'으로 되돌림."""
        self.conn.execute("UPDATE cases SET updated_at=? WHERE id=?", (now(), case_id))
        self._invalidate_approval(case_id, user_id, reason)

    def _invalidate_approval(self, case_id, user_id, reason):
        st = self.one("SELECT status FROM cases WHERE id=?", (case_id,))
        if st and st["status"] != STATUS_DRAFT:
            self.conn.execute(
                "UPDATE cases SET status=?, approved_by=NULL, approved_at=NULL WHERE id=?",
                (STATUS_DRAFT, case_id),
            )
            self.log(case_id, user_id, "상태 되돌림", f"{st['status']} → {STATUS_DRAFT} (사유: {reason})")

    def set_status(self, case_id, user: dict, status: str, open_warnings: int = 0):
        """상태 변경 규칙:
        - 확인 완료는 변호사 계정만, 미처리 경고가 0건일 때만
        - 검토 요청은 누구나(작성 중에서만)
        - 작성 중으로 되돌리기는 누구나
        """
        if status not in STATUSES:
            raise ValueError("알 수 없는 상태")
        case = self.get_case(case_id)
        if status == STATUS_APPROVED:
            if user["role"] != ROLE_LAWYER:
                raise PermissionError("변호사 계정만 '변호사 확인 완료'로 바꿀 수 있습니다.")
            if open_warnings > 0:
                raise PermissionError(f"처리하지 않은 경고가 {open_warnings}건 남아 있습니다.")
            if case["status"] != STATUS_REVIEW:
                raise PermissionError("'검토 요청' 상태의 사건만 확인 완료할 수 있습니다.")
        with self.tx() as c:
            if status == STATUS_APPROVED:
                c.execute("UPDATE cases SET status=?, approved_by=?, approved_at=? WHERE id=?",
                          (status, user["id"], now(), case_id))
            else:
                c.execute("UPDATE cases SET status=?, approved_by=NULL, approved_at=NULL WHERE id=?",
                          (status, case_id))
            self.log(case_id, user["id"], "상태 변경", f"{case['status']} → {status}")

    # ---------- 문진 ----------
    def get_questionnaire(self, case_id) -> dict[str, dict]:
        return {r["item_key"]: r for r in self.q("SELECT * FROM questionnaire WHERE case_id=?", (case_id,))}

    def save_questionnaire(self, case_id, user_id, answers: dict[str, tuple[str, str]]):
        with self.tx() as c:
            for key, (ans, note) in answers.items():
                c.execute(
                    "INSERT INTO questionnaire(case_id,item_key,answer,note) VALUES(?,?,?,?) "
                    "ON CONFLICT(case_id,item_key) DO UPDATE SET answer=excluded.answer, note=excluded.note",
                    (case_id, key, ans, note),
                )
            self.touch(case_id, user_id, "문진 수정")
            self.log(case_id, user_id, "문진 저장")

    # ---------- 채권자 / 채권 ----------
    CREDITOR_FIELDS = ("name", "biz_no", "address", "phone", "fax", "debt_cert_status",
                       "debt_cert_requested_at", "debt_cert_received_at", "sort_order")
    CLAIM_FIELDS = ("category", "cause", "occurred_date", "principal", "interest", "late_fee",
                    "amount_base_date", "transferred_from", "evidence", "note", "sort_order")

    def creditors(self, case_id) -> list[dict]:
        return self.q("SELECT * FROM creditors WHERE case_id=? ORDER BY sort_order, id", (case_id,))

    def claims(self, case_id) -> list[dict]:
        return self.q(
            "SELECT cl.*, cr.name AS creditor_name FROM claims cl JOIN creditors cr ON cr.id=cl.creditor_id "
            "WHERE cr.case_id=? ORDER BY cr.sort_order, cr.id, cl.sort_order, cl.id",
            (case_id,),
        )

    def add_creditor(self, case_id, user_id, **f) -> int:
        cols = {k: v for k, v in f.items() if k in self.CREDITOR_FIELDS}
        if "sort_order" not in cols:
            mx = self.one("SELECT COALESCE(MAX(sort_order),0) m FROM creditors WHERE case_id=?", (case_id,))
            cols["sort_order"] = mx["m"] + 1
        with self.tx() as c:
            cur = c.execute(
                f"INSERT INTO creditors(case_id,{','.join(cols)}) VALUES(?{',?' * len(cols)})",
                (case_id, *cols.values()),
            )
            self.touch(case_id, user_id, "채권자 추가")
            self.log(case_id, user_id, "채권자 추가", cols.get("name"))
            return cur.lastrowid

    def update_creditor(self, creditor_id, user_id, **f):
        cols = {k: v for k, v in f.items() if k in self.CREDITOR_FIELDS}
        if not cols:
            return
        cr = self.one("SELECT case_id,name FROM creditors WHERE id=?", (creditor_id,))
        with self.tx() as c:
            c.execute(f"UPDATE creditors SET {','.join(f'{k}=?' for k in cols)} WHERE id=?",
                      (*cols.values(), creditor_id))
            # 부채증명서 상태만 바뀐 경우는 목록 내용 변경이 아니므로 승인 유지
            if set(cols) - {"debt_cert_status", "debt_cert_requested_at", "debt_cert_received_at"}:
                self.touch(cr["case_id"], user_id, "채권자 수정")
            self.log(cr["case_id"], user_id, "채권자 수정", {"채권자": cr["name"], **cols})

    def delete_creditor(self, creditor_id, user_id):
        cr = self.one("SELECT case_id,name FROM creditors WHERE id=?", (creditor_id,))
        with self.tx() as c:
            c.execute("DELETE FROM creditors WHERE id=?", (creditor_id,))
            self.touch(cr["case_id"], user_id, "채권자 삭제")
            self.log(cr["case_id"], user_id, "채권자 삭제", cr["name"])

    def add_claim(self, creditor_id, user_id, **f) -> int:
        cols = {k: v for k, v in f.items() if k in self.CLAIM_FIELDS}
        cr = self.one("SELECT case_id,name FROM creditors WHERE id=?", (creditor_id,))
        with self.tx() as c:
            cur = c.execute(
                f"INSERT INTO claims(creditor_id{''.join(',' + k for k in cols)}) VALUES(?{',?' * len(cols)})",
                (creditor_id, *cols.values()),
            )
            self.touch(cr["case_id"], user_id, "채권 추가")
            self.log(cr["case_id"], user_id, "채권 추가", {"채권자": cr["name"], **cols})
            return cur.lastrowid

    def update_claim(self, claim_id, user_id, **f):
        cols = {k: v for k, v in f.items() if k in self.CLAIM_FIELDS}
        if not cols:
            return
        row = self.one("SELECT cr.case_id, cr.name FROM claims cl JOIN creditors cr ON cr.id=cl.creditor_id "
                       "WHERE cl.id=?", (claim_id,))
        with self.tx() as c:
            c.execute(f"UPDATE claims SET {','.join(f'{k}=?' for k in cols)} WHERE id=?", (*cols.values(), claim_id))
            self.touch(row["case_id"], user_id, "채권 수정")
            self.log(row["case_id"], user_id, "채권 수정", {"채권자": row["name"], "채권ID": claim_id, **cols})

    def delete_claim(self, claim_id, user_id):
        row = self.one("SELECT cr.case_id, cr.name FROM claims cl JOIN creditors cr ON cr.id=cl.creditor_id "
                       "WHERE cl.id=?", (claim_id,))
        with self.tx() as c:
            c.execute("DELETE FROM claims WHERE id=?", (claim_id,))
            self.touch(row["case_id"], user_id, "채권 삭제")
            self.log(row["case_id"], user_id, "채권 삭제", {"채권자": row["name"], "채권ID": claim_id})

    # ---------- 신용정보조회서 항목 (1단계는 직접 입력) ----------
    def credit_items(self, case_id) -> list[dict]:
        return self.q("SELECT * FROM credit_report_items WHERE case_id=? ORDER BY id", (case_id,))

    def add_credit_item(self, case_id, user_id, institution, product=None, amount=None, note=None) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO credit_report_items(case_id,institution,product,amount,note) VALUES(?,?,?,?,?)",
                (case_id, institution.strip(), product, amount, note),
            )
            self.touch(case_id, user_id, "신용정보 항목 추가")
            self.log(case_id, user_id, "신용정보 항목 추가", institution)
            return cur.lastrowid

    def import_credit_items(self, case_id, user_id, items: list[dict], source: str) -> tuple[int, int]:
        """PDF에서 고른 항목을 한꺼번에 저장. 같은 기관·같은 금액이 이미 있으면 건너뜀.
        돌려주는 값: (추가 건수, 건너뛴 건수)"""
        from .rules import normalize_name
        existing = {(normalize_name(r["institution"]), r["amount"]) for r in self.credit_items(case_id)}
        added = skipped = 0
        with self.tx() as c:
            for it in items:
                key = (normalize_name(it["institution"]), it.get("amount"))
                if not it["institution"].strip() or key in existing:
                    skipped += 1
                    continue
                existing.add(key)
                c.execute(
                    "INSERT INTO credit_report_items(case_id,institution,product,amount,note) VALUES(?,?,?,?,?)",
                    (case_id, it["institution"].strip(), it.get("product"), it.get("amount"), it.get("note")),
                )
                added += 1
            if added:
                self.touch(case_id, user_id, "신용정보 PDF 가져오기")
            self.log(case_id, user_id, "신용정보 PDF 가져오기",
                     {"파일": source, "추가": added, "건너뜀": skipped})
        return added, skipped

    def delete_credit_item(self, item_id, user_id):
        row = self.one("SELECT case_id, institution FROM credit_report_items WHERE id=?", (item_id,))
        with self.tx() as c:
            c.execute("DELETE FROM credit_report_items WHERE id=?", (item_id,))
            self.touch(row["case_id"], user_id, "신용정보 항목 삭제")
            self.log(row["case_id"], user_id, "신용정보 항목 삭제", row["institution"])

    # ---------- 부채증명서 (3단계) ----------
    def save_debt_cert(self, case_id, creditor_id, user_id, *, source_name, base_date, principal, interest,
                       late_fee, other, total, contracts, ocr_text, applied: bool) -> int:
        """증명서에서 읽은 값을 남기고, 그 채권자의 부채증명서 상태를 '수령'으로 바꿉니다."""
        cr = self.one("SELECT name, debt_cert_status, debt_cert_requested_at FROM creditors WHERE id=?", (creditor_id,))
        today = now()[:10]
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO debt_certs(case_id,creditor_id,source_name,base_date,principal,interest,late_fee,other,"
                "total,contracts_json,ocr_text,applied,user_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (case_id, creditor_id, source_name, base_date, principal, interest, late_fee, other, total,
                 json.dumps(contracts, ensure_ascii=False), ocr_text, int(applied), user_id, now()),
            )
            if cr["debt_cert_status"] != "수령":
                c.execute("UPDATE creditors SET debt_cert_status='수령', debt_cert_received_at=?, "
                          "debt_cert_requested_at=COALESCE(debt_cert_requested_at, ?) WHERE id=?",
                          (today, today, creditor_id))
            self.log(case_id, user_id, "부채증명서 등록",
                     {"채권자": cr["name"], "파일": source_name, "기준일": base_date, "합계": total,
                      "채권에 반영": bool(applied)})
            return cur.lastrowid

    def debt_certs(self, case_id) -> list[dict]:
        return self.q("SELECT d.*, cr.name AS creditor_name, u.name AS user_name FROM debt_certs d "
                      "JOIN creditors cr ON cr.id=d.creditor_id LEFT JOIN users u ON u.id=d.user_id "
                      "WHERE d.case_id=? ORDER BY d.id DESC", (case_id,))

    def latest_certs(self, case_id) -> dict[int, dict]:
        """채권자별 가장 최근 증명서."""
        out: dict[int, dict] = {}
        for r in self.q("SELECT * FROM debt_certs WHERE case_id=? ORDER BY id", (case_id,)):
            out[r["creditor_id"]] = r
        return out

    def delete_debt_cert(self, cert_id, user_id):
        r = self.one("SELECT case_id, source_name FROM debt_certs WHERE id=?", (cert_id,))
        with self.tx() as c:
            c.execute("DELETE FROM debt_certs WHERE id=?", (cert_id,))
            self.log(r["case_id"], user_id, "부채증명서 기록 삭제", r["source_name"])

    # ---------- 보정명령 (4단계) ----------
    def create_correction(self, case_id, user_id, *, order_date, served_date, days, deadline, source_name,
                          raw_text, items: list[dict]) -> int:
        """items: [{no, heading, text, category, answer, docs: [이름]}]"""
        seq = self.one("SELECT COALESCE(MAX(seq),0)+1 n FROM corrections WHERE case_id=?", (case_id,))["n"]
        t = now()
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO corrections(case_id,seq,order_date,served_date,days,deadline,source_name,raw_text,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (case_id, seq, order_date, served_date, days, deadline, source_name, raw_text, t, t))
            oid = cur.lastrowid
            for i, it in enumerate(items):
                iid = c.execute(
                    "INSERT INTO correction_items(correction_id,no,heading,text,category,answer,sort_order) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (oid, it["no"], it.get("heading") or None, it["text"], it.get("category"), it.get("answer") or "", i),
                ).lastrowid
                for d in it.get("docs") or []:
                    c.execute("INSERT INTO correction_docs(item_id,name) VALUES(?,?)", (iid, d))
            self.log(case_id, user_id, "보정명령 등록", {"차수": seq, "항목": len(items), "기한": deadline})
            return oid

    def corrections(self, case_id) -> list[dict]:
        return self.q("SELECT c.*, u.name AS approved_by_name FROM corrections c LEFT JOIN users u "
                      "ON u.id=c.approved_by WHERE c.case_id=? ORDER BY c.seq DESC", (case_id,))

    def get_correction(self, oid) -> dict | None:
        return self.one("SELECT c.*, u.name AS approved_by_name FROM corrections c LEFT JOIN users u "
                        "ON u.id=c.approved_by WHERE c.id=?", (oid,))

    def open_corrections(self) -> list[dict]:
        """제출하지 않은 보정명령 전체 (기한 알림용)."""
        return self.q("SELECT c.id, c.case_id, c.seq, c.deadline, c.status, k.debtor_name, k.case_no "
                      "FROM corrections c JOIN cases k ON k.id=c.case_id WHERE c.status != '제출 완료' "
                      "ORDER BY c.deadline IS NULL, c.deadline")

    def correction_items(self, oid) -> list[dict]:
        return self.q("SELECT * FROM correction_items WHERE correction_id=? ORDER BY sort_order, id", (oid,))

    def correction_docs(self, oid) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for r in self.q("SELECT d.* FROM correction_docs d JOIN correction_items i ON i.id=d.item_id "
                        "WHERE i.correction_id=? ORDER BY d.id", (oid,)):
            out.setdefault(r["item_id"], []).append(r)
        return out

    def _corr_changed(self, oid, user_id, reason):
        o = self.one("SELECT case_id, status FROM corrections WHERE id=?", (oid,))
        self.conn.execute("UPDATE corrections SET updated_at=? WHERE id=?", (now(), oid))
        if o["status"] in ("검토 요청", "변호사 승인"):
            self.conn.execute("UPDATE corrections SET status='작성 중', approved_by=NULL, approved_at=NULL WHERE id=?",
                              (oid,))
            self.log(o["case_id"], user_id, "보정 상태 되돌림", f"{o['status']} → 작성 중 (사유: {reason})")
        return o["case_id"]

    def update_correction(self, oid, user_id, **f):
        cols = {k: v for k, v in f.items() if k in ("order_date", "served_date", "days", "deadline", "memo")}
        if not cols:
            return
        o = self.one("SELECT case_id FROM corrections WHERE id=?", (oid,))
        with self.tx() as c:
            c.execute(f"UPDATE corrections SET {','.join(f'{k}=?' for k in cols)}, updated_at=? WHERE id=?",
                      (*cols.values(), now(), oid))
            self.log(o["case_id"], user_id, "보정명령 정보 수정", cols)

    def delete_correction(self, oid, user_id):
        o = self.one("SELECT case_id, seq FROM corrections WHERE id=?", (oid,))
        with self.tx() as c:
            c.execute("DELETE FROM corrections WHERE id=?", (oid,))
            self.log(o["case_id"], user_id, "보정명령 삭제", f"{o['seq']}차")

    def update_item(self, item_id, user_id, **f):
        cols = {k: v for k, v in f.items() if k in ("no", "heading", "text", "category", "answer")}
        it = self.one("SELECT correction_id, no FROM correction_items WHERE id=?", (item_id,))
        with self.tx() as c:
            c.execute(f"UPDATE correction_items SET {','.join(f'{k}=?' for k in cols)} WHERE id=?",
                      (*cols.values(), item_id))
            cid = self._corr_changed(it["correction_id"], user_id, f"{it['no']}번 항목 수정")
            self.log(cid, user_id, "보정 항목 수정", {"항목": it["no"], **{k: (v[:60] if isinstance(v, str) else v)
                                                                          for k, v in cols.items()}})

    def add_item(self, oid, user_id, no, text, category=None, heading=None) -> int:
        mx = self.one("SELECT COALESCE(MAX(sort_order),0)+1 n FROM correction_items WHERE correction_id=?", (oid,))["n"]
        with self.tx() as c:
            iid = c.execute("INSERT INTO correction_items(correction_id,no,heading,text,category,answer,sort_order) "
                            "VALUES(?,?,?,?,?,'',?)", (oid, no, heading, text, category, mx)).lastrowid
            cid = self._corr_changed(oid, user_id, "항목 추가")
            self.log(cid, user_id, "보정 항목 추가", no)
            return iid

    def delete_item(self, item_id, user_id):
        it = self.one("SELECT correction_id, no FROM correction_items WHERE id=?", (item_id,))
        with self.tx() as c:
            c.execute("DELETE FROM correction_items WHERE id=?", (item_id,))
            cid = self._corr_changed(it["correction_id"], user_id, "항목 삭제")
            self.log(cid, user_id, "보정 항목 삭제", it["no"])

    def add_doc(self, item_id, user_id, name) -> int:
        it = self.one("SELECT correction_id FROM correction_items WHERE id=?", (item_id,))
        with self.tx() as c:
            did = c.execute("INSERT INTO correction_docs(item_id,name) VALUES(?,?)", (item_id, name.strip())).lastrowid
            cid = self._corr_changed(it["correction_id"], user_id, "서류 추가")
            self.log(cid, user_id, "보정 서류 추가", name)
            return did

    def update_doc(self, doc_id, user_id, **f):
        cols = {k: v for k, v in f.items() if k in ("name", "status", "requested_at", "received_at", "note")}
        d = self.one("SELECT d.name, i.correction_id FROM correction_docs d JOIN correction_items i ON i.id=d.item_id "
                     "WHERE d.id=?", (doc_id,))
        with self.tx() as c:
            c.execute(f"UPDATE correction_docs SET {','.join(f'{k}=?' for k in cols)} WHERE id=?",
                      (*cols.values(), doc_id))
            cid = self._corr_changed(d["correction_id"], user_id, "서류 상태 변경")
            self.log(cid, user_id, "보정 서류 변경", {"서류": d["name"], **cols})

    def delete_doc(self, doc_id, user_id):
        d = self.one("SELECT d.name, i.correction_id FROM correction_docs d JOIN correction_items i ON i.id=d.item_id "
                     "WHERE d.id=?", (doc_id,))
        with self.tx() as c:
            c.execute("DELETE FROM correction_docs WHERE id=?", (doc_id,))
            cid = self._corr_changed(d["correction_id"], user_id, "서류 삭제")
            self.log(cid, user_id, "보정 서류 삭제", d["name"])

    def set_correction_status(self, oid, user: dict, status: str, blockers: list[str] | None = None):
        """작성 중 → 검토 요청(누구나) → 변호사 승인(변호사만, 막는 사항 없을 때) → 제출 완료(승인 뒤)."""
        o = self.get_correction(oid)
        if status == "변호사 승인":
            if user["role"] != ROLE_LAWYER:
                raise PermissionError("변호사 계정만 보정서 답변을 승인할 수 있습니다.")
            if o["status"] != "검토 요청":
                raise PermissionError("'검토 요청' 상태에서만 승인할 수 있습니다.")
            if blockers:
                raise PermissionError("승인할 수 없습니다: " + " / ".join(blockers))
        if status == "제출 완료" and o["status"] != "변호사 승인":
            raise PermissionError("변호사 승인 뒤에만 제출 완료로 바꿀 수 있습니다.")
        if status == "검토 요청" and o["status"] != "작성 중":
            raise PermissionError("'작성 중' 상태에서만 검토 요청할 수 있습니다.")
        with self.tx() as c:
            if status == "변호사 승인":
                c.execute("UPDATE corrections SET status=?, approved_by=?, approved_at=? WHERE id=?",
                          (status, user["id"], now(), oid))
            elif status == "제출 완료":
                c.execute("UPDATE corrections SET status=?, submitted_at=? WHERE id=?", (status, now(), oid))
            else:
                c.execute("UPDATE corrections SET status=?, approved_by=NULL, approved_at=NULL, submitted_at=NULL "
                          "WHERE id=?", (status, oid))
            self.log(o["case_id"], user["id"], "보정 상태 변경", f"{o['seq']}차: {o['status']} → {status}")

    # ---------- 변제계획안 (5단계) ----------
    def get_plan(self, case_id) -> dict | None:
        return self.one("SELECT p.*, u.name AS approved_by_name FROM repayment_plans p LEFT JOIN users u "
                        "ON u.id=p.approved_by WHERE p.case_id=?", (case_id,))

    def save_plan_inputs(self, case_id, user_id, inputs_json: str):
        cur = self.get_plan(case_id)
        if cur and cur["inputs_json"] == inputs_json:
            return
        with self.tx() as c:
            c.execute("INSERT INTO repayment_plans(case_id,inputs_json,updated_at) VALUES(?,?,?) "
                      "ON CONFLICT(case_id) DO UPDATE SET inputs_json=excluded.inputs_json, "
                      "updated_at=excluded.updated_at", (case_id, inputs_json, now()))
            if cur and cur["status"] != "작성 중":
                c.execute("UPDATE repayment_plans SET status='작성 중', approved_by=NULL, approved_at=NULL "
                          "WHERE case_id=?", (case_id,))
                self.log(case_id, user_id, "변제계획안 상태 되돌림", f"{cur['status']} → 작성 중 (사유: 입력값 수정)")
            self.log(case_id, user_id, "변제계획안 입력 저장")

    def plan_list_changed(self, case_id, user_id, fingerprint: str) -> bool:
        """검토 요청·확정 뒤 채권자목록이 바뀌었으면 '작성 중'으로 되돌리고 True."""
        p = self.get_plan(case_id)
        if not p or p["status"] == "작성 중" or not p["fingerprint"] or p["fingerprint"] == fingerprint:
            return False
        with self.tx() as c:
            c.execute("UPDATE repayment_plans SET status='작성 중', approved_by=NULL, approved_at=NULL "
                      "WHERE case_id=?", (case_id,))
            self.log(case_id, user_id, "변제계획안 상태 되돌림", f"{p['status']} → 작성 중 (사유: 채권자목록 변경)")
        return True

    def set_plan_status(self, case_id, user: dict, status: str, fingerprint: str, blockers: list[str] | None = None):
        p = self.get_plan(case_id)
        if not p:
            raise PermissionError("먼저 변제계획안 입력값을 저장하세요.")
        if status == "검토 요청" and p["status"] != "작성 중":
            raise PermissionError("'작성 중' 상태에서만 검토 요청할 수 있습니다.")
        if status == "변호사 확정":
            if user["role"] != ROLE_LAWYER:
                raise PermissionError("변호사 계정만 변제계획안을 확정할 수 있습니다.")
            if p["status"] != "검토 요청":
                raise PermissionError("'검토 요청' 상태에서만 확정할 수 있습니다.")
            if blockers:
                raise PermissionError("확정할 수 없습니다: " + " / ".join(blockers))
        with self.tx() as c:
            if status == "변호사 확정":
                c.execute("UPDATE repayment_plans SET status=?, approved_by=?, approved_at=?, fingerprint=? "
                          "WHERE case_id=?", (status, user["id"], now(), fingerprint, case_id))
            else:
                c.execute("UPDATE repayment_plans SET status=?, approved_by=NULL, approved_at=NULL, fingerprint=? "
                          "WHERE case_id=?", (status, fingerprint if status != "작성 중" else None, case_id))
            self.log(case_id, user["id"], "변제계획안 상태 변경", f"{p['status']} → {status}")

    # ---------- 경고 확인 기록 ----------
    def acks(self, case_id) -> dict[str, dict]:
        rows = self.q("SELECT w.*, u.name AS user_name, u.role FROM warning_acks w JOIN users u ON u.id=w.user_id "
                      "WHERE w.case_id=?", (case_id,))
        return {r["warning_id"]: r for r in rows}

    def ack_warning(self, case_id, user_id, warning_id, note):
        if not (note or "").strip():
            raise ValueError("확인 사유를 적어 주세요.")
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO warning_acks(case_id,warning_id,user_id,note,acked_at) VALUES(?,?,?,?,?)",
                (case_id, warning_id, user_id, note.strip(), now()),
            )
            self.log(case_id, user_id, "경고 확인함", {"경고": warning_id, "사유": note.strip()})

    def unack_warning(self, case_id, user_id, warning_id):
        with self.tx() as c:
            c.execute("DELETE FROM warning_acks WHERE case_id=? AND warning_id=?", (case_id, warning_id))
            self.log(case_id, user_id, "경고 확인 취소", warning_id)
