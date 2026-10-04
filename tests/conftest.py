"""Configuração comum dos testes: coloca os pacotes dos serviços no PYTHONPATH."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("shared", "api", "worker", "notifier"):
    sys.path.insert(0, str(ROOT / sub))
