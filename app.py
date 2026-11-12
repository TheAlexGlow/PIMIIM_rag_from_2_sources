import json
import os
from dataclasses import asdict
from datetime import date

import streamlit as st

from literature.models import Report, Source
from literature.pipeline import SynthesisAgent, make_plan, run_pipeline
from literature.sources import BMCAgent, PMCAgent

DEMO_KEYWORDS = "sleep duration hypertension"


def setting(name: str, default: str = "") -> str:
    if value := os.environ.get(name):
        return value
    try:
        return str(st.secrets.get(name, default))
    except FileNotFoundError:
        return default


def article_label(article) -> str:
    return " · ".join(part for part in (
        article.journal, str(article.year) if article.year else "", article.authors,
    ) if part)


def article_links(article):
    columns = st.columns(len(article.links))
    for column, (source, url) in zip(columns, article.links.items()):
        column.link_button(str(source), url, use_container_width=True)


def render_report(report: Report):
    st.divider()
    st.subheader("Результаты поиска")
    st.caption(f"{report.plan.keywords} · {report.plan.start_year}–{report.plan.end_year}")

    metrics = st.columns(len(report.sources) + 1)
    for column, result in zip(metrics, report.sources):
        column.metric(f"Получено из {result.source}", "—" if result.error else len(result.articles))
        if result.error:
            column.warning(result.error)
    metrics[-1].metric("Уникальных после проверки", len(report.articles))

    overview_tab, articles_tab, trace_tab = st.tabs(["Сводка", "Публикации", "Журнал агентов"])
    with overview_tab:
        if not report.findings:
            if all(source.error for source in report.sources):
                st.info("Источники сейчас недоступны. Повторите поиск позже.")
            elif not report.articles:
                st.info("Подходящих записей нет. Попробуйте сократить набор ключевых слов или расширить период.")
            else:
                st.info("Для найденных записей нет аннотаций, пригодных для извлечения фрагментов.")
        else:
            st.caption("Сводка по аннотациям. Для интерпретации результатов откройте исходные статьи.")
            by_id = {item.id: item for item in report.evidence}
            for finding in report.findings:
                article = by_id[finding.evidence_id].article
                with st.container(border=True):
                    st.caption(f"[{finding.evidence_id}] {article_label(article)}")
                    st.text(article.title)
                    if finding.statement:
                        st.text(finding.statement)
                    st.text(finding.quote)
                    article_links(article)

    with articles_tab:
        if not report.articles:
            st.caption("Найденные публикации появятся здесь.")
        for article in report.articles:
            with st.expander(article.title):
                st.caption(article_label(article))
                if article.doi:
                    st.caption(f"DOI {article.doi}")
                if article.pmcid:
                    st.caption(article.pmcid)
                st.text(article.abstract or "Аннотация в полученных метаданных отсутствует.")
                article_links(article)

    with trace_tab:
        for event in report.events:
            agent, duration = st.columns([4, 1])
            agent.markdown(f"**{event.agent}**")
            duration.caption(f"{event.seconds:.2f} с")
            st.text(event.detail)
        for result in report.sources:
            if result.note:
                st.caption(f"{result.source} · {result.note}")
        st.download_button(
            "Скачать результаты JSON",
            data=json.dumps(asdict(report), ensure_ascii=False, indent=2),
            file_name="literature-report.json",
            mime="application/json",
        )


def main():
    st.set_page_config(page_title="Поиск медицинских публикаций", layout="centered")
    st.title("Поиск медицинских публикаций")
    st.caption("PubMed Central и BMC Medicine · поиск, проверка релевантности и сводка по аннотациям")

    api_key = setting("OPENAI_API_KEY")
    this_year = date.today().year
    with st.form("literature-search"):
        keywords = st.text_input(
            "Ключевые слова на английском", value=DEMO_KEYWORDS, max_chars=220,
            help="Введите до 12 значимых слов через пробел. Валидатор ищет их в названии или аннотации.",
        )
        question = st.text_input(
            "Вопрос для сводки на русском (необязательно)", max_chars=600, disabled=not api_key,
            help="Доступно при подключении OpenAI. Поиск всегда использует английские ключевые слова.",
        )
        left, middle, right = st.columns(3)
        start = left.number_input("Год с", 1900, this_year, 2018)
        end = middle.number_input("Год по", 1900, this_year, this_year)
        limit = right.number_input("Записей на источник", 1, 20, 10)
        sources = st.multiselect("Источники", list(Source), default=list(Source), format_func=str)
        use_model = st.checkbox("Сформировать сводку с OpenAI", disabled=not api_key)
        if api_key:
            st.caption("В режиме OpenAI вопрос и выбранные фрагменты передаются модели.")
        else:
            st.caption("Без ключа OpenAI сводка показывает релевантные фрагменты в оригинале.")
        submitted = st.form_submit_button("Найти публикации", type="primary")

    if submitted:
        try:
            plan = make_plan(question if use_model else "", keywords, start, end, limit, tuple(sources))
        except ValueError as error:
            st.error(str(error))
        else:
            email = setting("LITERATURE_CONTACT_EMAIL")
            agents = {
                Source.PMC: PMCAgent(email, setting("NCBI_API_KEY")),
                Source.BMC: BMCAgent(email),
            }
            model = setting("OPENAI_MODEL", "gpt-4.1-mini")
            synthesis = SynthesisAgent(api_key, model) if use_model and api_key else None
            with st.status("Выполняется поиск", expanded=False) as status:
                report = run_pipeline(plan, agents, synthesis=synthesis, on_event=lambda event: st.text(
                    f"{event.agent} · {event.detail}"
                ))
                st.session_state["literature_report"] = report
                status.update(label="Поиск завершён", state="complete", expanded=False)

    if report := st.session_state.get("literature_report"):
        render_report(report)


main()
