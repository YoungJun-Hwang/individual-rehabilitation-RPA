@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 채권자목록 정리 프로그램

if not exist ".venv\Scripts\python.exe" (
  echo [처음 실행] 프로그램 환경을 준비합니다. 몇 분 걸릴 수 있습니다...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo Python을 찾을 수 없습니다. https://www.python.org 에서 Python 3.11 이상을 설치하고
    echo 설치 화면에서 "Add python.exe to PATH"를 꼭 체크하세요.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo 설치 실패 & pause & exit /b 1)
)

echo 프로그램을 시작합니다. 브라우저가 열리면 사용하세요. 끝낼 때는 이 창을 닫으세요.
".venv\Scripts\python.exe" -m streamlit run app.py
pause
