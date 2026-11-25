"""Garante que os módulos do projeto sejam importáveis nos testes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
