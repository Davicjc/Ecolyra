@echo off
rem Abre o Ecolyra (se ainda nao estiver instalado, roda o instalador)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" -SoAbrir
if errorlevel 1 pause
