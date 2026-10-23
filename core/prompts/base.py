"""The prompt template engine, and the two classes every prompt is built on.

A template is a `.md` file in a templates folder: core/prompts/templates/ for
the core's prompts, graphs/<id>/prompts/ for a graph's own. It's named by its
path there without the extension: "voice/speech_writer". Its text is the file,
minus the newline that ends it. Tags use double braces, so single braces, JSON
and dollar signs in a prompt are plain text:

    {{name}}                   a value
    {{#name}} ... {{/name}}    lines kept only when `name` is truthy
    {{^name}} ... {{/name}}    lines kept only when it isn't
    {{> voice/_speech_rules}}  another template of the same folder, inlined

A section tag stands alone on its line, and the line goes with it, so a section
leaves no blank line behind when it's dropped. A partial -- a template only
ever included by others -- has a name starting with `_`.

Rendering fails loudly on a name that has no value, even one only a dropped
section uses, so a template and its caller can't drift apart silently. Values
are inserted as they are and never scanned for tags themselves. Templates are
read on each render, so an edit takes effect without a restart.
"""
import dataclasses
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar, Dict, Optional, Set

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# The cheap, stateless worker agent that runs a WorkerPrompt.
WORKER_LOW = "graph-worker-low"

# What runs a WorkerPrompt: the runtime's `ask_worker` (core/runtime/delegation.py),
# plugged in by core/runtime/bootstrap.py, since prompts never import the runtime.
# Called as worker(text, channel=, caller=, timeout=, agent_id=); returns the
# run's DelegationResult, or None when it didn't complete.
Worker = Callable[..., Awaitable[Optional[Any]]]
_worker: Optional[Worker] = None


def set_worker(worker: Optional[Worker]) -> None:
    """Plugs in what `WorkerPrompt.run` hands its task to (core/runtime/bootstrap.py)."""
    global _worker
    _worker = worker


_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_PLACEHOLDER = re.compile(r"\{\{\s*(" + _NAME + r")\s*\}\}")
_SECTION_TAG = re.compile(r"\{\{([#^/])\s*(" + _NAME + r")\s*\}\}")
_INCLUDE = re.compile(r"\{\{>\s*([A-Za-z0-9_./-]+?)\s*\}\}")


def load_template(template: str, root: Path = TEMPLATES_DIR, /) -> str:
    """The text of `template` in the `root` folder, with every partial it includes inlined."""
    return _expand(root, template, ())


def placeholders(template: str, root: Path = TEMPLATES_DIR, /) -> Set[str]:
    """Every name `template` needs a value for: its values and its sections'."""
    return _names(load_template(template, root))


def render_template(template: str, root: Path = TEMPLATES_DIR, /, **values) -> str:
    """`template` in the `root` folder, filled in from `values`. Positional-only
    arguments, so a value may be called anything, `template` and `root`
    included. Values it doesn't use are ignored."""
    text = load_template(template, root)
    missing = sorted(_names(text) - set(values))
    if missing:
        raise KeyError(f"prompt '{template}' needs a value for: {', '.join(missing)}")
    text = _sections(text, values, template)
    return _PLACEHOLDER.sub(lambda match: str(values[match.group(1)]), text)


def _read(root: Path, template: str) -> str:
    text = (root / f"{template}.md").read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def _expand(root: Path, template: str, including: tuple) -> str:
    if template in including:
        raise ValueError(f"prompt include cycle: {' -> '.join(including + (template,))}")
    return _INCLUDE.sub(lambda match: _expand(root, match.group(1), including + (template,)), _read(root, template))


def _names(text: str) -> Set[str]:
    return {m.group(1) for m in _PLACEHOLDER.finditer(text)} | {m.group(2) for m in _SECTION_TAG.finditer(text)}


def _sections(text: str, values: Dict[str, Any], template: str) -> str:
    """The lines of `text` its sections keep, without the section tags' own lines."""
    kept, open_sections = [], []
    for line in text.split("\n"):
        tag = _SECTION_TAG.fullmatch(line.strip())
        if tag is None:
            if all(keep for _, keep in open_sections):
                kept.append(line)
            continue
        kind, name = tag.groups()
        if kind != "/":
            open_sections.append((name, bool(values[name]) == (kind == "#")))
        elif open_sections and open_sections[-1][0] == name:
            open_sections.pop()
        else:
            raise ValueError(f"prompt '{template}': {{{{/{name}}}}} closes no open section")
    if open_sections:
        raise ValueError(f"prompt '{template}': section '{open_sections[-1][0]}' is never closed")
    out = "\n".join(kept)
    stray = _SECTION_TAG.search(out)
    if stray:
        raise ValueError(f"prompt '{template}': {stray.group(0)} must stand alone on its line")
    return out


class Prompt:
    """One prompt: a template, and the inputs that fill it in.

    A subclass names its `template` and is a dataclass whose fields are its
    inputs. `render()` fills the template from `values()`: the fields as they
    are, unless the subclass derives what the template shows from them.

    The template is in `templates_dir`: core/prompts/templates/, unless the
    prompt belongs to a graph, which keeps its prompts in its own folder
    (graphs/<id>/prompts/) and goes when the graph goes.
    """

    template: ClassVar[str] = ""
    templates_dir: ClassVar[Path] = TEMPLATES_DIR

    def values(self) -> Dict[str, Any]:
        """What fills the template: the dataclass fields, by name."""
        if dataclasses.is_dataclass(self):
            return {field.name: getattr(self, field.name) for field in dataclasses.fields(self)}
        return {}

    def render(self) -> str:
        return render_template(self.template, self.templates_dir, **self.values())


class WorkerPrompt(Prompt):
    """A prompt that is a whole task, run as is by the stateless worker.

    `run()` renders it and hands it to `agent_id` (graph-worker-low) through
    the worker core/runtime/bootstrap.py plugs in, `ask_worker`
    (core/runtime/delegation.py): the path every delegation takes, recording
    nothing to memory and cut off after `timeout` seconds. It returns
    `parse(result)`, or None when the run didn't complete (it failed or ran out
    of time), which ask_worker has logged.

    A subclass sets `caller` -- the code asking, named in the logs and in the
    worker's <caller> line -- and `timeout`, and overrides `parse()` to read its
    answer out of the reply. parse() never returns None.
    """

    agent_id: ClassVar[str] = WORKER_LOW
    caller: ClassVar[str] = ""
    timeout: ClassVar[float] = 30.0

    def parse(self, result: Any) -> Any:
        """The answer in a completed run's DelegationResult: its text, unless a subclass reads more."""
        return result.text

    async def run(self, *, channel: str, timeout: Optional[float] = None) -> Any:
        """The worker's answer, asked from `channel`; None when the run didn't
        complete. `timeout` replaces the class's for a caller whose budget is configured."""
        if _worker is None:
            raise RuntimeError("No worker is plugged in: call core.runtime.bootstrap.wire() at startup.")
        result = await _worker(self.render(), channel=channel, caller=self.caller,
                               timeout=self.timeout if timeout is None else timeout, agent_id=self.agent_id)
        return None if result is None else self.parse(result)
