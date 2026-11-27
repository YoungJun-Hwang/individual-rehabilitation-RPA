"""모든 비밀번호를 잊었을 때 쓰는 관리 도구 (비밀번호_초기화.bat이 실행).

이 PC에서만 실행할 수 있고, 고른 계정의 비밀번호를 임시 비밀번호로 바꿉니다.
그 계정은 다음 로그인 때 새 비밀번호로 바꿔야 하며, 초기화한 사실은 변경 기록에 남습니다.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import DB, DEFAULT_DB  # noqa: E402


def main() -> int:
    if not Path(DEFAULT_DB).exists():
        print("자료 파일(data/cases.db)이 없습니다. 프로그램을 한 번도 실행하지 않았다면 실행.bat부터 실행하세요.")
        return 1
    db = DB()
    users = db.users()
    if not users:
        print("등록된 계정이 없습니다. 실행.bat으로 프로그램을 열면 계정을 만들 수 있습니다.")
        return 1
    print("비밀번호를 초기화할 계정을 고르세요.\n")
    for i, u in enumerate(users, start=1):
        print(f"  {i}. {u['name']} ({u['role']}){'' if u['active'] else ' - 사용 안 함'}")
    choice = input("\n번호 (그만두려면 Enter): ").strip()
    if not choice:
        print("아무것도 바꾸지 않았습니다.")
        return 0
    if not choice.isdigit() or not 1 <= int(choice) <= len(users):
        print("번호가 맞지 않습니다. 아무것도 바꾸지 않았습니다.")
        return 1
    u = users[int(choice) - 1]
    pw = getpass.getpass(f"{u['name']}의 임시 비밀번호 ({DB.MIN_PW}자 이상, 입력해도 화면에 안 보임): ")
    pw2 = getpass.getpass("한 번 더: ")
    if pw != pw2:
        print("두 번 입력한 비밀번호가 다릅니다. 아무것도 바꾸지 않았습니다.")
        return 1
    try:
        db.recover_password(u["id"], pw)
    except ValueError as e:
        print(f"{e} 아무것도 바꾸지 않았습니다.")
        return 1
    print(f"\n{u['name']}의 비밀번호를 임시 비밀번호로 바꿨습니다. 로그인하면 새 비밀번호로 바꾸라는 화면이 나옵니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
