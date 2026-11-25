"""
Interface web (Streamlit) do Planejador de Viagens.

Executar:  streamlit run app.py

Abas:
- Planejar viagem: um formulário só; o agente encadeia todas as ferramentas.
- Orçamento / Clima / Câmbio: formulários para uma informação específica.
- Tirar dúvidas: chat livre com o agente.
Os formulários só montam a pergunta e a enviam ao MESMO agente ReAct, então
tudo fica na memória da conversa.
"""

import math
import os
from dataclasses import dataclass
from datetime import date, timedelta

import streamlit as st

import destinations
from agent import AgentResponse, TravelPlannerAgent
from config import DEFAULT_MODELS, ConfigError, get_llm, get_memory_window
from memory import ConversationMemory

# Ícone e rótulo amigável de cada ferramenta (usados nas etiquetas e no raciocínio).
TOOL_LABELS = {
    "previsao_do_tempo": ("🌤️", "Clima"),
    "conversor_de_moedas": ("💱", "Câmbio"),
    "estimativa_de_custos": ("📊", "Estimativa de custos"),
    "estimativa_passagens": ("🛫", "Estimativa de passagens"),
    "calculadora_orcamento": ("🧮", "Calculadora de orçamento"),
}

QUICK_EXAMPLES = [
    "Vou para Lisboa semana que vem. Como vai estar o tempo e o que levo na mala?",
    "Quanto são 1500 euros em reais hoje?",
    "Quanto custa uma viagem econômica de 5 dias para Buenos Aires em julho, para 2 pessoas?",
]
DEFAULT_TRIP_START_DAYS = 30  # o formulário de orçamento sugere uma viagem daqui a ~1 mês
DEFAULT_TRIP_LENGTH_DAYS = 5
DEFAULT_ORIGIN_CITY = "São Paulo"
FARE_OPTIONS = ("Estimar automaticamente", "Já sei o valor", "Não incluir")
FINISH_BUDGET_FOLLOW_UP = (
    "Você ainda não chamou a calculadora_orcamento, então o orçamento não está completo. Use as estimativas "
    "obtidas (chame as ferramentas de estimativa se ainda não chamou), chame a calculadora_orcamento e apresente "
    "o resultado final em tabelas, com total, custo médio por dia, por pessoa e por pessoa/dia."
)

CUSTOM_CSS = """
<style>
.block-container {padding-top: 2rem; max-width: 960px;}
.hero {
    background: linear-gradient(135deg, #0E7490 0%, #0891B2 55%, #22D3EE 100%);
    border-radius: 18px; padding: 26px 32px; color: white; margin-bottom: 1.2rem;
    box-shadow: 0 10px 30px rgba(14, 116, 144, 0.25);
}
.hero h1 {color: white; font-size: 2rem; margin: 0 0 6px 0; padding: 0;}
.hero p {color: rgba(255,255,255,0.92); margin: 0 0 12px 0; font-size: 1rem;}
.chip {
    display: inline-block; background: rgba(255,255,255,0.18); border: 1px solid rgba(255,255,255,0.35);
    color: white; border-radius: 999px; padding: 4px 12px; margin: 0 6px 6px 0; font-size: 0.85rem;
}
.summary {
    background: #F1F7F9; border: 1px solid #CFE8EE; border-radius: 12px; padding: 12px 16px;
    margin: 8px 0 12px 0; font-size: 0.95rem;
}
.tool-chip {
    display: inline-block; background: #ECFEFF; color: #0E7490; border: 1px solid #A5F3FC;
    border-radius: 999px; padding: 2px 10px; margin-right: 6px; font-size: 0.8rem;
}
.step-title {font-weight: 600; color: #0E7490; margin-top: 6px;}
</style>
"""


# ---------------------------------------------------------------- agente


@st.cache_resource(show_spinner=False)
def create_agent() -> TravelPlannerAgent:
    """Cria o agente uma única vez por sessão do servidor."""
    return TravelPlannerAgent(llm=get_llm(), memory=ConversationMemory(get_memory_window()))


def ask_agent(agent: TravelPlannerAgent, question: str) -> AgentResponse:
    """Envia a pergunta, mostra um indicador de carregamento e registra no histórico do chat."""
    with st.spinner("Pensando e consultando as ferramentas..."):
        response = agent.ask(question)
    st.session_state.chat += [("user", question), ("assistant", response)]
    return response


# ---------------------------------------------------------------- componentes visuais


def render_hero() -> None:
    chips = "".join(f'<span class="chip">{icon} {label}</span>' for icon, label in TOOL_LABELS.values())
    st.markdown(
        f"""<div class="hero">
            <h1>✈️ Planejador de Viagens</h1>
            <p>Planeje a viagem completa num formulário só, consulte uma informação específica ou tire dúvidas.
            O agente raciocina, escolhe as ferramentas certas e responde com dados atuais.</p>
            {chips}
        </div>""",
        unsafe_allow_html=True,
    )


def render_tools_used(response: AgentResponse) -> None:
    used = dict.fromkeys(step.tool for step in response.steps if step.tool in TOOL_LABELS)
    if used:
        chips = "".join(f'<span class="tool-chip">{TOOL_LABELS[t][0]} {TOOL_LABELS[t][1]}</span>' for t in used)
        st.markdown(chips, unsafe_allow_html=True)


def render_reasoning(response: AgentResponse) -> None:
    """Mostra o ciclo ReAct (Thought / Action / Observation) em um painel recolhível."""
    if not response.steps:
        return
    with st.expander(f"🧠 Ver raciocínio do agente ({len(response.steps)} passo(s))"):
        for number, step in enumerate(response.steps, start=1):
            icon = TOOL_LABELS.get(step.tool, ("⚙️", ""))[0]
            st.markdown(f'<div class="step-title">Passo {number}</div>', unsafe_allow_html=True)
            st.markdown(f"💭 **Pensamento:** {step.thought or '—'}")
            st.markdown(f"{icon} **Ação:** `{step.tool}` ← `{step.tool_input}`")
            st.code(step.observation, language=None)


def render_assistant_message(response: AgentResponse) -> None:
    st.markdown(response.answer)
    if response.error:
        st.error(f"Detalhe técnico: {response.error}")
    render_tools_used(response)
    render_reasoning(response)


def render_form_result(result_key: str) -> None:
    """Mostra o último resultado de um formulário (fica salvo entre recarregamentos da página)."""
    if result_key in st.session_state:
        st.divider()
        with st.chat_message("assistant", avatar="✈️"):
            render_assistant_message(st.session_state[result_key])


def destination_picker(key_prefix: str) -> tuple[str, str, destinations.City] | None:
    """Campos de texto País / Estado / Cidade. Devolve None enquanto algum estiver vazio."""
    col_country, col_state, col_city = st.columns(3)
    country = col_country.text_input("País", value="Brasil", placeholder="Ex.: Brasil", key=f"{key_prefix}_country")
    state = col_state.text_input("Estado / região", placeholder="Ex.: Rio de Janeiro", key=f"{key_prefix}_state")
    city_name = col_city.text_input("Cidade", placeholder="Ex.: Paraty", key=f"{key_prefix}_city")
    country, state, city_name = country.strip(), state.strip(), city_name.strip()
    if not (country and state and city_name):
        st.caption("Preencha país, estado e cidade.")
        return None
    return country, state, destinations.resolve_city(country, city_name)


def date_range_picker(label: str, default_start: date, default_days: int, key: str) -> tuple[date, date] | None:
    """Seletor de ida e volta. Devolve None enquanto o usuário escolheu só a primeira data."""
    today = date.today()
    selection = st.date_input(
        label,
        value=(default_start, default_start + timedelta(days=default_days - 1)),
        min_value=today,
        max_value=today + timedelta(days=365 * 2),
        format="DD/MM/YYYY",
        key=key,
    )
    if isinstance(selection, (tuple, list)) and len(selection) == 2:
        return selection[0], selection[1]
    st.caption("Selecione também a data de volta.")
    return None


def weather_tool_input(country: str, state: str, city: destinations.City, start: date, end: date) -> str:
    """Entrada exata para a ferramenta de clima (cidade + país + datas)."""
    if destinations.is_known_country(country):
        place = f"{city.search_name}, {destinations.country_code(country)}"  # filtro exato por código do país
    else:
        place = f"{city.search_name}, {country}"  # a API de geocoding aceita o país por extenso após a vírgula
    return f"{place} | {start.isoformat()} | {end.isoformat()}"


# ---------------------------------------------------------------- abas


def tab_chat(agent: TravelPlannerAgent) -> None:
    """Chat livre com o agente (todo o histórico, inclusive o que veio dos formulários)."""
    if not st.session_state.chat:
        st.caption("Tire dúvidas sobre a viagem. O agente lembra do que foi planejado nas outras abas.")
        st.markdown("**Experimente perguntar:**")
        for index, example in enumerate(QUICK_EXAMPLES):
            if st.button(example, key=f"example_{index}", width="stretch"):
                st.session_state.pending_question = example
                st.rerun()

    for role, content in st.session_state.chat:
        avatar = "🧳" if role == "user" else "✈️"
        with st.chat_message(role, avatar=avatar):
            # Checa pelo papel (e não com isinstance): quando o Streamlit recarrega o código,
            # a classe AgentResponse é recriada e objetos antigos deixariam de ser reconhecidos.
            if role == "assistant" and hasattr(content, "answer"):
                render_assistant_message(content)
            else:
                st.markdown(content)

    question = st.chat_input("Pergunte sobre clima, câmbio ou orçamento...")
    question = question or st.session_state.pop("pending_question", None)
    if question:
        ask_agent(agent, question)
        st.rerun()


@dataclass
class TripDetails:
    """Dados da viagem preenchidos nos formulários de Orçamento e Planejar viagem."""

    country: str
    state: str
    city: destinations.City
    start: date
    end: date
    people: int
    rooms: int
    quality_key: str
    origin: str
    fare_mode: str
    fare: float

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def place(self) -> str:
        return f"{self.city.display_name}, {self.state}, {self.country}"

    @property
    def quality_label(self) -> str:
        return destinations.QUALITY_LEVELS[self.quality_key]


def trip_details_form(prefix: str) -> TripDetails | None:
    """Campos comuns da viagem + resumo. Devolve None enquanto faltar destino ou datas."""
    st.markdown("##### Para onde você vai?")
    destination = destination_picker(prefix)

    st.markdown("##### Quando e com quem?")
    col_dates, col_people, col_rooms = st.columns([2, 1, 1])
    with col_dates:
        period = date_range_picker(
            "Ida e volta", date.today() + timedelta(days=DEFAULT_TRIP_START_DAYS), DEFAULT_TRIP_LENGTH_DAYS, f"{prefix}_dates"
        )
    people = col_people.number_input("Pessoas", min_value=1, max_value=20, value=2, step=1, key=f"{prefix}_people")
    rooms = col_rooms.number_input(
        "Quartos", min_value=1, max_value=20, value=math.ceil(people / 2), step=1,
        help="Padrão: 1 quarto a cada 2 pessoas.", key=f"{prefix}_rooms_{people}",
    )

    st.markdown("##### Como vai ser a viagem?")
    quality_key = st.radio(
        "Padrão",
        list(destinations.QUALITY_LEVELS),
        format_func=destinations.QUALITY_LEVELS.get,
        index=1,
        horizontal=True,
        help="Econômico: hostels/pousadas simples. Conforto: hotéis 3-4★. Luxo: hotéis 5★ e restaurantes sofisticados.",
        key=f"{prefix}_quality",
    )
    col_origin, col_fare_mode = st.columns(2)
    origin = col_origin.text_input(
        "Saindo de (cidade no Brasil)", value=DEFAULT_ORIGIN_CITY, help="Usada para estimar as passagens.",
        key=f"{prefix}_origin",
    ).strip()
    fare_mode = col_fare_mode.radio(
        "Passagens de ida e volta", FARE_OPTIONS, horizontal=True,
        help="O modelo de linguagem estima o preço conforme o trecho, o mês e o padrão.", key=f"{prefix}_fare_mode",
    )
    fare = 0.0
    if fare_mode == "Já sei o valor":
        fare = st.number_input(
            "Valor das passagens (R$, soma de todos os viajantes)", min_value=0.0, value=0.0, step=100.0,
            key=f"{prefix}_fare",
        )

    if period is None or destination is None:
        return None
    country, state, city = destination
    details = TripDetails(country, state, city, period[0], period[1], int(people), int(rooms), quality_key, origin, fare_mode, fare)
    origin_text = f" &nbsp;·&nbsp; 🛫 saindo de {origin}" if fare_mode == "Estimar automaticamente" and origin else ""
    st.markdown(
        f'<div class="summary">📍 <b>{city.display_name}</b>, {state} — {country} &nbsp;·&nbsp; '
        f"📅 {details.start:%d/%m/%Y} a {details.end:%d/%m/%Y} ({details.days} dias) &nbsp;·&nbsp; "
        f"👥 {details.people} pessoa(s), {details.rooms} quarto(s) &nbsp;·&nbsp; ⭐ {details.quality_label}{origin_text}</div>",
        unsafe_allow_html=True,
    )
    return details


def build_budget_instructions(trip: TripDetails) -> str:
    """Parte do pedido que descreve a viagem e o passo a passo do orçamento."""
    if trip.fare_mode == "Estimar automaticamente" and trip.origin:
        fare_text = (
            f"estimar saindo de {trip.origin}, Brasil. Use estimativa_passagens com a entrada "
            f"'{trip.origin}, Brasil | {trip.place} | {trip.people} | {trip.start.month} | {trip.quality_key}' "
            "e coloque o total em transporte_total"
        )
    elif trip.fare_mode == "Já sei o valor" and trip.fare > 0:
        fare_text = f"R$ {trip.fare:,.2f} (total do grupo; use em transporte_total)"
    else:
        fare_text = "não incluir"
    uses_fare_tool = "estimativa_passagens" in fare_text
    return (
        f"- Destino: {trip.place}\n"
        f"- Período: {trip.start:%d/%m/%Y} a {trip.end:%d/%m/%Y} ({trip.days} dias)\n"
        f"- Viajantes: {trip.people} pessoa(s) em {trip.rooms} quarto(s)\n"
        f"- Padrão: {trip.quality_label}\n"
        f"- Passagens: {fare_text}\n"
        f"Orçamento: use estimativa_de_custos com a entrada '{trip.place} | {trip.quality_key} | {trip.start.month} | BRL'"
        f"{', depois estimativa_passagens (veja acima)' if uses_fare_tool else ''} e, por fim, calculadora_orcamento "
        f"em BRL com quartos={trip.rooms}, preenchendo destino, qualidade, periodo e temporada "
        "(alta ou normal, conforme a estimativa de custos)."
    )


def ask_until_budget_is_calculated(agent: TravelPlannerAgent, question: str) -> AgentResponse:
    """Envia o pedido e, se o agente não usou a calculadora, pede que ele conclua a conta."""
    response = ask_agent(agent, question)
    if not response.error and not any(step.tool == "calculadora_orcamento" for step in response.steps):
        response = ask_agent(agent, FINISH_BUDGET_FOLLOW_UP)
    return response


def tab_planner(agent: TravelPlannerAgent) -> None:
    """Planejamento completo: um formulário, e o agente usa todas as ferramentas necessárias."""
    st.caption("Preencha uma vez e receba o plano completo: orçamento, passagens, clima e câmbio.")
    trip = trip_details_form("plan")

    st.markdown("##### O que incluir no plano?")
    col_weather, col_currency = st.columns(2)
    include_weather = col_weather.checkbox("🌤️ Clima do período", value=True, key="plan_weather")
    include_currency = col_currency.checkbox("💱 Câmbio da moeda local", value=True, key="plan_currency")

    if trip and st.button("✈️ Planejar minha viagem", type="primary", width="stretch"):
        extra_steps = []
        if include_weather:
            weather_input = weather_tool_input(trip.country, trip.state, trip.city, trip.start, trip.end)
            extra_steps.append(f"Clima: use previsao_do_tempo com a entrada '{weather_input}'.")
        if include_currency:
            known = destinations.is_known_country(trip.country)
            currency = destinations.country_currency(trip.country) if known else None
            if currency == "BRL":
                extra_steps.append("Câmbio: o destino usa reais, então diga apenas que não é preciso trocar dinheiro.")
            elif currency:
                extra_steps.append(f"Câmbio: use conversor_de_moedas com a entrada '1000 BRL para {currency}'.")
            else:
                extra_steps.append(
                    f"Câmbio: identifique o código de 3 letras da moeda de {trip.country} e use conversor_de_moedas "
                    "para converter 1000 BRL para essa moeda."
                )
        question = (
            "Planeje esta viagem completa:\n"
            f"{build_budget_instructions(trip)}\n"
            + "\n".join(extra_steps)
            + "\nApresente um relatório com as seções: Resumo da viagem (tabela), Orçamento (tabela de custos com "
            "total, custo médio por dia, por pessoa e por pessoa/dia)"
            + (", Clima do período (tabela ou resumo, com o que levar na mala)" if include_weather else "")
            + (", Câmbio (cotação e quanto vale R$ 1.000)" if include_currency else "")
            + " e 3 dicas práticas para o destino."
        )
        st.session_state.result_planner = ask_until_budget_is_calculated(agent, question)
    render_form_result("result_planner")


def tab_budget(agent: TravelPlannerAgent) -> None:
    """Só o orçamento: destino, datas, viajantes e padrão da viagem."""
    trip = trip_details_form("budget")
    if trip and st.button("🧮 Calcular orçamento", type="primary", width="stretch"):
        question = (
            "Monte o orçamento desta viagem:\n"
            f"{build_budget_instructions(trip)}\n"
            "Apresente o resultado em tabelas: resumo da viagem e custos (total, custo médio por dia, por pessoa "
            "e por pessoa/dia)."
        )
        st.session_state.result_budget = ask_until_budget_is_calculated(agent, question)
    render_form_result("result_budget")


def tab_weather(agent: TravelPlannerAgent) -> None:
    """Formulário de clima: destino e período."""
    st.markdown("##### Onde e quando?")
    destination = destination_picker("weather")
    period = date_range_picker("Período", date.today(), 7, "weather_dates")
    st.caption("Até 16 dias à frente: previsão do tempo. Mais longe que isso: clima típico do mesmo período do ano passado.")

    if period and destination and st.button("🌤️ Ver clima", type="primary", width="stretch"):
        country, state, city = destination
        start, end = period
        question = (
            f"Como vai estar o tempo em {city.display_name} ({state}, {country}) de {start:%d/%m/%Y} a {end:%d/%m/%Y}? "
            f"Use previsao_do_tempo com a entrada '{weather_tool_input(country, state, city, start, end)}'. "
            "Mostre o resultado em uma tabela e dê dicas do que levar na mala."
        )
        st.session_state.result_weather = ask_agent(agent, question)
    render_form_result("result_weather")


def tab_currency(agent: TravelPlannerAgent) -> None:
    """Formulário de câmbio: país de destino (define a moeda), valor e direção."""
    st.markdown("##### Para qual país?")
    col_country, col_amount = st.columns(2)
    country = col_country.text_input("País", placeholder="Ex.: Portugal", key="fx_country").strip()
    amount = col_amount.number_input("Valor", min_value=0.01, value=1000.0, step=100.0)
    if not country:
        st.caption("Digite o país de destino.")
        return
    # País da base: moeda conhecida. País digitado: o agente (LLM) identifica a moeda.
    currency = destinations.country_currency(country) if destinations.is_known_country(country) else None
    currency_label = currency or "moeda local"
    direction = st.radio("Converter", [f"Reais → {currency_label}", f"{currency_label} → Reais"], horizontal=True)

    if st.button("💱 Converter", type="primary", width="stretch"):
        to_local = direction.startswith("Reais")
        if currency:
            source, target = ("BRL", currency) if to_local else (currency, "BRL")
            question = (
                f"Vou viajar para {country}. Converta {amount:.2f} {source} para {target}. "
                f"Use conversor_de_moedas com a entrada '{amount:.2f} {source} para {target}'. "
                f"Informe o valor convertido e quanto vale 1 {currency} em reais."
            )
        else:
            direction_text = "de reais (BRL) para a moeda local" if to_local else "da moeda local para reais (BRL)"
            question = (
                f"Vou viajar para {country}. Identifique o código de 3 letras da moeda local desse país e converta "
                f"{amount:.2f} {direction_text} usando conversor_de_moedas. Informe a moeda, o valor convertido e "
                "quanto vale 1 unidade da moeda local em reais."
            )
        st.session_state.result_currency = ask_agent(agent, question)
    render_form_result("result_currency")


# ---------------------------------------------------------------- barra lateral e main


def render_sidebar(agent: TravelPlannerAgent) -> None:
    with st.sidebar:
        st.markdown("### Como usar")
        st.markdown(
            "**Plano completo (recomendado)**\n"
            "1. Abra **✈️ Planejar viagem**\n"
            "2. Digite país, estado e cidade do destino\n"
            "3. Escolha as datas, pessoas e o padrão da viagem\n"
            "4. Informe de onde você sai para estimar as passagens\n"
            "5. Clique em **Planejar minha viagem** e aguarde alguns segundos\n\n"
            "**Só uma informação**\n"
            "- **🧮 Orçamento:** quanto vai custar\n"
            "- **🌤️ Clima:** previsão ou clima típico do período\n"
            "- **💱 Câmbio:** conversão de reais para a moeda local\n\n"
            "**Depois do plano**\n"
            "- Use **💬 Tirar dúvidas** para ajustes, como *“e se fossem 3 pessoas?”* "
            "ou *“vai chover no segundo dia?”*. O agente lembra do que já foi feito.\n"
            "- Clique em **Nova conversa** para começar outra viagem do zero."
        )
        st.divider()
        st.markdown("### ⚙️ Configuração")
        provider = os.getenv("LLM_PROVIDER", "gemini").lower()
        model = os.getenv("LLM_MODEL") or DEFAULT_MODELS.get(provider, "?")
        st.markdown(f"**Modelo:** `{model}`")
        stored, limit = len(agent.memory), agent.memory.window_size
        st.progress(stored / limit, text=f"🧠 Memória: {stored} de {limit} trocas")

        if st.button("🗑️ Nova conversa", width="stretch"):
            agent.reset()
            create_agent.clear()  # recria o agente: garante que ele use a versão mais recente do código
            st.session_state.chat = []
            for key in ("result_planner", "result_budget", "result_weather", "result_currency"):
                st.session_state.pop(key, None)
            st.rerun()

        st.divider()
        st.markdown("### Como o agente pensa")
        st.markdown(
            "Ele segue o ciclo **ReAct**:\n"
            "1. **Pensa** no que falta para responder\n"
            "2. **Age** chamando uma ferramenta\n"
            "3. **Observa** o resultado\n"
            "4. Repete até ter a **resposta final**\n\n"
            "Abra *Ver raciocínio* em cada resposta para acompanhar os passos."
        )
        st.divider()
        st.caption(
            "Custos no destino e passagens são **estimados pelo modelo de linguagem** (podem variar e não são "
            "preços reais). Clima e câmbio vêm de APIs em tempo real; as contas são feitas pela calculadora."
        )


def main() -> None:
    st.set_page_config(page_title="Planejador de Viagens", page_icon="✈️", layout="centered")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    try:
        agent = create_agent()
    except ConfigError as exc:
        render_hero()
        st.error(f"Erro de configuração: {exc}")
        st.stop()

    if "chat" not in st.session_state:
        st.session_state.chat = []  # lista de (papel, AgentResponse | str)

    render_sidebar(agent)
    render_hero()

    planner, budget, weather, currency, chat = st.tabs(
        ["✈️ Planejar viagem", "🧮 Orçamento", "🌤️ Clima", "💱 Câmbio", "💬 Tirar dúvidas"]
    )
    with planner:
        tab_planner(agent)
    with budget:
        tab_budget(agent)
    with weather:
        tab_weather(agent)
    with currency:
        tab_currency(agent)
    with chat:  # por último: o chat_input dispara st.rerun()
        tab_chat(agent)


if __name__ == "__main__":
    main()
