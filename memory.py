"""
Memória conversacional do agente.

Guarda as últimas N trocas (pergunta do usuário + resposta do agente) como
mensagens do LangChain (HumanMessage / AIMessage). A janela evita que o prompt
cresça sem limite em conversas longas (custo e limite de contexto do LLM).
"""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

EMPTY_HISTORY_TEXT = "(nenhuma conversa anterior)"


class ConversationMemory:
    """Memória de janela deslizante: lembra as últimas `window_size` trocas."""

    def __init__(self, window_size: int = 10):
        if window_size < 1:
            raise ValueError("window_size deve ser pelo menos 1")
        self.window_size = window_size
        self._messages: list[BaseMessage] = []

    @property
    def messages(self) -> list[BaseMessage]:
        """Cópia das mensagens dentro da janela, da mais antiga para a mais recente."""
        return list(self._messages)

    def add_turn(self, user_message: str, agent_message: str) -> None:
        """Registra uma troca completa e descarta o que passou da janela."""
        self._messages += [HumanMessage(content=user_message), AIMessage(content=agent_message)]
        max_messages = self.window_size * 2  # cada troca = 2 mensagens
        self._messages = self._messages[-max_messages:]

    def as_text(self) -> str:
        """Formata o histórico como texto para ser inserido no prompt ReAct."""
        if not self._messages:
            return EMPTY_HISTORY_TEXT
        lines = []
        for message in self._messages:
            speaker = "Usuário" if isinstance(message, HumanMessage) else "Assistente"
            lines.append(f"{speaker}: {message.content}")
        return "\n".join(lines)

    def clear(self) -> None:
        """Apaga todo o histórico (ex.: botão 'Nova conversa')."""
        self._messages = []

    def __len__(self) -> int:
        """Número de trocas armazenadas."""
        return len(self._messages) // 2
