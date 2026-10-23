"""The prompt template engine and the two base classes (core/prompts/base.py)."""
import importlib
import pkgutil
import tempfile
import unittest
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import core.prompts
from core.prompts import base
from core.prompts.base import (
    TEMPLATES_DIR,
    WORKER_LOW,
    Prompt,
    WorkerPrompt,
    load_template,
    placeholders,
    render_template,
)


class _Templates:
    """Literal template texts in place of the files, by name, whatever the folder."""

    def __init__(self, test, **texts):
        patcher = patch.object(base, "_read", side_effect=lambda root, name: texts[name])
        patcher.start()
        test.addCleanup(patcher.stop)


class TestValues(unittest.TestCase):

    def test_values_are_filled_and_single_braces_are_text(self):
        _Templates(self, t='Hi {{ name }}. JSON stays: {"a": 1}. Price: $5. {{name}} again.')
        self.assertEqual(render_template("t", name="Daisy"), 'Hi Daisy. JSON stays: {"a": 1}. Price: $5. Daisy again.')

    def test_a_missing_value_fails_loudly(self):
        _Templates(self, t="{{name}} posted in #{{channel}}")
        with self.assertRaises(KeyError) as raised:
            render_template("t", name="Daisy")
        self.assertIn("channel", str(raised.exception))

    def test_values_are_never_scanned_for_tags(self):
        _Templates(self, t="<post>{{post}}</post>")
        self.assertEqual(render_template("t", post="see {{name}} {{#x}}"), "<post>see {{name}} {{#x}}</post>")

    def test_a_value_may_be_called_template(self):
        _Templates(self, t="{{template}}")
        self.assertEqual(render_template("t", template="x"), "x")

    def test_unused_values_are_ignored(self):
        _Templates(self, t="{{a}}")
        self.assertEqual(render_template("t", a=1, b=2), "1")


class TestSections(unittest.TestCase):

    TEXT = "before\n{{#extra}}\n\n--- EXTRA ---\n{{extra}}\n{{/extra}}\n{{^extra}}\nnothing extra\n{{/extra}}\nafter"

    def test_a_section_keeps_its_lines_only_when_its_value_is_truthy(self):
        _Templates(self, t=self.TEXT)
        self.assertEqual(render_template("t", extra="X"), "before\n\n--- EXTRA ---\nX\nafter")

    def test_an_inverted_section_keeps_them_only_when_it_is_not(self):
        _Templates(self, t=self.TEXT)
        for empty in ("", None, [], 0):
            with self.subTest(extra=empty):
                self.assertEqual(render_template("t", extra=empty), "before\nnothing extra\nafter")

    def test_dropped_trailing_sections_leave_no_blank_line(self):
        _Templates(self, t="- one\n{{#a}}\n- a\n{{/a}}\n{{#b}}\n- b\n{{/b}}")
        self.assertEqual(render_template("t", a=False, b=False), "- one")
        self.assertEqual(render_template("t", a=False, b=True), "- one\n- b")

    def test_sections_nest(self):
        _Templates(self, t="{{#a}}\na\n{{#b}}\nb\n{{/b}}\n{{/a}}")
        self.assertEqual(render_template("t", a=True, b=True), "a\nb")
        self.assertEqual(render_template("t", a=True, b=False), "a")
        self.assertEqual(render_template("t", a=False, b=True), "")

    def test_a_section_needs_its_value_even_when_dropped(self):
        _Templates(self, t="{{#extra}}\n{{extra}}\n{{/extra}}")
        with self.assertRaises(KeyError):
            render_template("t")

    def test_malformed_sections_fail_loudly(self):
        for text in ("{{#a}}\nx", "x\n{{/a}}", "{{#a}}\n{{#b}}\n{{/a}}\n{{/b}}", "inline {{#a}}x{{/a}}"):
            with self.subTest(text=text):
                _Templates(self, t=text)
                with self.assertRaises(ValueError):
                    render_template("t", a=True, b=True)


class TestPartials(unittest.TestCase):

    def test_a_partial_is_inlined_and_its_tags_belong_to_the_whole(self):
        _Templates(self, t="Rules:\n{{> _rules}}\nend", _rules="- be brief, {{name}}")
        self.assertEqual(load_template("t"), "Rules:\n- be brief, {{name}}\nend")
        self.assertEqual(placeholders("t"), {"name"})
        self.assertEqual(render_template("t", name="Daisy"), "Rules:\n- be brief, Daisy\nend")

    def test_an_include_cycle_fails_loudly(self):
        _Templates(self, a="{{> b}}", b="{{> a}}")
        with self.assertRaises(ValueError):
            load_template("a")

    def test_the_newline_that_ends_a_file_is_not_part_of_its_text(self):
        self.assertFalse(load_template("voice/_speech_rules").endswith("\n"))


class TestPrompt(unittest.TestCase):

    def test_a_dataclass_prompt_fills_its_template_from_its_fields(self):
        @dataclass
        class Greeting(Prompt):
            template = "t"
            name: str
            extra: str = ""

        _Templates(self, t="Hi {{name}}.\n{{#extra}}\n{{extra}}\n{{/extra}}")
        self.assertEqual(Greeting(name="Daisy").render(), "Hi Daisy.")
        self.assertEqual(Greeting(name="Daisy", extra="Bye.").render(), "Hi Daisy.\nBye.")

    def test_a_prompt_without_fields_renders_its_template_as_is(self):
        class Static(Prompt):
            template = "t"

        _Templates(self, t="Static {text}.")
        self.assertEqual(Static().render(), "Static {text}.")

    def test_a_prompt_may_keep_its_templates_in_its_own_folder(self):
        """How a graph keeps its prompts in graphs/<id>/prompts/: partials come from the same folder."""
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "coder.md").write_text("Task {{task}}.\n{{> _rules}}\n", encoding="utf-8")
            Path(folder, "_rules.md").write_text("- stay in the worktree\n", encoding="utf-8")

            @dataclass
            class GraphPrompt(Prompt):
                templates_dir = Path(folder)
                template = "coder"
                task: str

            self.assertEqual(GraphPrompt(task="T-1").render(), "Task T-1.\n- stay in the worktree")
            self.assertEqual(placeholders("coder", Path(folder)), {"task"})
            self.assertEqual(render_template("coder", Path(folder), task="T-2", root="a value"),
                             "Task T-2.\n- stay in the worktree")


@dataclass
class _Echo(WorkerPrompt):
    template = "t"
    caller = "echo-test"
    timeout = 3.0
    text: str


class TestWorkerPrompt(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        _Templates(self, t="Say {{text}}.")

    async def test_it_runs_on_graph_worker_low_through_ask_worker(self):
        ask = AsyncMock(return_value=SimpleNamespace(text="<payload>hi</payload>", response=None))
        with patch("core.prompts.base._worker", new=ask):
            self.assertEqual(await _Echo(text="hi").run(channel="general"), "<payload>hi</payload>")
        self.assertEqual(ask.await_args.args, ("Say hi.",))
        self.assertEqual(ask.await_args.kwargs, {"channel": "general", "caller": "echo-test", "timeout": 3.0,
                                                 "agent_id": WORKER_LOW})
        self.assertEqual(WORKER_LOW, "graph-worker-low")

    async def test_a_caller_may_replace_the_timeout(self):
        ask = AsyncMock(return_value=SimpleNamespace(text="", response=None))
        with patch("core.prompts.base._worker", new=ask):
            await _Echo(text="hi").run(channel="general", timeout=9.5)
        self.assertEqual(ask.await_args.kwargs["timeout"], 9.5)

    async def test_parse_reads_the_answer_and_a_run_that_did_not_complete_is_none(self):
        class Shout(_Echo):
            def parse(self, result):
                return result.text.upper()

        with patch("core.prompts.base._worker", new=AsyncMock(return_value=SimpleNamespace(text="hi"))):
            self.assertEqual(await Shout(text="hi").run(channel="general"), "HI")
        with patch("core.prompts.base._worker", new=AsyncMock(return_value=None)):
            self.assertIsNone(await Shout(text="hi").run(channel="general"))

    async def test_without_a_worker_it_says_how_to_plug_one_in(self):
        with patch("core.prompts.base._worker", new=None):
            with self.assertRaisesRegex(RuntimeError, "bootstrap.wire"):
                await _Echo(text="hi").run(channel="general")


REPO = Path(core.prompts.__file__).resolve().parents[2]
# The core's templates, and every graph's own prompts folder.
PROMPT_FOLDERS = [TEMPLATES_DIR] + sorted(p for p in (REPO / "graphs").glob("*/prompts") if p.is_dir())


def _prompt_packages():
    yield core.prompts
    for folder in PROMPT_FOLDERS[1:]:
        yield importlib.import_module(f"graphs.{folder.parent.name}.prompts")


def _prompt_classes():
    names = []
    for package in _prompt_packages():
        names.append(package.__name__)
        for module in pkgutil.iter_modules(package.__path__):
            importlib.import_module(f"{package.__name__}.{module.name}")
    found, todo = [], [Prompt]
    while todo:
        for cls in todo.pop().__subclasses__():
            todo.append(cls)
            if cls.template and any(cls.__module__.startswith(name + ".") for name in names):
                found.append(cls)
    return found


def _templates(folder, partial):
    return {path.relative_to(folder).with_suffix("").as_posix()
            for path in folder.rglob("*.md") if path.name.startswith("_") == partial}


class TestEveryTemplateHasItsPrompt(unittest.TestCase):
    """A template no class renders is dead text; a class without its template fails at runtime."""

    def test_the_graphs_keep_their_own_prompts(self):
        """A path slip in PROMPT_FOLDERS would make the checks below pass vacuously for the graphs."""
        self.assertLessEqual({"coding", "content_creation"}, {folder.parent.name for folder in PROMPT_FOLDERS[1:]})

    def test_each_template_is_rendered_by_exactly_one_prompt_class_of_its_folder(self):
        classes = _prompt_classes()
        for folder in PROMPT_FOLDERS:
            with self.subTest(folder=str(folder.relative_to(REPO))):
                rendered = Counter(cls.template for cls in classes if cls.templates_dir == folder)
                self.assertEqual(set(rendered), _templates(folder, partial=False))
                self.assertEqual([t for t, n in rendered.items() if n > 1], [])
        self.assertEqual([cls.__name__ for cls in classes if cls.templates_dir not in PROMPT_FOLDERS], [])

    def test_a_graphs_prompts_read_their_templates_from_the_graph(self):
        for cls in _prompt_classes():
            if cls.__module__.startswith("graphs."):
                with self.subTest(prompt=cls.__name__):
                    self.assertEqual(cls.templates_dir, REPO.joinpath(*cls.__module__.split(".")[:3]))

    def test_each_partial_is_included_by_a_template_of_its_folder(self):
        for folder in PROMPT_FOLDERS:
            sources = "".join(path.read_text(encoding="utf-8") for path in folder.rglob("*.md"))
            with self.subTest(folder=str(folder.relative_to(REPO))):
                self.assertEqual({p for p in _templates(folder, partial=True) if "{{> " + p + "}}" not in sources}, set())

    def test_the_worker_prompts_are_the_three_that_run_on_graph_worker_low(self):
        workers = {cls.__name__: cls for cls in _prompt_classes() if issubclass(cls, WorkerPrompt)}
        self.assertEqual(set(workers), {"SpeechWriterPrompt", "NearMissPrompt", "ContextSummaryPrompt"})
        for cls in workers.values():
            self.assertEqual(cls.agent_id, WORKER_LOW)
            self.assertTrue(cls.caller, cls.__name__)


if __name__ == "__main__":
    unittest.main()
