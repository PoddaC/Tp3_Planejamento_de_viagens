"""
Testes do agente ReAct e da memória.

Usamos um LLM falso (FakeListChatModel) que devolve respostas pré-definidas no
formato ReAct. Assim validamos o ciclo Thought -> Action -> Observation e o uso
da memória sem gastar chamadas reais de API.
"""

from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from pydantic import Field

from agent import TravelPlannerAgent
from memory import EMPTY_HISTORY_TEXT, ConversationMemory
from tests.test_tools import fake_currency_api


class RecordingFakeLLM(FakeListChatModel):
    """LLM falso que também guarda os prompts recebidos (para inspecionar a memória)."""

    received_prompts: list[str] = Field(default_factory=list)

    def _stream(self, messages, *args, **kwargs):
        self.received_prompts.append(messages[0].content)
        yield from super()._stream(messages, *args, **kwargs)


class BrokenLLM(FakeListChatModel):
    """LLM falso que simula uma falha de rede/API."""

    def _stream(self, *args, **kwargs):
        raise ConnectionError("sem internet")
        yield  # pragma: no cover - torna o método um gerador


# ---------- memória ----------


def test_memory_window_discards_old_turns():
    memory = ConversationMemory(window_size=2)
    for index in range(3):
        memory.add_turn(f"pergunta {index}", f"resposta {index}")
    assert len(memory) == 2
    assert "pergunta 0" not in memory.as_text()
    assert "pergunta 2" in memory.as_text()


def test_memory_clear():
    memory = ConversationMemory()
    memory.add_turn("oi", "olá")
    memory.clear()
    assert memory.as_text() == EMPTY_HISTORY_TEXT


# ---------- agente ----------


def test_agent_uses_tool_and_returns_final_answer():
    fake_llm = FakeListChatModel(
        responses=[
            " Preciso converter o valor.\nAction: conversor_de_moedas\nAction Input: 100 USD para BRL",
            " Já tenho a cotação.\nFinal Answer: 100 dólares equivalem a R$ 500,00.",
        ]
    )
    agent = TravelPlannerAgent(llm=fake_llm)
    with patch("tools.currency.get_json", side_effect=fake_currency_api):
        response = agent.ask("Quanto é 100 dólares em reais?")

    assert response.error is None
    assert response.answer == "100 dólares equivalem a R$ 500,00."
    assert len(response.steps) == 1
    step = response.steps[0]
    assert step.tool == "conversor_de_moedas"
    assert step.thought == "Preciso converter o valor."
    assert "500.00 BRL" in step.observation
    assert len(agent.memory) == 1


def test_agent_chains_multiple_tools():
    fake_llm = FakeListChatModel(
        responses=[
            " Primeiro calculo o orçamento.\nAction: calculadora_orcamento\n"
            'Action Input: {"dias": 2, "pessoas": 1, "transporte_total": 100, "moeda": "USD"}',
            " Agora converto para reais.\nAction: conversor_de_moedas\nAction Input: 110 USD para BRL",
            " Pronto.\nFinal Answer: A viagem custa US$ 110, cerca de R$ 550.",
        ]
    )
    agent = TravelPlannerAgent(llm=fake_llm)
    with patch("tools.currency.get_json", side_effect=fake_currency_api):
        response = agent.ask("Quanto custa a viagem em reais?")

    assert [step.tool for step in response.steps] == ["calculadora_orcamento", "conversor_de_moedas"]
    assert "TOTAL: 110.00 USD" in response.steps[0].observation
    assert "550.00 BRL" in response.steps[1].observation


def test_agent_sends_memory_to_the_prompt():
    """A segunda pergunta deve chegar ao LLM com a primeira troca no histórico."""
    fake_llm = RecordingFakeLLM(
        responses=[
            " Não preciso de ferramenta.\nFinal Answer: Ótimo, Lisboa é uma bela escolha!",
            " Não preciso de ferramenta.\nFinal Answer: Você disse que vai para Lisboa.",
        ]
    )
    agent = TravelPlannerAgent(llm=fake_llm)
    agent.ask("Vou viajar para Lisboa.")

    agent.ask("Para onde eu vou mesmo?")
    prompt_sent = fake_llm.received_prompts[-1]
    assert "(nenhuma conversa anterior)" in fake_llm.received_prompts[0]
    assert "Usuário: Vou viajar para Lisboa." in prompt_sent
    assert "Assistente: Ótimo, Lisboa é uma bela escolha!" in prompt_sent


def test_agent_accepts_answer_without_final_answer_prefix():
    """Gemini às vezes responde sem 'Final Answer:'; isso deve ser aceito sem passo extra."""
    fake_llm = FakeListChatModel(
        responses=[
            " Vou converter.\nAction: conversor_de_moedas\nAction Input: 100 USD para BRL",
            "Hoje, 100 dólares equivalem a **R$ 500,00**.",
        ]
    )
    with patch("tools.currency.get_json", side_effect=fake_currency_api):
        response = TravelPlannerAgent(llm=fake_llm).ask("100 dólares em reais?")
    assert response.answer == "Hoje, 100 dólares equivalem a **R$ 500,00**."
    assert [step.tool for step in response.steps] == ["conversor_de_moedas"]


def test_agent_recovers_from_broken_action_format():
    fake_llm = FakeListChatModel(
        responses=[
            "Action: conversor_de_moedas",  # faltou o Action Input
            " Agora no formato certo.\nFinal Answer: Olá! Como posso ajudar na sua viagem?",
        ]
    )
    response = TravelPlannerAgent(llm=fake_llm).ask("oi")
    assert response.answer == "Olá! Como posso ajudar na sua viagem?"


def test_agent_handles_llm_failure():
    response = TravelPlannerAgent(llm=BrokenLLM(responses=["x"])).ask("oi")
    assert response.error and "sem internet" in response.error
    assert len(response.steps) == 0


def test_agent_ignores_empty_question():
    agent = TravelPlannerAgent(llm=FakeListChatModel(responses=["x"]))
    assert "Digite uma pergunta" in agent.ask("   ").answer
    assert len(agent.memory) == 0


def test_agent_rejects_invented_observation_and_calls_the_tool():
    """O Gemini às vezes 'inventa' a Observation; o agente deve rejeitar e chamar a ferramenta de verdade."""
    fake_llm = RecordingFakeLLM(
        responses=[
            "Observation: Custos de referência inventados: R$ 320 por noite.",
            " Vou chamar a calculadora.\nAction: calculadora_orcamento\nAction Input: {\"dias\": 2, \"pessoas\": 1, \"transporte_total\": 100}",
            " Pronto.\nFinal Answer: O total é R$ 110,00.",
        ]
    )
    response = TravelPlannerAgent(llm=fake_llm).ask("Quanto custa?")
    assert response.answer == "O total é R$ 110,00."
    assert [step.tool for step in response.steps] == ["(formato inválido, nova tentativa)", "calculadora_orcamento"]
    assert "só o sistema pode escrever observações" in response.steps[0].observation
    assert "TOTAL: 110.00 BRL" in response.steps[1].observation
