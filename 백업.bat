@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist backup mkdir backup
for /f "tokens=1-3 delims=-/. " %%a in ("%date%") do set D=%%a%%b%%c
copy "data\cases.db" "backup\cases_%D%_%time:~0,2%%time:~3,2%.db"
echo 백업했습니다: backup 폴더
pause
