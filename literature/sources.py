import re
from dataclasses import replace
from html.parser import HTMLParser
from threading import Lock
from time import monotonic, sleep
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

from .models import Article, SearchPlan, Source, SourceResult

NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CROSSREF = "https://api.crossref.org/journals/1741-7015/works"
EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
NCBI_LOCK = Lock()
NCBI_LAST_CALL = 0.0


class SourceError(Exception):
    pass


def clean(value: str) -> str:
    return " ".join(value.split())


class MarkupText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag.split(":")[-1] in {"p", "sec", "title", "br", "h3", "h4"}:
            self.parts.append(" ")


def markup_text(value: str) -> str:
    parser = MarkupText()
    parser.feed(value)
    parser.close()
    return clean("".join(parser.parts))


def xml_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return clean("".join(node.itertext()))


def year_of(value: object) -> int | None:
    match = re.match(r"^([12][0-9]{3})", str(value))
    return int(match[1]) if match else None


def normalize_doi(value: str) -> str:
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I)
    return value.lower() if re.fullmatch(r"10\.\d{4,9}/[^\s<>\"']+", value) else ""


def bmc_link(doi: str) -> str:
    return "https://link.springer.com/article/" + quote(doi, safe="/")


def request(url: str, params: dict, *, ncbi: bool = False) -> requests.Response:
    global NCBI_LAST_CALL
    for attempt in range(2):
        if ncbi:
            with NCBI_LOCK:
                sleep(max(0.0, 0.37 - (monotonic() - NCBI_LAST_CALL)))
                NCBI_LAST_CALL = monotonic()
        try:
            response = requests.get(
                url, params=params, timeout=(4, 12),
                headers={"User-Agent": "PIMIIM-Literature/1.0 (academic search)"},
            )
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 0:
                sleep(0.5)
                continue
            raise SourceError("Сервис не ответил вовремя или соединение недоступно.") from None
        except requests.RequestException:
            raise SourceError("Не удалось выполнить сетевой запрос.") from None

        if response.status_code in {429, 502, 503, 504} and attempt == 0:
            retry_after = response.headers.get("Retry-After", "")
            if retry_after and (not retry_after.isdigit() or int(retry_after) > 2):
                break
            sleep(max(0.5, int(retry_after or "0")))
            continue
        if response.ok:
            return response
        break

    raise SourceError(f"Внешний сервис вернул HTTP {response.status_code}.")


def pmc_articles(xml: bytes) -> tuple[Article, ...]:
    root = ET.fromstring(xml)
    if root.tag.lower() == "error" or root.find(".//ERROR") is not None:
        raise SourceError("NCBI не смог получить записи по найденным идентификаторам.")
    articles = []
    nodes = [root] if root.tag == "article" else root.findall("./article")
    for node in nodes:
        if node.get("article-type") in {"retraction", "correction", "expression-of-concern"}:
            continue
        front = node.find("./front")
        if front is None:
            continue
        meta = front.find("article-meta")
        if meta is None:
            continue

        ids = {item.get("pub-id-type"): xml_text(item) for item in meta.findall("article-id")}
        pmcid = ids.get("pmcid") or ids.get("pmc") or ids.get("pmcaid") or ""
        pmcid = pmcid.removeprefix("PMC")
        title = xml_text(meta.find("title-group/article-title"))
        if not title or not re.fullmatch(r"\d+", pmcid):
            continue
        pmcid = "PMC" + pmcid

        abstracts = []
        for abstract in meta.findall("abstract"):
            if abstract.get("abstract-type") not in {None, "", "abstract"}:
                continue
            parts = [xml_text(part) for part in abstract.findall(".//p")]
            abstracts.append(" ".join(filter(None, parts)) or xml_text(abstract))

        names = []
        for author in meta.findall("./contrib-group/contrib[@contrib-type='author']"):
            name = " ".join(filter(None, (
                xml_text(author.find("name/given-names")), xml_text(author.find("name/surname")),
            )))
            names.append(name or xml_text(author.find("collab")))

        dates = meta.findall("pub-date")
        dates.sort(key=lambda date: (
            date.get("date-type") != "pub" and date.get("pub-type") not in {"epub", "ppub"},
            date.get("publication-format") != "electronic" and date.get("pub-type") != "epub",
        ))
        years = [year_of(xml_text(date.find("year"))) for date in dates]
        journal = xml_text(front.find("journal-meta/journal-title-group/journal-title"))
        articles.append(Article(
            title, journal, next((year for year in years if year), None),
            ", ".join(filter(None, names)), clean(" ".join(abstracts)),
            normalize_doi(ids.get("doi") or ""), pmcid,
            {Source.PMC: f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"},
        ))
    return tuple(articles)


class PMCAgent:
    source = Source.PMC

    def __init__(self, email: str = "", api_key: str = ""):
        self.params = {"db": "pmc", "tool": "pimiim_literature"}
        if email:
            self.params["email"] = email
        if api_key:
            self.params["api_key"] = api_key

    def search(self, plan: SearchPlan) -> SourceResult:
        terms = " AND ".join(f"{term}[Title/Abstract]" for term in plan.terms)
        search = request(f"{NCBI}/esearch.fcgi", {
            **self.params, "retmode": "json", "retmax": plan.limit,
            "term": (f"({terms}) AND (cc by license[filter] OR cc0 license[filter]) "
                     "NOT (hasretractionin OR articletyperetraction)"),
            "datetype": "pdat", "mindate": f"{plan.start_year}/01/01",
            "maxdate": f"{plan.end_year}/12/31",
        }, ncbi=True).json()["esearchresult"]
        if search.get("ERROR"):
            raise SourceError("NCBI отклонил поисковый запрос.")
        ids = search["idlist"]
        if not ids:
            return SourceResult(self.source)
        xml = request(f"{NCBI}/efetch.fcgi", {
            **self.params, "retmode": "xml", "id": ",".join(ids),
        }, ncbi=True).content
        articles = pmc_articles(xml)
        if not articles:
            return SourceResult(self.source, note="PMC вернул идентификаторы, но подходящие записи JATS не извлечены.")
        return SourceResult(self.source, articles)


def crossref_article(row: dict) -> Article | None:
    doi = normalize_doi(row.get("DOI") or "")
    title = markup_text(next(iter(row.get("title") or []), ""))
    if not doi or not title:
        return None
    if any(item.get("type") == "retraction" for item in row.get("update-to", [])):
        return None

    dates = (row.get("published") or row.get("issued") or {}).get("date-parts") or [[]]
    year = year_of(next(iter(dates[0]), ""))
    authors = [
        clean(f"{item.get('given', '')} {item.get('family', '')}") or item.get("name", "")
        for item in row.get("author", [])
    ]
    return Article(
        title, str(Source.BMC), year, ", ".join(filter(None, authors)),
        markup_text(row.get("abstract") or ""), doi,
        links={Source.BMC: bmc_link(doi)},
    )


def europe_rows(query: str, page_size: int) -> list[dict]:
    payload = request(EUROPE_PMC, {
        "query": query, "format": "json", "resultType": "core", "pageSize": page_size,
    }).json()
    return payload["resultList"].get("result", [])


def europe_article(row: dict) -> Article | None:
    doi = normalize_doi(row.get("doi") or "")
    title = markup_text(row.get("title") or "")
    journal = row.get("journalInfo", {}).get("journal", {})
    journal_names = [row.get("journalTitle") or "", journal.get("title") or "",
                     journal.get("medlineAbbreviation") or ""]
    issns = {row.get("journalIssn"), journal.get("issn"), journal.get("essn")}
    names = {name.casefold() for name in journal_names}
    if not doi or not title or not ("1741-7015" in issns or names & {"bmc medicine", "bmc med"}):
        return None
    pub_types = row.get("pubTypeList", {}).get("pubType", [])
    if any(item.casefold() in {"retracted publication", "retraction of publication", "published erratum"}
           for item in pub_types):
        return None
    return Article(
        title, str(Source.BMC), year_of(row.get("pubYear")),
        row.get("authorString") or "", markup_text(row.get("abstractText") or ""), doi,
        row.get("pmcid") or "", {Source.BMC: bmc_link(doi)},
    )


class BMCAgent:
    source = Source.BMC

    def __init__(self, email: str = ""):
        self.email = email

    def search(self, plan: SearchPlan) -> SourceResult:
        try:
            records = self.from_crossref(plan)
        except (SourceError, ValueError, KeyError, TypeError):
            terms = " AND ".join(f"TITLE_ABS:{term}" for term in plan.terms)
            query = (f'(JOURNAL:"BMC Medicine" OR JOURNAL:"BMC Med") AND ({terms}) AND '
                     f"FIRST_PDATE:[{plan.start_year}-01-01 TO {plan.end_year}-12-31]")
            records = tuple(filter(None, map(europe_article, europe_rows(query, plan.limit))))
            return SourceResult(self.source, records, note="Crossref недоступен. Выполнен поиск в Europe PMC по журналу BMC Medicine.")

        missing = [article for article in records if not article.abstract]
        if not missing:
            return SourceResult(self.source, records, note="Метаданные журнала получены из Crossref.")
        query = " OR ".join(f'DOI:"{article.doi}"' for article in missing)
        try:
            enriched = {}
            for row in europe_rows(f"({query})", min(100, 3 * len(missing))):
                article = europe_article(row)
                if article and article.abstract:
                    enriched[article.doi] = article
        except (SourceError, ValueError, KeyError, TypeError, AttributeError):
            return SourceResult(self.source, records, note="Получены записи Crossref. Дозагрузка аннотаций из Europe PMC недоступна.")

        articles = tuple(
            replace(article, abstract=enriched[article.doi].abstract, pmcid=enriched[article.doi].pmcid)
            if article.doi in enriched else article
            for article in records
        )
        return SourceResult(self.source, articles, note="Записи Crossref дополнены доступными аннотациями из Europe PMC.")

    def from_crossref(self, plan: SearchPlan) -> tuple[Article, ...]:
        params = {
            "query.bibliographic": " ".join(plan.terms),
            "filter": f"from-pub-date:{plan.start_year}-01-01,until-pub-date:{plan.end_year}-12-31,type:journal-article",
            "rows": plan.limit,
        }
        if self.email:
            params["mailto"] = self.email
        items = request(CROSSREF, params).json()["message"]["items"]
        return tuple(filter(None, map(crossref_article, items)))
