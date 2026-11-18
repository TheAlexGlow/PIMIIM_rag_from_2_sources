"""Streamlit host for the three MCP sources."""

import asyncio
import json
import re

import streamlit as st

from clinical_mcp.catalog import CATALOG, ENUM_LABELS
from clinical_mcp.client import RequestFailed, request
from clinical_mcp.forms import field_schema, sanitize


SOURCES = {"stdio": "Локальный · stdio", "sse": "Локальный · SSE", "pubmed": "Внешний · PubMed"}
PUBMED_DEMO = {"query": "hypertension systematic review[pt]", "maxResults": 5, "summaryCount": 5}


@st.cache_data(ttl=300, max_entries=2, show_spinner=False)
def remote_lookup(tool=None, encoded_arguments="{}"):
    return asyncio.run(request("pubmed", tool, json.loads(encoded_arguments)))


@st.cache_data(ttl=600, max_entries=64, show_spinner=False)
def remote_search(tool, encoded_arguments):
    result, trace = asyncio.run(request("pubmed", tool, json.loads(encoded_arguments)))
    if result.get("isError"):
        raise RequestFailed("PubMed вернул ошибку инструмента.", trace)
    return result, trace


def load_catalog(source):
    if source == "pubmed":
        return remote_lookup()
    return asyncio.run(request(source))


def render_field(name, field, value, key, required):
    title = field.get("title", name)
    kind = field.get("type")
    detail = field.get("description", "").strip()
    limits = ""
    if "x-min" in field:
        bracket = "(" if field.get("x-exclusive-min") else "["
        limits = f" Диапазон формы {bracket}{field['x-min']:g}; {field['x-max']:g}]."
    help_text = (detail + limits).strip() or None
    if detail and len(detail) < 15 and "x-min" in field:
        title = f"{title}, {detail}"
    if not required:
        title += " · необязательно"
    choices = field.get("enum")
    if choices:
        selected = choices.index(value) if value in choices else None
        return st.selectbox(title, choices, index=selected, key=key, help=help_text,
                            format_func=lambda item: ENUM_LABELS.get(name, {}).get(item, str(item)))
    if kind == "boolean":
        enabled = st.checkbox(title, value=value is True, key=key, help=help_text)
        return enabled if required or enabled or value is not None else None
    if kind in {"integer", "number"}:
        number = int if kind == "integer" else float
        # The server returns structured warnings for locally entered out-of-range values.
        return st.number_input(title, value=None if value is None else number(value),
                               step=number(field.get("x-step", 1 if kind == "integer" else 0.1)),
                               key=key, help=help_text)
    if kind in {"array", "object"}:
        return st.text_area(title, value=json.dumps(value, ensure_ascii=False) if value is not None else "",
                            key=key, help=help_text, placeholder="JSON")
    return st.text_input(title, value=str(value) if value is not None else "", key=key, help=help_text)


def render_calculation(data, title):
    if data.get("warning"):
        st.warning(data["warning"])
    elif data.get("value") is not None:
        st.metric(title, f"{data['value']:.4g} {data.get('unit', '')}")
        draw_status = {"red": st.error, "yellow": st.warning, "green": st.success}.get(data.get("severity"), st.info)
        draw_status(data.get("interpretation", "Результат расчёта"))
        st.table([{"Компонент": name, "Значение": str(value)} for name, value in data.get("components", {}).items()])
        st.write(data.get("guidance", ""))
    if data.get("source"):
        st.link_button("Открыть источник методики", data["source"])


def render_evidence(data, content):
    if "totalCount" in data:
        st.metric("Найдено записей в PubMed", data["totalCount"])
    if data.get("effectiveQuery"):
        st.caption(f"Запрос в PubMed · {data['effectiveQuery']}")
    summaries = data.get("summaries") or []
    if not isinstance(summaries, list):
        summaries = []
    for item in summaries:
        if not isinstance(item, dict):
            continue
        pmid = str(item.get("pmid", ""))
        st.write(item.get("title") or f"PMID {pmid}")
        if re.fullmatch(r"[0-9]+", pmid):
            st.link_button(f"PubMed · {pmid}", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
    if not summaries:
        for block in content:
            if block.get("type") == "text":
                st.text(block["text"])
        if not content and data:
            st.json(data)


def inspector(trace):
    with st.expander("Инспектор JSON-RPC", expanded=False):
        if not trace:
            st.caption("Пакетов пока нет.")
        methods = {}
        for event in trace:
            packet = event["packet"]
            identity = (event["attempt"], packet.get("id"))
            method = packet.get("method")
            if method and "id" in packet:
                methods[identity] = method
            label = method or methods.get(identity, "Ответ")
            st.caption(f"{event['direction']} {label} · попытка {event['attempt']}")
            st.json(packet, expanded=False)


def run():
    st.set_page_config(page_title="PIMIIM · Клинические инструменты MCP", page_icon="⚕️", layout="centered")
    st.title("Клинические инструменты MCP")
    st.caption("PIMIIM · Практическое задание 2 · учебные данные")
    source = st.radio("Источник", list(SOURCES), format_func=SOURCES.get, horizontal=True)
    if source == "pubmed":
        st.caption("Для поиска отправляйте только общую тему без сведений о пациенте. [PubMed MCP в Glama](https://glama.ai/mcp/connectors/io.github.cyanheads/pubmed-mcp-server)")

    catalog_key = f"catalog:{source}"
    if st.button("Обновить каталог"):
        st.session_state.pop(catalog_key, None)
        if source == "pubmed":
            remote_lookup.clear()
    if catalog_key not in st.session_state:
        try:
            with st.spinner("Получаем список инструментов MCP…"):
                st.session_state[catalog_key] = load_catalog(source)
        except RequestFailed as error:
            st.error(str(error))
            inspector(error.trace)
            return
    tools, catalog_trace = st.session_state[catalog_key]
    if not tools:
        st.warning("Подходящие инструменты не найдены.")
        inspector(catalog_trace)
        return

    tool_catalog = {item["name"]: item for item in tools}
    categories = sorted({CATALOG[name].category for name in tool_catalog if name in CATALOG})
    options = list(tool_catalog)
    if categories:
        category = st.selectbox("Специализация", ["Все", *categories], key=f"category:{source}")
        if category != "Все":
            options = [name for name in options if name in CATALOG and CATALOG[name].category == category]
    tool = st.selectbox("Инструмент · введите название или аббревиатуру", options, key=f"tool:{source}",
                        format_func=lambda name: tool_catalog[name].get("title") or name)
    definition = tool_catalog[tool]
    if definition.get("description"):
        st.caption(definition["description"])
    schema = definition.get("inputSchema", {})
    fields = schema.get("properties", {})
    examples = CATALOG[tool].example if tool in CATALOG else PUBMED_DEMO if source == "pubmed" else {}
    demo = st.toggle("Подставить учебный пример", key=f"demo:{source}:{tool}") if examples else False
    view = f"{source}:{tool}:{demo}"

    with st.form(f"form:{view}"):
        values = {}
        columns = st.columns(2) if len(fields) > 1 else [st.container()]
        for index, (name, raw_field) in enumerate(fields.items()):
            with columns[index % len(columns)]:
                field = field_schema(schema, raw_field)
                value = examples.get(name, field.get("default")) if demo else field.get("default")
                values[name] = render_field(name, field, value, f"field:{view}:{name}", name in schema.get("required", []))
        submitted = st.form_submit_button("Найти публикации" if source == "pubmed" else "Вызвать инструмент", type="primary")

    if submitted:
        st.session_state.pop("last_result", None)
        try:
            args, changes = sanitize(schema, values, examples if demo else None)
            if source == "pubmed" and not args.get("query"):
                raise ValueError("Введите общую тему поиска в поле query.")
            with st.spinner("Ожидаем ответ MCP…"):
                if source == "pubmed":
                    result, trace = remote_search(tool, json.dumps(args, sort_keys=True, ensure_ascii=False))
                else:
                    result, trace = asyncio.run(request(source, tool, args))
            st.session_state.last_result = (view, result, trace, changes)
        except ValueError as error:
            st.warning(str(error))
        except RequestFailed as error:
            st.error(str(error))
            inspector([*catalog_trace, *error.trace])
            return

    saved = st.session_state.get("last_result")
    call_trace = []
    if saved and saved[0] == view:
        _, result, call_trace, changes = saved
        if demo:
            st.caption("Учебный пример · результат получен для введённых демонстрационных значений.")
        if changes:
            st.caption("Подготовка параметров · " + " · ".join(changes))
        data = result.get("structuredContent") or {}
        content = result.get("content", [])
        if result.get("isError"):
            st.error("Инструмент отклонил параметры.")
            for item in content:
                if item.get("type") == "text":
                    st.text(item["text"])
        elif source == "pubmed":
            render_evidence(data, content)
        elif tool in CATALOG:
            render_calculation(data, CATALOG[tool].title)
        else:
            st.json(data or content)
    inspector([*catalog_trace, *call_trace])


run()
