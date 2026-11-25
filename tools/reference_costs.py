"""
Ferramenta 4: estimativa de custos no destino, feita pelo LLM (Gemini).

O modelo estima, para a cidade, o mês e o padrão da viagem, quanto custam em
média a hospedagem, a alimentação, o transporte local e os passeios, e se o mês
é alta temporada. Os valores vêm em USD (onde o conhecimento do modelo é mais
consistente) e são convertidos para a moeda pedida pela cotação REAL do dia.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import Tool
from pydantic import BaseModel, Field

from destinations import QUALITY_LEVELS, normalize_quality
from tools.currency import rate_between
from tools.http_client import ExternalServiceError
from tools.input_cleaning import clean_tool_input, extract_currency_code
from tools.llm_estimator import MONTH_NAMES, QUALITY_DESCRIPTIONS, EstimationError, make_cached_estimator

MAX_REASONABLE_USD = 5000  # filtro contra números absurdos


class CostEstimate(BaseModel):
    """Formato que o LLM deve preencher."""

    hospedagem_noite_usd: float = Field(description="preço médio de UM quarto duplo por noite, em USD")
    alimentacao_pessoa_dia_usd: float = Field(description="gasto médio com refeições por pessoa por dia, em USD")
    transporte_local_pessoa_dia_usd: float = Field(description="transporte dentro da cidade por pessoa por dia, em USD")
    passeios_pessoa_dia_usd: float = Field(description="passeios e ingressos por pessoa por dia, em USD")
    alta_temporada: bool = Field(description="true se o mês é alta temporada no destino")
    justificativa: str = Field(description="uma frase explicando a estimativa (ex.: temporada, perfil da cidade)")


COST_LABELS = {
    "hospedagem_noite_usd": "Hospedagem (quarto duplo, por noite)",
    "alimentacao_pessoa_dia_usd": "Alimentação (por pessoa, por dia)",
    "transporte_local_pessoa_dia_usd": "Transporte local (por pessoa, por dia)",
    "passeios_pessoa_dia_usd": "Passeios e ingressos (por pessoa, por dia)",
}

PROMPT_TEMPLATE = (
    "Você é um especialista em custos de viagem. Estime valores MÉDIOS e realistas, em dólares americanos (USD), "
    "para um turista em {place} no mês de {month_name}, no padrão {quality}. "
    "Leve em conta se esse mês é alta temporada no destino (preços de hospedagem sobem). "
    "Hospedagem = um quarto duplo por noite. Alimentação, transporte local e passeios = por pessoa por dia."
)


def _parse_input(raw_input: str) -> tuple[str, str, int, str]:
    """'Lisboa, Portugal | conforto | 7 | BRL' -> (local, qualidade, mês, moeda)."""
    parts = [part.strip() for part in clean_tool_input(raw_input).split("|")]
    if len(parts) < 3 or not parts[0]:
        raise ValueError("entrada incompleta. Use: 'Cidade, País | qualidade | mês (1-12) | MOEDA (opcional)'")
    place, quality, month_text = parts[:3]
    currency = extract_currency_code(parts[3], "BRL") if len(parts) > 3 else "BRL"
    try:
        month = int(month_text)
    except ValueError as exc:
        raise ValueError(f"mês inválido: {month_text!r} (use um número de 1 a 12)") from exc
    if not 1 <= month <= 12:
        raise ValueError("o mês deve estar entre 1 e 12")
    return place, normalize_quality(quality), month, currency


def make_reference_costs_tool(llm: BaseChatModel) -> Tool:
    """Cria a ferramenta ligada ao LLM do agente."""
    estimate = make_cached_estimator(llm, CostEstimate)

    def get_reference_costs(raw_input: str) -> str:
        try:
            place, quality, month, currency = _parse_input(raw_input)
            result: CostEstimate = estimate(
                PROMPT_TEMPLATE.format(place=place, month_name=MONTH_NAMES[month - 1], quality=QUALITY_DESCRIPTIONS[quality])
            )
        except (ValueError, KeyError) as exc:
            return f"Erro: {exc.args[0] if exc.args else exc}."
        except EstimationError as exc:
            return f"Erro: não foi possível estimar os custos agora ({exc})."

        values = {key: getattr(result, key) for key in COST_LABELS}
        if any(not 0 < value < MAX_REASONABLE_USD for value in values.values()):
            return "Erro: o modelo devolveu valores fora de uma faixa razoável. Tente novamente."

        note = ""
        try:
            rate = rate_between("USD", currency)
        except (ValueError, ExternalServiceError):
            rate, currency = 1.0, "USD"
            note = "\n(Não foi possível converter agora; valores mantidos em USD.)"

        season = "ALTA temporada" if result.alta_temporada else "temporada normal"
        lines = [f"Custos estimados pelo modelo de linguagem — {place}, padrão {QUALITY_LEVELS[quality]}, mês {month} ({season}), em {currency}:"]
        lines += [f"- {label}: {values[key] * rate:,.2f} {currency}" for key, label in COST_LABELS.items()]
        lines += [
            f"Justificativa do modelo: {result.justificativa}",
            "Use estes valores nos campos hospedagem_diaria, alimentacao_diaria_pessoa, transporte_diario_pessoa e "
            "passeios_diario_pessoa da calculadora_orcamento. São estimativas do modelo (conversão pela cotação real).",
        ]
        return "\n".join(lines) + note

    return Tool(
        name="estimativa_de_custos",
        func=get_reference_costs,
        description=(
            "Pede ao modelo de linguagem uma estimativa dos custos médios de um turista numa CIDADE: hospedagem por "
            "noite, alimentação, transporte local e passeios por pessoa/dia, conforme o padrão (economico, conforto "
            "ou luxo) e o mês, indicando se é alta temporada. Converte para a moeda pedida pela cotação real. "
            "Use antes da calculadora_orcamento quando o usuário não informar os custos. "
            "Entrada: 'Cidade, País | qualidade | mês (1-12) | MOEDA'. Exemplo: 'Lisboa, Portugal | conforto | 7 | BRL'."
        ),
    )
