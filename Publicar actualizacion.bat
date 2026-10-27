@echo off
cd /d "%~dp0"
python publicar_actualizacion.py
if errorlevel 1 pause
