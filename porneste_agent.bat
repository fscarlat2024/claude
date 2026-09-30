@echo off
rem Varianta care ruleaza continuu si asteapta extrase (pentru o singura rulare foloseste Genereaza-Factura.ps1)
cd /d "%~dp0"
python -m pip install -q -r requirements.txt
python agent_facturi.py
pause
