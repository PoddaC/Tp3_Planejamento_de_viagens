"""
Ferramenta 2: conversor de moedas.

Usa a AwesomeAPI (economia.awesomeapi.com.br), gratuita e sem chave.
A API cota pares contra o real (ex.: USD-BRL). Para converter entre duas moedas
estrangeiras (ex.: EUR -> USD), usamos o real como ponte:
    taxa(EUR->USD) = taxa(EUR->BRL) / taxa(USD->BRL)
"""

import re

from langchain_core.tools import Tool

from tools.http_client import ExternalServiceError, get_json
from tools.input_cleaning import clean_tool_input

QUOTE_URL = "https://economia.awesomeapi.com.br/json/last/{pair}"
BRIDGE_CURRENCY = "BRL"

# Aceita: "100 USD para BRL", "100,50 EUR -> BRL", "1500 usd brl", "100 USD em BRL"
CONVERSION_PATTERN = re.compile(
    r"^\s*(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<source>[A-Za-z]{3})\s*(?:para|em|to|->|=>)?\s*(?P<target>[A-Za-z]{3})\s*$"
)


def _parse_conversion_input(raw_input: str) -> tuple[float, str, str]:
    """Extrai (valor, moeda de origem, moeda de destino) da entrada em texto."""
    match = CONVERSION_PATTERN.match(clean_tool_input(raw_input))
    if not match:
        raise ValueError(
            f"entrada '{raw_input}' fora do formato. Use: '<valor> <MOEDA_ORIGEM> para <MOEDA_DESTINO>', "
            "ex.: '100 USD para BRL'"
        )
    amount = float(match["amount"].replace(",", "."))
    return amount, match["source"].upper(), match["target"].upper()


def _rate_to_brl(currency: str) -> float:
    """Retorna quantos reais vale 1 unidade da moeda informada."""
    if currency == BRIDGE_CURRENCY:
        return 1.0
    pair = f"{currency}-{BRIDGE_CURRENCY}"
    try:
        data = get_json(QUOTE_URL.format(pair=pair))
    except ExternalServiceError as exc:
        # A AwesomeAPI responde 404 para moedas desconhecidas.
        if "404" in str(exc):
            raise ValueError(f"moeda '{currency}' não suportada") from exc
        raise
    try:
        return float(data[pair.replace("-", "")]["bid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"cotação de '{currency}' indisponível") from exc


def rate_between(source: str, target: str) -> float:
    """
    Quantas unidades de `target` vale 1 unidade de `source` (cotação atual).
    Também usada pela ferramenta estimativa_de_custos.

    Raises:
        ValueError: moeda não suportada.
        ExternalServiceError: falha ao consultar a API.
    """
    source, target = source.upper(), target.upper()
    if source == target:
        return 1.0
    return _rate_to_brl(source) / _rate_to_brl(target)


def convert_currency(raw_input: str) -> str:
    """Função executada pela tool. Devolve o resultado da conversão em texto."""
    try:
        amount, source, target = _parse_conversion_input(raw_input)
        if source == target:
            return f"{amount:,.2f} {source} = {amount:,.2f} {target} (mesma moeda)."
        rate = rate_between(source, target)
    except ValueError as exc:
        return f"Erro: {exc}."
    except ExternalServiceError as exc:
        return f"Erro ao consultar a cotação: {exc}. Tente novamente mais tarde."

    converted = amount * rate
    return (
        f"{amount:,.2f} {source} = {converted:,.2f} {target} "
        f"(cotação atual: 1 {source} = {rate:.4f} {target})."
    )


currency_tool = Tool(
    name="conversor_de_moedas",
    func=convert_currency,
    description=(
        "Converte um valor entre moedas usando a cotação atual do mercado. "
        "Use códigos de 3 letras (BRL, USD, EUR, ARS, GBP, JPY, CLP...). "
        "Entrada: '<valor> <MOEDA_ORIGEM> para <MOEDA_DESTINO>'. Exemplo: '250 EUR para BRL'."
    ),
)
