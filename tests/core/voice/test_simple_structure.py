"""Tests for the deterministic fast path: which layouts are spoken by rule,
and which are declined so the Verbalizer sends them to the model."""
import pytest

from core.voice.simple_structure import render_simple_structure
from core.voice.tts_engine import TextSanitizer


@pytest.mark.parametrize("text,expected", [
    ("- eggs\n- milk", "eggs. milk."),
    ("* eggs\n+ milk", "eggs. milk."),
    ("1. Book the table\n2. Call Sam", "First, Book the table. Second, Call Sam."),
    ("3) Pay rent", "Third, Pay rent."),
    ("## Plan\nWe leave at nine.", "Plan. We leave at nine."),
    ("# Weekend #", "Weekend."),
    ("This is **important** to note.", "This is important to note."),
    ("Use `git status`, then read [the docs](https://example.com).",
     "Use git status, then read the docs."),
    ("Here is the list:\n- eggs\n- milk", "Here is the list: eggs. milk."),
    ("> Be kind.", "Be kind."),
    ("Intro.\n\n---\n\nOutro.", "Intro. Outro."),
    ("| Day | Forecast |\n| --- | --- |\n| Monday | Sunny |\n| Tuesday | Rain |",
     "Monday: Sunny. Tuesday: Rain."),
])
def test_render_simple_structure_speaks_simple_layouts(text, expected):
    assert TextSanitizer.sanitize(render_simple_structure(text)) == expected


@pytest.mark.parametrize("text", [
    "```python\nprint(1)\n```",                                   # code
    "- eggs\n  - free range\n- milk",                             # nested list
    "- eggs\n\t- free range",                                     # nested (tab)
    "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |",           # wide table
    "| A | B |\n| 1 | 2 |",                                       # no separator row
    "| K | V |\n| --- | --- |\n" + "| k | v |\n" * 9,             # long table
    "\n".join(f"- item {i}" for i in range(11)),                   # long list
    "11. eleventh step",                                           # beyond ordinals
    "- [ ] buy milk\n- [x] call Sam",                              # checklist
    "- " + "word " * 40,                                           # long item
    "- eggs\n  still about eggs",                                  # item continuation
    "The ~~old~~ new plan.",                                       # strikethrough
    "![chart](chart.png)",                                         # image
    "> - quoted bullet",                                           # structure in quote
])
def test_render_simple_structure_declines_complex_layouts(text):
    assert render_simple_structure(text) is None
