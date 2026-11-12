import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from literature.bergamot import BergamotTranslator, TranslationError
from literature.models import Source, SourceResult
from literature.pipeline import make_plan
from literature.query_translation import QueryFormatVerifier, review_translation
from literature.sources import BMCAgent, PMCAgent


class VerificationTests(unittest.TestCase):
    def test_accepts_equivalent_round_trip(self):
        translator = Mock(return_value=None)
        translator.translate.return_value = ("Asthma in children.", "Астма у детей.")
        result = review_translation("астма у детей", translator)
        self.assertTrue(result.approved)
        self.assertEqual(len(result.checks), 2)

    def test_checks_numbers_identifiers_and_negation(self):
        verifier = QueryFormatVerifier()
        original = "HbA1c без метформина при диабете 2 типа"
        for candidate in (
            "HbA1c without metformin in type 1 diabetes",
            "Without metformin in type 2 diabetes",
            "HbA1c with metformin in type 2 diabetes",
        ):
            with self.subTest(candidate=candidate):
                self.assertFalse(verifier.verify(original, candidate)[0])
        self.assertTrue(verifier.verify(original, "HbA1c without metformin in type 2 diabetes")[0])

    def test_checks_that_round_trip_retains_topic(self):
        translator = Mock()
        translator.translate.return_value = ("Skin diseases in adults.", "Кожные заболевания у взрослых.")
        review = review_translation("астма у детей", translator)
        self.assertTrue(review.checks[0].passed)
        self.assertFalse(review.checks[1].passed)
        self.assertFalse(review.approved)

    def test_verified_numbers_reach_the_search_plan(self):
        plan = make_plan("", "HbA1c 6,5 type 2 diabetes", 2020, 2026, 5, (Source.PMC,))
        self.assertEqual(plan.terms, ("hba1c", "6.5", "type", "2", "diabetes"))

    def test_failed_model_leaves_manual_option(self):
        translator = Mock()
        translator.translate.side_effect = TranslationError("Model unavailable")
        review = review_translation("астма у детей", translator)
        self.assertFalse(review.approved)
        self.assertEqual(review.english, "")
        self.assertIn("Model unavailable", review.checks[0].detail)


class TranslationStreamlitTests(unittest.TestCase):
    def run_query(self):
        app = AppTest.from_file("app.py").run()
        app.text_input[0].set_value("астма у детей")
        return app.button[0].click().run()

    def test_verified_query_goes_to_both_agents(self):
        with patch.object(BergamotTranslator, "translate", return_value=("Asthma in children", "Астма у детей")), \
             patch.object(PMCAgent, "search", return_value=SourceResult(Source.PMC)) as pmc, \
             patch.object(BMCAgent, "search", return_value=SourceResult(Source.BMC)) as bmc:
            app = self.run_query()
        self.assertFalse(app.exception)
        self.assertEqual(pmc.call_args.args[0].terms, ("asthma", "children"))
        self.assertEqual(bmc.call_args.args[0].terms, ("asthma", "children"))
        report = app.session_state["literature_report"]
        self.assertTrue(report.translation.approved)
        self.assertEqual(report.events[1].agent, "Верификатор формата")

    def test_suspect_query_waits_for_manual_confirmation(self):
        with patch.object(BergamotTranslator, "translate", return_value=("Heart disease", "Болезни сердца")), \
             patch.object(PMCAgent, "search", return_value=SourceResult(Source.PMC)) as pmc, \
             patch.object(BMCAgent, "search", return_value=SourceResult(Source.BMC)) as bmc:
            app = self.run_query()
            self.assertFalse(app.exception)
            pmc.assert_not_called()
            bmc.assert_not_called()
            app.text_input[2].set_value("asthma children")
            app.button[1].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(pmc.call_args.args[0].terms, ("asthma", "children"))
        self.assertEqual(app.session_state["literature_report"].translation.override, "asthma children")


if __name__ == "__main__":
    unittest.main()
