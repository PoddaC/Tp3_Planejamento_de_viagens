"""
Interface de linha de comando do Planejador de Viagens.

Executar:  python cli.py
Comandos:  /limpar (nova conversa), /passos (liga/desliga raciocínio), /sair
"""

from agent import AgentResponse, TravelPlannerAgent
from config import ConfigError, get_llm, get_memory_window
from memory import ConversationMemory

EXIT_COMMANDS = {"/sair", "sair", "exit", "quit"}


def print_reasoning(response: AgentResponse) -> None:
    """Imprime o ciclo ReAct de cada passo."""
    for number, step in enumerate(response.steps, start=1):
        print(f"  [Passo {number}] Pensamento: {step.thought}")
        print(f"            Ação: {step.tool}({step.tool_input})")
        observation = step.observation.replace("\n", "\n              ")
        print(f"            Observação: {observation}")


def main() -> None:
    try:
        agent = TravelPlannerAgent(llm=get_llm(), memory=ConversationMemory(get_memory_window()))
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}")
        return

    show_steps = True
    print("✈️  Planejador de Viagens — digite /sair para encerrar, /limpar para nova conversa, /passos para "
          "mostrar/ocultar o raciocínio.\n")

    while True:
        try:
            question = input("Você: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nAté a próxima viagem!")
            break

        if not question:
            continue
        if question.lower() in EXIT_COMMANDS:
            print("Até a próxima viagem!")
            break
        if question.lower() == "/limpar":
            agent.reset()
            print("(memória apagada)\n")
            continue
        if question.lower() == "/passos":
            show_steps = not show_steps
            print(f"(raciocínio {'visível' if show_steps else 'oculto'})\n")
            continue

        response = agent.ask(question)
        if show_steps:
            print_reasoning(response)
        print(f"\nAgente: {response.answer}")
        if response.error:
            print(f"(detalhe técnico: {response.error})")
        print()


if __name__ == "__main__":
    main()
