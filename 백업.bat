@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 자료 백업

if not exist "data\cases.db" (
  echo 백업할 자료 파일이 없습니다: data\cases.db
  echo 프로그램을 한 번도 쓰지 않았거나, 이 폴더가 실제로 쓰는 프로그램 폴더가 맞는지 확인하세요.
  pause
  exit /b 1
)
if not exist backup mkdir backup

rem 날짜·시간 표기는 Windows 언어 설정과 관계없이 PowerShell로 만듦 (예: 20261127_093015)
set STAMP=
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%t
if not defined STAMP set STAMP=%RANDOM%%RANDOM%

copy /y "data\cases.db" "backup\cases_%STAMP%.db" >nul
if errorlevel 1 (
  echo 백업에 실패했습니다. 프로그램 검은 창을 닫은 뒤 다시 실행하세요.
  pause
  exit /b 1
)
echo 백업했습니다: backup\cases_%STAMP%.db
echo 업데이트할 때는 이 backup 폴더도 새 폴더로 옮기세요.
pause
