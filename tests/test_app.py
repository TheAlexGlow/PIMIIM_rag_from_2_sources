import json
import unittest
from threading import Barrier
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from literature.models import Article, Evidence, Source, SourceResult
from literature.pipeline import SynthesisAgent, checked_findings, make_plan, run_pipeline
from literature.sources import BMCAgent, PMCAgent, SourceError, pmc_articles

TITLE = "Sleep duration and hypertension in a population cohort"
ABSTRACT = (
    "This cohort studied sleep duration and hypertension in adults. "
    "Results: Short sleep duration was associated with hypertension in this sample."
)
DOI = "10.1186/s12916-024-00001-0"
JATS = f"""<pmc-articleset><article article-type="research-article"><front>
<journal-meta><journal-title-group><journal-title>BMC Medicine</journal-title></journal-title-group></journal-meta>
<article-meta><article-id pub-id-type="pmc">1234</article-id>
<article-id pub-id-type="doi">{DOI.upper()}</article-id>
<title-group><article-title>{TITLE}</article-title></title-group>
<pub-date publication-format="electronic" date-type="pub"><year>2024</year></pub-date>
<abstract><p>{ABSTRACT}</p></abstract>
</article-meta></front></article></pmc-articleset>""".encode()


def api_response(payload=None, body=b""):
    result = Mock(content=body)
    result.json.return_value = payload
    return result


def example_plan(sources=tuple(Source)):
    return make_plan("", "sleep duration hypertension", 2020, 2025, 10, sources)


class SourceTests(unittest.TestCase):
    def test_pmc_esearch_then_jats(self):
        with patch("literature.sources.request", side_effect=[
            api_response({"esearchresult": {"idlist": ["1234"]}}), api_response(body=JATS),
        ]) as request:
            result = PMCAgent().search(example_plan())
        self.assertEqual(result.articles[0].pmcid, "PMC1234")
        self.assertEqual(result.articles[0].doi, DOI)
        self.assertEqual(result.articles[0].abstract, ABSTRACT)
        self.assertIn("cc by license[filter]", request.call_args_list[0].args[1]["term"])
        self.assertEqual(request.call_args_list[1].args[1]["id"], "1234")

    def test_pmc_empty_search_skips_efetch(self):
        with patch("literature.sources.request", return_value=api_response({
            "esearchresult": {"idlist": []},
        })) as request:
            self.assertFalse(PMCAgent().search(example_plan()).articles)
        request.assert_called_once()

    def test_bmc_reads_crossref_and_enriches_by_doi(self):
        rows = [{"DOI": DOI, "title": [TITLE], "published": {"date-parts": [[2024]]}}]
        europe = [{
            "doi": DOI, "title": TITLE, "abstractText": f"<p>{ABSTRACT}</p>",
            "pmcid": "PMC1234", "journalInfo": {"journal": {"essn": "1741-7015"}},
        }]
        with patch("literature.sources.request", side_effect=[
            api_response({"message": {"items": rows}}),
            api_response({"resultList": {"result": europe}}),
        ]) as request:
            result = BMCAgent().search(example_plan())
        self.assertIn("1741-7015", request.call_args_list[0].args[0])
        self.assertIn(f'DOI:"{DOI}"', request.call_args_list[1].args[1]["query"])
        article = result.articles[0]
        self.assertEqual(article.abstract, ABSTRACT)
        self.assertEqual(article.pmcid, "PMC1234")
        self.assertIn("link.springer.com", article.links[Source.BMC])

    def test_bmc_uses_only_target_journal_in_fallback(self):
        with patch("literature.sources.request", side_effect=[
            SourceError("unavailable"),
            api_response({"resultList": {"result": [
                {"doi": DOI, "title": TITLE, "journalTitle": "BMC Medicine", "pubYear": "2024"},
                {"doi": "10.1000/other", "title": TITLE, "journalTitle": "BMC Public Health"},
            ]}}),
        ]) as request:
            result = BMCAgent().search(example_plan())
        self.assertEqual(len(result.articles), 1)
        self.assertIn('JOURNAL:"BMC Medicine"', request.call_args.args[1]["query"])


class PipelineTests(unittest.TestCase):
    def test_parallel_search_validation_and_deduplication(self):
        original = pmc_articles(JATS)[0]
        bmc_copy = Article("Sleep duration in a cohort", "BMC Medicine", 2024, "", "", DOI,
                           links={Source.BMC: "https://link.springer.com/article/" + DOI})
        irrelevant = Article("Sleep duration in a cohort", "BMC Medicine", 2024, "", "",
                             "10.1000/other", links=bmc_copy.links)
        barrier = Barrier(2, timeout=3)

        def search(source, *articles):
            def result(plan):
                barrier.wait()
                return SourceResult(source, articles)
            return Mock(source=source, search=result)

        agents = {
            Source.PMC: search(Source.PMC, original),
            Source.BMC: search(Source.BMC, bmc_copy, irrelevant),
        }
        report = run_pipeline(example_plan(), agents)
        self.assertEqual(len(report.articles), 1)
        self.assertEqual(set(report.articles[0].links), set(Source))
        self.assertEqual(len(report.findings), 1)
        self.assertIn(report.findings[0].quote, original.abstract)
        self.assertIn("2/3", report.events[3].detail)

    def test_failure_does_not_remove_other_source_results(self):
        article = pmc_articles(JATS)[0]
        agents = {
            Source.PMC: Mock(source=Source.PMC, search=Mock(return_value=SourceResult(Source.PMC, (article,)))),
            Source.BMC: Mock(source=Source.BMC, search=Mock(side_effect=SourceError("unavailable"))),
        }
        report = run_pipeline(example_plan(), agents)
        self.assertEqual(report.articles, (article,))
        self.assertTrue(report.sources[1].error)

    def test_router_only_calls_selected_source(self):
        agent = Mock(search=Mock(return_value=SourceResult(Source.PMC)))
        report = run_pipeline(example_plan((Source.PMC,)), {Source.PMC: agent})
        self.assertEqual(len(report.sources), 1)
        agent.search.assert_called_once()

    def test_citation_validation_rejects_invented_quote(self):
        evidence = (Evidence("E1", pmc_articles(JATS)[0], ABSTRACT),)
        with self.assertRaises(ValueError):
            checked_findings({"findings": [
                {"statement": "Вывод", "evidence_id": "E1", "quote": "A sentence that is not in the abstract."},
            ]}, evidence)

    def test_synthesizer_retries_invalid_citation(self):
        evidence = (Evidence("E1", pmc_articles(JATS)[0], ABSTRACT),)
        bad = {"findings": [{"statement": "Вывод", "evidence_id": "E2", "quote": ABSTRACT}]}
        good = {"findings": [{"statement": "Вывод", "evidence_id": "E1", "quote": ABSTRACT}]}

        def reply(payload):
            return api_response({"choices": [{"message": {"content": json.dumps(payload)}}]})

        with patch("literature.pipeline.requests.post", side_effect=[reply(bad), reply(good)]) as post:
            findings, _ = SynthesisAgent("test-key", "test-model").synthesize(example_plan(), evidence)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(findings[0].statement, "Вывод")
        self.assertEqual(findings[0].evidence_id, "E1")

    def test_keyword_validation(self):
        with self.assertRaises(ValueError):
            make_plan("", "гипертония", 2020, 2025, 10, tuple(Source))
        with self.assertRaises(ValueError):
            make_plan("", "hypertension", 2025, 2020, 10, tuple(Source))


class StreamlitTests(unittest.TestCase):
    def test_form_submission_renders_results_and_trace(self):
        pmc = pmc_articles(JATS)[0]
        bmc = Article(TITLE, "BMC Medicine", 2024, "", ABSTRACT, DOI,
                      links={Source.BMC: "https://link.springer.com/article/" + DOI})
        with patch.object(PMCAgent, "search", return_value=SourceResult(Source.PMC, (pmc,))), \
             patch.object(BMCAgent, "search", return_value=SourceResult(Source.BMC, (bmc,))):
            app = AppTest.from_file("app.py").run()
            self.assertFalse(app.exception)
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual([tab.label for tab in app.tabs], ["Сводка", "Публикации", "Журнал агентов"])
            self.assertEqual([metric.value for metric in app.metric], ["1", "1", "1"])
            self.assertEqual(app.expander[0].label, TITLE)
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[2].value, "1")


if __name__ == "__main__":
    unittest.main()
