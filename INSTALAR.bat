@echo off
rem Ecolyra - instalador (o trabalho de verdade fica no instalar.ps1)
title Ecolyra - instalacao
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1" %*
if errorlevel 1 pause
