import unittest

from agents.meal_planner.lib.quantity import (
    PORTION, deduct, fmt, has_unit, parse_amount, parse_terms, portions, render, set_portions,
)


class TestParsing(unittest.TestCase):

    def test_terms_with_units_and_parentheses(self):
        terms = parse_terms("1 盒 (2 份)")
        self.assertEqual([(t.value, t.unit, t.in_parens) for t in terms], [(1, "盒", False), (2, "份", True)])

    def test_prefix_words_and_english_units(self):
        terms = parse_terms("約 1.5 lb (1 份)")
        self.assertEqual([(t.value, t.unit) for t in terms], [(1.5, "lb"), (1, "份")])

    def test_fractions(self):
        self.assertEqual(parse_terms("3/4 盒")[0].value, 0.75)

    def test_a_parenthesis_without_a_number_is_not_a_term(self):
        self.assertEqual([t.unit for t in parse_terms("1 袋 (大包)")], ["袋"])

    def test_portions(self):
        self.assertEqual(portions("2 包 (1 份)"), 1)
        self.assertEqual(portions("0.5 份"), 0.5)
        self.assertIsNone(portions("1 袋"))

    def test_english_portion_words_count_as_portions(self):
        self.assertTrue(has_unit("2 份", "portions"))

    def test_parse_amount(self):
        self.assertEqual(parse_amount("4 顆"), (4, "顆"))
        self.assertEqual(parse_amount("0.5"), (0.5, PORTION))
        with self.assertRaises(ValueError):
            parse_amount("some")

    def test_fmt(self):
        self.assertEqual([fmt(1.0), fmt(0.5), fmt(0.75), fmt(2 / 3), fmt(0.1 + 0.2)], ["1", "0.5", "0.75", "0.67", "0.3"])


class TestDeduct(unittest.TestCase):

    def test_a_portion_comes_off(self):
        result = deduct("2 份", 1)
        self.assertEqual((result.text, result.taken, result.left), ("1 份", 1, 1))

    def test_the_restating_term_scales_along(self):
        self.assertEqual(deduct("1 盒 (2 份)", 1).text, "0.5 盒 (1 份)")
        self.assertEqual(deduct("1.5 把 (1.5 份)", 0.5).text, "1 把 (1 份)")
        self.assertEqual(deduct("約 1.5 lb (1 份)", 0.5).text, "約 0.75 lb (0.5 份)")

    def test_deducting_the_outer_unit_scales_the_inner_one(self):
        self.assertEqual(deduct("24 顆 (2 打)", 4, "顆").text, "20 顆 (1.67 打)")

    def test_amounts_joined_by_plus_are_separate(self):
        self.assertEqual(deduct("1 袋 + 0.8 lb", 0.3, "lb").text, "1 袋 + 0.5 lb")

    def test_reaching_zero_uses_the_row_up(self):
        result = deduct("0.5 份", 1)
        self.assertEqual((result.text, result.taken, result.left), (None, 0.5, 0))
        self.assertIsNone(deduct("2 包 (1 份)", 1).text)

    def test_float_noise_still_reaches_zero(self):
        self.assertIsNone(deduct(deduct("0.3 份", 0.1).text, 0.2).text)

    def test_a_row_not_counted_in_the_unit_is_refused(self):
        with self.assertRaises(ValueError):
            deduct("1 袋", 1)


class TestRender(unittest.TestCase):

    def test_new_row_quantities(self):
        self.assertEqual(render("1 盒", 2, 2.0), "1 盒 (2 份)")
        self.assertEqual(render(None, 0.5, 2.0), "0.5 份")
        self.assertEqual(render("12 顆", None, 2.0), "12 顆")
        self.assertEqual(render(None, None, 2.0), "2 份")

    def test_with_neither_an_amount_nor_portions_the_default_applies(self):
        self.assertEqual(render(None, None, 1.5), "1.5 份")
        self.assertEqual(render("", None, 3), "3 份")

    def test_a_quantity_that_already_counts_portions_is_kept(self):
        self.assertEqual(render("3 份", 3, 2.0), "3 份")

    def test_set_portions(self):
        self.assertEqual(set_portions("1 盒 (2 份)", 1), "0.5 盒 (1 份)")
        self.assertEqual(set_portions("1 袋", 1), "1 袋 (1 份)")
        self.assertEqual(set_portions("", 2), "2 份")


if __name__ == "__main__":
    unittest.main()
