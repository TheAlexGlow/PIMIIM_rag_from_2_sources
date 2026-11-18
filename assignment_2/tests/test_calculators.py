import math
import unittest

from pydantic import TypeAdapter, ValidationError

from clinical_mcp.calculators import Age, bmi, body_surface_area, egfr_ckd_epi, glasgow_coma_scale, mean_arterial_pressure
from clinical_mcp.forms import sanitize


class CalculatorTests(unittest.TestCase):
    def test_synthetic_examples(self):
        self.assertAlmostEqual(bmi(72, 175).value, 23.51020408)
        self.assertEqual(bmi(100, 200).severity, "yellow")
        self.assertAlmostEqual(body_surface_area(72, 175).value, math.sqrt(3.5))
        self.assertEqual(glasgow_coma_scale("sound", "confused", "obeys", True).value, 13)
        self.assertEqual(glasgow_coma_scale("none", "none", "none", True).severity, "red")
        self.assertAlmostEqual(mean_arterial_pressure(120, 80).value, 93.33333333)
        self.assertAlmostEqual(egfr_ckd_epi(55, "female", 0.9).value, 75.49880473)

    def test_range_warnings_have_no_value(self):
        for result in [bmi(72, 0), bmi(math.inf, 175), mean_arterial_pressure(80, 120),
                       egfr_ckd_epi(55, "female", 0), glasgow_coma_scale("sound", "confused", "obeys", False)]:
            self.assertIsNone(result.value)
            self.assertTrue(result.warning)
        self.assertEqual(TypeAdapter(Age).json_schema()["x-min"], 18)
        with self.assertRaises(ValidationError):
            TypeAdapter(Age).validate_python("55")


class SanitizerTests(unittest.TestCase):
    schema = {"properties": {"age": {"type": "integer"}, "x": {"anyOf": [{"type": "number"}, {"type": "null"}]}},
              "required": ["age"]}

    def test_coercion(self):
        values, changes = sanitize(self.schema, {"age": "55", "x": "0,9"})
        self.assertEqual(values, {"age": 55, "x": 0.9})
        self.assertEqual(len(changes), 2)

    def test_explicit_demo_defaults_and_invalid_data(self):
        values, changes = sanitize(self.schema, {}, {"age": 55})
        self.assertEqual(values, {"age": 55})
        self.assertEqual(len(changes), 1)
        for invalid in ({}, {"age": "55.1"}, {"age": False}, {"age": "nan"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                sanitize(self.schema, invalid)
