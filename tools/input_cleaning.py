"""
Limpeza das entradas que o LLM envia às ferramentas.

Na prática o Gemini às vezes "vaza" texto do formato ReAct para dentro do
Action Input (ex.: 'Lisboa | conforto | 7 | BRL Obervation:') ou coloca aspas
e crases em volta. Limpar aqui evita erros bobos nas ferramentas.
"""

import re

# Palavras do formato ReAct (inclusive com erros de digitação comuns) que não fazem parte da entrada.
REACT_LEAK_PATTERN = re.compile(r"\s*\b(Ob[a-z]*ation|Thought|Action|Final Answer)\s*:.*$", re.IGNORECASE)
CURRENCY_CODE_PATTERN = re.compile(r"\b([A-Za-z]{3})\b")


def clean_tool_input(raw_input: str) -> str:
    """Mantém só a primeira linha, sem aspas/crases e sem restos do formato ReAct."""
    first_line = raw_input.strip().splitlines()[0] if raw_input.strip() else ""
    first_line = REACT_LEAK_PATTERN.sub("", first_line)
    return first_line.strip().strip("`\"'").strip()


def extract_currency_code(text: str, default: str) -> str:
    """Primeiro código de 3 letras do texto (ex.: 'BRL Obervation' -> 'BRL')."""
    match = CURRENCY_CODE_PATTERN.search(text)
    return match.group(1).upper() if match else default
