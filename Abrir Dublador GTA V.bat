@echo off
cd /d "%~dp0"
set PY=py -3.11
%PY% -c "import sys" 2>nul || set PY=python
%PY% -c "import numpy, scipy, soundfile, sounddevice, cryptography, numba" 2>nul || (
  echo Instalando bibliotecas necessarias...
  %PY% -m pip install numpy scipy soundfile sounddevice cryptography numba
)
%PY% dublador_v.py
if errorlevel 1 pause
