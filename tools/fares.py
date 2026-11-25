"""
Ferramenta 5: estimativa de passagens (ida e volta), feita pelo LLM (Gemini).

O modelo escolhe o meio de transporte mais comum para o trecho (avião ou
ônibus) e estima o preço médio de ida e volta por pessoa, considerando o mês
(alta temporada) e o padrão da viagem. O código só valida e multiplica pelo
número de pessoas.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import Tool
from pydantic import BaseModel, Field

from destinations import normalize_quality
from tools.input_cleaning import clean_tool_input
from tools.llm_estimator import MONTH_NAMES, QUALITY_DESCRIPTIONS, EstimationError, make_cached_estimator

MAX_REASONABLE_BRL = 100_000  # filtro contra números absurdos (por pessoa)


class FareEstimate(BaseModel):
    """Formato que o LLM deve preencher."""

    meio_de_transporte: str = Field(description="'avião' ou 'ônibus': o mais usado por turistas nesse trecho")
    classe: str = Field(description="classe ou tipo de serviço considerado (ex.: econômica, executiva, semi-leito)")
    preco_ida_volta_por_pessoa_brl: float = Field(description="preço médio de ida e volta por pessoa, em reais (BRL)")
    alta_temporada: bool = Field(description="true se o mês é alta temporada para esse trecho")
    justificativa: str = Field(description="uma frase explicando a estimativa")


PROMPT_TEMPLATE = (
    "Você é um especialista em passagens no mercado brasileiro. Estime o preço MÉDIO e realista de uma passagem de "
    "ida e volta, por pessoa, em reais (BRL), de {origin} para {destination}, viajando em {month_name}, no padrão "
    "{quality}. Escolha o meio de transporte que um turista brasileiro normalmente usaria nesse trecho (avião ou "
    "ônibus) e considere se o mês é alta temporada. Considere compra com cerca de um mês de antecedência."
)


def _parse_input(raw_input: str) -> tuple[str, str, int, int, str]:
    """'São Paulo, Brasil | Lisboa, Portugal | 2 | 7 | conforto' -> partes validadas."""
    parts = [part.strip() for part in clean_tool_input(raw_input).split("|")]
    if len(parts) < 5:
        raise ValueError("entrada incompleta. Use: 'Origem | Destino | pessoas | mês (1-12) | padrão'")
    origin, destination = parts[0], parts[1]
    if not origin or not destination:
        raise ValueError("informe a cidade de origem e a de destino")
    try:
        people, month = int(parts[2]), int(parts[3])
    except ValueError as exc:
        raise ValueError("pessoas e mês devem ser números inteiros") from exc
    if people < 1:
        raise ValueError("o número de pessoas deve ser pelo menos 1")
    if not 1 <= month <= 12:
        raise ValueError("o mês deve estar entre 1 e 12")
    return origin, destination, people, month, normalize_quality(parts[4])


def make_fares_tool(llm: BaseChatModel) -> Tool:
    """Cria a ferramenta ligada ao LLM do agente."""
    estimate = make_cached_estimator(llm, FareEstimate)

    def estimate_fares(raw_input: str) -> str:
        try:
            origin, destination, people, month, quality = _parse_input(raw_input)
            result: FareEstimate = estimate(
                PROMPT_TEMPLATE.format(
                    origin=origin, destination=destination,
                    month_name=MONTH_NAMES[month - 1], quality=QUALITY_DESCRIPTIONS[quality],
                )
            )
        except (ValueError, KeyError) as exc:
            return f"Erro: {exc.args[0] if exc.args else exc}."
        except EstimationError as exc:
            return f"Erro: não foi possível estimar as passagens agora ({exc})."

        per_person = result.preco_ida_volta_por_pessoa_brl
        if not 0 <= per_person < MAX_REASONABLE_BRL:
            return "Erro: o modelo devolveu um preço fora de uma faixa razoável. Tente novamente."

        season = "alta temporada" if result.alta_temporada else "temporada normal"
        return (
            f"PASSAGENS estimadas pelo modelo de linguagem (ida e volta) — {origin} → {destination}:\n"
            f"- Meio: {result.meio_de_transporte} ({result.classe}), {season}\n"
            f"- Por pessoa: {per_person:,.2f} BRL\n"
            f"- Total para {people} pessoa(s): {per_person * people:,.2f} BRL\n"
            f"Justificativa do modelo: {result.justificativa}\n"
            "Use o total no campo transporte_total da calculadora_orcamento. "
            "É uma estimativa do modelo; o preço real varia com a antecedência e a companhia."
        )

    return Tool(
        name="estimativa_passagens",
        func=estimate_fares,
        description=(
            "Pede ao modelo de linguagem uma estimativa do preço médio de ida e volta entre duas cidades (avião ou "
            "ônibus), conforme o mês e o padrão da viagem, e calcula o total do grupo em BRL. Use quando o usuário "
            "não souber o valor das passagens. Entrada: 'Origem | Destino | pessoas | mês (1-12) | padrão "
            "(economico, conforto ou luxo)'. Exemplo: 'São Paulo, Brasil | Lisboa, Portugal | 2 | 7 | conforto'."
        ),
    )
