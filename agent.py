"""
Lógica do agente: une LLM + ferramentas + memória usando o framework ReAct.

O ReAct (Reason + Act) faz o LLM alternar entre:
    Thought      -> raciocina sobre o que falta para responder
    Action       -> escolhe uma ferramenta e a entrada dela
    Observation  -> lê o resultado devolvido pela ferramenta
...repetindo até ter informação suficiente para a Final Answer.

As palavras-chave (Thought, Action, Observation...) ficam em inglês porque é o
formato que o parser do LangChain reconhece; o conteúdo é em português.
"""

from dataclasses import dataclass, field
from datetime import date

from langchain_classic.agents import AgentExecutor, create_react_agent
from langchain_classic.agents.output_parsers import ReActSingleInputOutputParser
from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import BaseTool

from memory import ConversationMemory
from tools import get_all_tools

MAX_REASONING_STEPS = 12  # evita loops infinitos de Thought/Action


class TolerantReActParser(ReActSingleInputOutputParser):
    """
    Parser ReAct mais tolerante.

    Modelos recentes (ex.: Gemini) às vezes escrevem a resposta final sem o
    prefixo "Final Answer:". O parser padrão trata isso como erro e gasta uma
    chamada extra ao LLM. Aqui, se o texto não pede nenhuma ferramenta
    (não tem "Action:"), ele é aceito como resposta final.
    """

    def parse(self, text: str) -> AgentAction | AgentFinish:
        if "Observation:" in text and "Final Answer:" not in text:
            # O modelo INVENTOU o resultado de uma ferramenta em vez de chamá-la.
            # Rejeitamos para que ele chame a ferramenta de verdade.
            raise OutputParserException(
                f"Resposta inválida (Observation escrita pelo modelo): {text[:200]}",
                observation=(
                    "Você escreveu 'Observation:' sozinho, mas só o sistema pode escrever observações. "
                    "Chame a ferramenta com 'Action:' e 'Action Input:' e espere o resultado real."
                ),
                llm_output=text,
                send_to_llm=True,
            )
        try:
            return super().parse(text)
        except OutputParserException:
            if "Action:" in text:
                raise  # tentou usar ferramenta, mas com formato quebrado: deixa o agente corrigir
            answer = text.strip()
            if answer.startswith("Thought:"):
                answer = answer.removeprefix("Thought:").strip()
            return AgentFinish(return_values={"output": answer}, log=text)

REACT_PROMPT = """Você é o "Planejador de Viagens", um assistente que ajuda pessoas a planejar viagens.
Responda sempre em português do Brasil, de forma clara e objetiva.
Data de hoje: {today}.

Regras:
- Use as ferramentas sempre que a resposta depender de dados atuais (clima, cotação) ou de cálculos.
- Nunca invente previsões do tempo, cotações ou totais: consulte a ferramenta.
- Use o histórico da conversa para entender referências como "lá", "essa viagem", "e em euros?".
- Se faltar informação essencial (ex.: número de dias), pergunte ao usuário na Final Answer.
- Para orçamento sem custos informados pelo usuário: primeiro use estimativa_de_custos (cidade, padrão, mês)
  e depois calculadora_orcamento com esses valores. Deixe claro que são estimativas.
- Se o usuário informar a cidade de origem mas não o preço das passagens, use estimativa_passagens e
  coloque o total estimado em transporte_total da calculadora_orcamento.
- Ao apresentar um orçamento, use duas tabelas em Markdown: (1) resumo da viagem com destino, padrão,
  período, temporada e viajantes; (2) custos por categoria, total, custo médio por dia, por pessoa e
  por pessoa/dia.
- Se uma ferramenta devolver erro, tente corrigir a entrada uma vez; se persistir, explique ao usuário.
- Sempre escreva um pensamento curto (em português) depois de "Thought:" antes de cada Action.
- Ao terminar, escreva obrigatoriamente "Final Answer:" antes da resposta para o usuário.
- NUNCA escreva "Observation:" você mesmo nem invente resultados de ferramentas: chame a ferramenta e espere.
- Num orçamento, a Final Answer só pode vir DEPOIS de chamar calculadora_orcamento; o total sai dela.

Histórico da conversa:
{chat_history}

Você tem acesso às seguintes ferramentas:
{tools}

Use EXATAMENTE este formato:

Question: a pergunta do usuário
Thought: pense no que precisa fazer
Action: o nome da ferramenta, uma de [{tool_names}]
Action Input: a entrada da ferramenta
Observation: o resultado da ferramenta
... (Thought/Action/Action Input/Observation podem se repetir)
Thought: agora sei a resposta final
Final Answer: a resposta final para o usuário

Se não precisar de ferramenta, vá direto para:
Thought: não preciso de ferramenta
Final Answer: sua resposta

Comece!

Question: {input}
Thought:{agent_scratchpad}"""


@dataclass
class ReasoningStep:
    """Um ciclo do ReAct: pensamento, ação escolhida e observação recebida."""

    thought: str
    tool: str
    tool_input: str
    observation: str


@dataclass
class AgentResponse:
    """Resposta do agente + passos de raciocínio (para exibir na interface)."""

    answer: str
    steps: list[ReasoningStep] = field(default_factory=list)
    error: str | None = None


DEFAULT_FORMAT_FEEDBACK = (
    "Formato inválido. Responda usando 'Thought:' seguido de 'Action:'/'Action Input:' ou de 'Final Answer:'."
)


def _format_error_feedback(error: OutputParserException) -> str:
    """Mensagem devolvida ao LLM quando a saída dele não segue o formato ReAct."""
    if error.send_to_llm and error.observation:
        return str(error.observation)
    return DEFAULT_FORMAT_FEEDBACK


def _extract_thought(action_log: str) -> str:
    """Pega só o texto do 'Thought' de um passo (o log também contém Action/Action Input)."""
    thought = action_log.split("Action:")[0]
    return thought.replace("Thought:", "").strip()


class TravelPlannerAgent:
    """Agente ReAct com memória conversacional para planejamento de viagens."""

    def __init__(
        self,
        llm: BaseChatModel,
        tools: list[BaseTool] | None = None,
        memory: ConversationMemory | None = None,
        verbose: bool = False,
    ):
        self.tools = tools if tools is not None else get_all_tools(llm)
        self.memory = memory if memory is not None else ConversationMemory()

        prompt = PromptTemplate.from_template(REACT_PROMPT)
        react_agent = create_react_agent(
            llm=llm, tools=self.tools, prompt=prompt, output_parser=TolerantReActParser()
        )
        self._executor = AgentExecutor(
            agent=react_agent,
            tools=self.tools,
            max_iterations=MAX_REASONING_STEPS,
            # Se o LLM sair do formato ReAct, o erro vira uma Observation e ele tenta de novo.
            handle_parsing_errors=_format_error_feedback,
            return_intermediate_steps=True,
            verbose=verbose,
        )

    def ask(self, question: str) -> AgentResponse:
        """
        Envia uma pergunta ao agente, executa o ciclo ReAct e salva a troca na memória.

        Erros do LLM (chave inválida, cota, rede) são capturados e devolvidos em
        `AgentResponse.error`, para a interface exibir uma mensagem amigável.
        """
        question = question.strip()
        if not question:
            return AgentResponse(answer="Digite uma pergunta sobre a sua viagem.")

        try:
            result = self._executor.invoke(
                {
                    "input": question,
                    "chat_history": self.memory.as_text(),
                    "today": date.today().strftime("%d/%m/%Y"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - qualquer falha do LLM deve chegar ao usuário sem derrubar a app
            return AgentResponse(
                answer="Desculpe, não consegui falar com o modelo de linguagem agora.",
                error=f"{type(exc).__name__}: {exc}",
            )

        answer = result.get("output", "").strip()
        if "Agent stopped due to iteration limit" in answer:
            answer = (
                "Não consegui concluir o raciocínio dentro do limite de passos. "
                "Tente reformular a pergunta com mais detalhes."
            )

        steps = [
            ReasoningStep(
                thought=_extract_thought(action.log),
                # "_Exception" é o nome interno que o LangChain usa quando o LLM saiu do formato.
                tool="(formato inválido, nova tentativa)" if action.tool == "_Exception" else action.tool,
                tool_input=str(action.tool_input),
                observation=str(observation),
            )
            for action, observation in result.get("intermediate_steps", [])
        ]

        self.memory.add_turn(question, answer)
        return AgentResponse(answer=answer, steps=steps)

    def reset(self) -> None:
        """Começa uma conversa nova (limpa a memória)."""
        self.memory.clear()
