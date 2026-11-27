@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 비밀번호 초기화
echo 모든 비밀번호를 잊었을 때 쓰는 도구입니다.
echo 다른 변호사·관리자 계정으로 로그인할 수 있다면 프로그램의 "계정 관리"에서 초기화하세요.
echo.
if not exist ".venv\Scripts\python.exe" (
  echo 먼저 실행.bat으로 프로그램을 한 번 실행하세요.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\reset_password.py
pause
