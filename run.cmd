@echo off
rem Запуск PDF Мультиподпись
setlocal
cd /d "%~dp0"
set PYTHONPATH=%~dp0;%~dp0vendor;%~dp0vendor\win32;%~dp0vendor\win32\lib;%~dp0vendor\pywin32_system32
set PATH=%~dp0vendor\pywin32_system32;%PATH%
python -m pdfsign.app %*
endlocal
