@echo off
setlocal

set "URL=http://192.168.1.26:5000/transparent-pass"
set "BODY={\"user\":\"User\",\"text\":\"fay \u7684\u5de5\u4f5c\u5df2\u7ecf\u5b8c\u6210\"}"

curl.exe -s -X POST "%URL%" ^
  -H "Content-Type: application/json" ^
  --data-raw "%BODY%" >nul

if errorlevel 1 (
  echo [done.bat] Request failed.
  exit /b 1
)

echo [done.bat] Notification sent.
exit /b 0
