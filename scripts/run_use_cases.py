"""
Executa os casos de uso do projeto contra o LLM real e salva um relatório.

Executar (na raiz do projeto):
    python scripts/run_use_cases.py            -> todos os casos  -> docs/resultados_casos_de_uso.md
    python scripts/run_use_cases.py UC4 UC7    -> só os escolhidos -> docs/resultados_UC4_UC7.md

O script para sozinho se a cota do LLM acabar (erro 429), para não gastar
chamadas à toa. Plano gratuito do Gemini: poucas requisições por dia.

Cada caso começa com memória vazia. O caso de memória envia várias mensagens
seguidas para testar se o agente usa o contexto anterior.
"""

import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent import AgentResponse, TravelPlannerAgent  # noqa: E402
from config import ConfigError, get_llm  # noqa: E402

OUTPUT_FILE = PROJECT_ROOT / "docs" / "resultados_casos_de_uso.md"
PAUSE_BETWEEN_CALLS_SECONDS = 2  # evita estourar o limite de requisições do plano gratuito

USE_CASES = [
    {
        "id": "UC1",
        "title": "Previsão do tempo do destino",
        "expected": "Usar previsao_do_tempo e resumir o clima com dica do que levar na mala.",
        "messages": ["Vou para Lisboa nos próximos 5 dias. Como vai estar o tempo? O que levo na mala?"],
    },
    {
        "id": "UC2",
        "title": "Conversão de moeda",
        "expected": "Usar conversor_de_moedas com a cotação atual.",
        "messages": ["Tenho 1500 euros guardados. Quanto isso vale em reais hoje?"],
    },
    {
        "id": "UC3",
        "title": "Orçamento de viagem",
        "expected": "Montar o JSON correto e usar calculadora_orcamento.",
        "messages": [
            "Calcule o orçamento: 5 dias em Gramado, 2 pessoas, pousada de R$ 450 a diária, "
            "R$ 150 por pessoa por dia de comida, R$ 1.200 de gasolina e pedágio e R$ 600 em passeios."
        ],
    },
    {
        "id": "UC4",
        "title": "Plano completo (custos e passagens estimados pelo LLM, clima e câmbio)",
        "expected": "Encadear estimativa_de_custos (indica a temporada) -> estimativa_passagens -> calculadora_orcamento -> previsao_do_tempo -> conversor_de_moedas.",
        "messages": [
            "Monte o orçamento de uma viagem para Lisboa, Portugal, de 10/07 a 15/07 do ano que vem, "
            "2 pessoas em 1 quarto, padrão conforto, saindo de São Paulo (estime as passagens). "
            "Inclua o clima do período, a cotação do euro (quanto vale R$ 1.000) e apresente em tabelas."
        ],
    },
    {
        "id": "UC5",
        "title": "Memória conversacional",
        "expected": "Entender 'lá', 'moeda local' e lembrar o destino sem que o usuário repita.",
        "messages": [
            "Estou planejando uma viagem para Santiago do Chile.",
            "Como vai estar o tempo lá nos próximos 3 dias?",
            "Converta 2000 reais para a moeda local.",
            "Me lembra: qual é o meu destino e quanto vou ter na moeda de lá?",
        ],
    },
    {
        "id": "UC6",
        "title": "Tratamento de erros",
        "expected": "Ferramentas devolvem erro amigável; o agente explica em vez de inventar dados.",
        "messages": ["Qual a previsão do tempo em Xptozlândia? E quanto são 100 ABC em reais?"],
    },
    {
        "id": "UC7",
        "title": "Planejamento em dólar (orçamento + câmbio + clima)",
        "expected": "Encadear calculadora_orcamento (USD) -> conversor_de_moedas -> previsao_do_tempo.",
        "messages": [
            "Vou passar 4 dias em Buenos Aires com minha esposa. Hotel de US$ 90 a diária, US$ 45 por pessoa "
            "por dia de comida e US$ 150 de passeios. Quanto fica em reais e como vai estar o tempo?"
        ],
    },
]


def format_response(response: AgentResponse) -> str:
    """Formata uma resposta (passos ReAct + resposta final) em Markdown."""
    lines = []
    for number, step in enumerate(response.steps, start=1):
        lines += [
            f"**Passo {number}**",
            f"- Thought: {step.thought}",
            f"- Action: `{step.tool}` | Action Input: `{step.tool_input}`",
            "- Observation:",
            "```",
            step.observation,
            "```",
        ]
    if not response.steps:
        lines.append("_(nenhuma ferramenta usada)_")
    lines.append(f"\n**Resposta final:** {response.answer}")
    if response.error:
        lines.append(f"\n> ⚠️ Erro: {response.error}")
    return "\n".join(lines)


QUOTA_ERROR_MARKERS = ("RESOURCE_EXHAUSTED", "429", "RateLimit")


def select_cases(requested_ids: list[str]) -> list[dict]:
    """Filtra os casos pelos IDs pedidos na linha de comando (ex.: UC4 UC7)."""
    if not requested_ids:
        return USE_CASES
    wanted = {case_id.upper() for case_id in requested_ids}
    unknown = wanted - {case["id"] for case in USE_CASES}
    if unknown:
        print(f"Casos desconhecidos: {', '.join(sorted(unknown))}. Disponíveis: {', '.join(c['id'] for c in USE_CASES)}")
        sys.exit(1)
    return [case for case in USE_CASES if case["id"] in wanted]


def main() -> None:
    cases = select_cases(sys.argv[1:])
    output_file = OUTPUT_FILE if len(cases) == len(USE_CASES) else (
        OUTPUT_FILE.parent / f"resultados_{'_'.join(case['id'] for case in cases)}.md"
    )
    try:
        llm = get_llm()
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}")
        sys.exit(1)

    report = [
        "# Resultados dos casos de uso",
        f"\nExecutado em {datetime.now():%d/%m/%Y %H:%M} com o modelo `{getattr(llm, 'model', getattr(llm, 'model_name', '?'))}`.\n",
    ]
    quota_exhausted = False
    for case in cases:
        print(f"Executando {case['id']} - {case['title']}...")
        agent = TravelPlannerAgent(llm=llm)  # memória nova para cada caso
        report += [f"\n## {case['id']} — {case['title']}", f"\n**Esperado:** {case['expected']}\n"]
        for message in case["messages"]:
            started = time.perf_counter()
            response = agent.ask(message)
            elapsed = time.perf_counter() - started
            tools_used = ", ".join(step.tool for step in response.steps) or "nenhuma"
            print(f"  -> {elapsed:.1f}s | ferramentas: {tools_used}")
            report += [f"\n### 🧑 {message}\n", format_response(response), f"\n_Tempo: {elapsed:.1f}s_"]
            if response.error and any(marker in response.error for marker in QUOTA_ERROR_MARKERS):
                quota_exhausted = True
                break
            time.sleep(PAUSE_BETWEEN_CALLS_SECONDS)
        if quota_exhausted:
            print("\n⚠️ A cota do LLM acabou. Parei para não gastar chamadas. Rode de novo mais tarde, só com os "
                  "casos que faltaram (ex.: python scripts/run_use_cases.py UC7).")
            break

    output_file.parent.mkdir(exist_ok=True)
    output_file.write_text("\n".join(report), encoding="utf-8")
    print(f"\nRelatório salvo em {output_file}")


if __name__ == "__main__":
    main()
