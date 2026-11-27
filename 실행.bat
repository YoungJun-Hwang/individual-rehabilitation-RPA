@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 개인회생 업무 프로그램

rem ---- 1. Python 가상 환경 (.venv) 만들기: 처음 한 번 ----
if not exist ".venv\Scripts\python.exe" (
  echo [처음 실행] 프로그램 환경을 만듭니다. 잠시 기다려 주세요...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Python을 찾을 수 없습니다. README의 "1. Python 설치"를 따라 설치한 뒤 다시 실행하세요.
  echo 설치 첫 화면에서 "Add python.exe to PATH"를 꼭 체크해야 합니다.
  pause
  exit /b 1
)

rem ---- 2. 필요한 프로그램 설치: 처음 실행과 업데이트로 requirements.txt가 바뀌었을 때만 ----
fc /b requirements.txt ".venv\installed-requirements.txt" >nul 2>&1
if errorlevel 1 (
  echo 필요한 프로그램을 설치합니다. 인터넷 연결이 필요하고 몇 분 걸릴 수 있습니다...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요. README의 "문제 해결"을 참고하세요.
    pause
    exit /b 1
  )
  copy /y requirements.txt ".venv\installed-requirements.txt" >nul
)

rem ---- 3. Streamlit이 처음에 이메일을 묻지 않게 함 ----
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  mkdir "%USERPROFILE%\.streamlit" 2>nul
  > "%USERPROFILE%\.streamlit\credentials.toml" echo [general]
  >> "%USERPROFILE%\.streamlit\credentials.toml" echo email = ""
)

rem ---- 4. 실행 ----
echo.
echo 프로그램을 시작합니다. 잠시 뒤 브라우저가 열립니다.
echo 브라우저가 안 열리면 주소창에 http://localhost:8501 을 입력하세요.
echo 끝낼 때는 이 검은 창을 닫으세요.
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
pause
