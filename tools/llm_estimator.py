"""
Estimativas feitas pelo próprio LLM (Gemini), com saída estruturada.

As ferramentas de custos e de passagens pedem ao modelo um JSON validado por
Pydantic (with_structured_output). Assim o número vem do conhecimento do LLM,
mas num formato fixo que o código consegue checar antes de usar.
"""

from collections.abc import Callable

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

MONTH_NAMES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]
QUALITY_DESCRIPTIONS = {
    "economico": "econômico (hostels/pousadas simples, comida popular, transporte público)",
    "conforto": "conforto (hotel 3-4 estrelas, restaurantes de preço médio, táxi/app de vez em quando)",
    "luxo": "luxo (hotel 5 estrelas, restaurantes sofisticados, passeios privativos)",
}


class EstimationError(Exception):
    """O LLM não conseguiu devolver uma estimativa válida."""


def make_cached_estimator(llm: BaseChatModel, schema: type[BaseModel]) -> Callable[[str], BaseModel]:
    """
    Cria uma função prompt -> objeto `schema` preenchido pelo LLM.

    O cache guarda as respostas por prompt: a mesma pergunta na mesma sessão
    devolve os mesmos números (o LLM poderia variar a cada chamada).
    """
    cache: dict[str, BaseModel] = {}
    structured_llm = None  # criado só no primeiro uso (nem todo modelo suporta saída estruturada)

    def estimate(prompt: str) -> BaseModel:
        nonlocal structured_llm
        if prompt not in cache:
            try:
                if structured_llm is None:
                    structured_llm = llm.with_structured_output(schema)
                result = structured_llm.invoke(prompt)
            except Exception as exc:  # noqa: BLE001 - qualquer falha do LLM vira um erro tratável
                raise EstimationError(f"{type(exc).__name__}: {exc}") from exc
            if not isinstance(result, schema):
                raise EstimationError("o modelo não devolveu a estimativa no formato esperado")
            cache[prompt] = result
        return cache[prompt]

    return estimate
