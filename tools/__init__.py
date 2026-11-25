"""Ferramentas (tools) disponíveis para o agente."""

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from tools.budget import budget_tool
from tools.currency import currency_tool
from tools.fares import make_fares_tool
from tools.reference_costs import make_reference_costs_tool
from tools.weather import weather_tool


def get_all_tools(llm: BaseChatModel) -> list[BaseTool]:
    """
    Lista de ferramentas que o agente pode usar.

    As de estimativa (custos e passagens) recebem o LLM porque é ele quem
    faz a estimativa; as demais consultam APIs ou fazem cálculo local.
    """
    return [weather_tool, currency_tool, make_reference_costs_tool(llm), make_fares_tool(llm), budget_tool]
