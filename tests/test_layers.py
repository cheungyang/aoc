"""The dependency rules in core/runtime/__init__.py, checked against every import in core/.

  R1  The runtime never imports an adapter.
  R2  Inside the runtime, imports point down the layers.
  R3  The packages the runtime builds on import nothing above them: no
      adapter, and from the runtime only core.runtime.foundation.
  R4  A package __init__.py has no imports.
  R5  The runtime imports no project module inside a function.

Today's violations are listed in ALLOWED, with what removes them. A new
violation fails, and so does a listed one that no longer occurs: fixing one
means deleting its line, so the list only shrinks.
"""
import ast
import pathlib
import unittest
from typing import Dict, Iterator, Optional, Set, Tuple

REPO = pathlib.Path(__file__).resolve().parent.parent
PROJECT = ("core", "graphs", "tools", "scripts", "agents", "main")

ADAPTERS = ("core.channel.discord", "core.voice", "core.scheduler", "graphs", "tools", "scripts", "agents", "main")
BELOW_RUNTIME = ("core.prompts", "core.loaders", "core.storage", "core.knowledge", "core.util",
                 "core.tooling", "core.permissions", "core.channel")
FOUNDATION = ("core.runtime.foundation",)

# The runtime's layers, lowest first.
LAYERS = (
    FOUNDATION,
    ("core.runtime.command_handler", "core.runtime.commands", "core.runtime.model",
     "core.runtime.history", "core.runtime.observability"),
    ("core.runtime.agent", "core.runtime.base_agent", "core.runtime.script_executor",
     "core.runtime.agent_factory", "core.runtime.graph_builder", "core.runtime.streaming"),
    ("core.runtime.delegation",),
    ("core.runtime.bootstrap",),
)

# "importer -> imported", or for R4 the package whose __init__.py imports.
ALLOWED: Dict[str, Set[str]] = {
    "R1": {
        # Phase 3: the adapter passes the channel it resolved, and an output port renders.
        "core.runtime.delegation -> core.channel.discord.loader",
        "core.runtime.agent -> core.channel.discord.dispatcher",
    },
    "R2": set(),
    "R3": {
        # Not yet scheduled: a config reload tells the Discord bots itself; it
        # should notify listeners that main.py registers.
        "core.loaders.agents_loader -> core.channel.discord.loader",
        # Not yet scheduled: the inbox table's rows are voice briefing types.
        "core.storage.channel_inbox -> core.voice.briefing.model",
        # Step 7.
        "core.util.summarize_util -> tools.filesystem",
    },
    "R4": {
        "core.channel",
        "core.channel.discord",
        "core.knowledge.memory",
        "core.knowledge.projects",
        "core.knowledge.vector.backends",
        "core.loaders",
        "core.prompts",
        "core.scheduler.preconditions",
        "core.storage",
        "core.tooling",
        "core.util",
    },
    "R5": {
        # Phase 3: Agent's side work moves into turn hooks.
        "core.runtime.agent -> core.runtime.observability.turn_trace",
        "core.runtime.agent -> core.runtime.graph_builder",
        "core.runtime.command_handler -> core.knowledge.memory.sqlite_checkpointer",
        "core.runtime.command_handler -> core.runtime.history.pruner",
        # Phase 3: delegation stops resolving Discord channels.
        "core.runtime.delegation -> core.channel.discord.loader",
        "core.runtime.delegation -> core.loaders.agents_loader",
        "core.runtime.delegation -> core.runtime.foundation.context",
        "core.runtime.delegation -> core.runtime.foundation.sessions",
        # Phase 3: model construction moves out of GraphBuilder into model/chat.py.
        "core.runtime.graph_builder -> core.loaders.graphs_loader",
        "core.runtime.graph_builder -> core.runtime.model.retry",
        "core.runtime.model.retry -> core.runtime.foundation.context",
        "core.runtime.model.retry -> core.runtime.model.thinking",
        "core.runtime.model.retry -> core.runtime.observability.turn_trace",
        "core.runtime.model.thinking -> core.loaders.agents_loader",
        "core.runtime.model.thinking -> core.runtime.foundation.context",
        "core.runtime.model.thinking -> core.runtime.observability.turn_trace",
        "core.runtime.model.thinking -> core.util.models",
        # Not yet scheduled. Tests patch most of these at their source module, so
        # moving one to the top of its file means re-pointing those patches too.
        "core.runtime.foundation.context -> core.util.models",
        "core.runtime.history.channel_posts -> core.knowledge.memory.sqlite_checkpointer",
        "core.runtime.history.channel_posts -> core.loaders.agents_loader",
        "core.runtime.history.channel_posts -> core.runtime.foundation.context",
        "core.runtime.history.channel_posts -> core.runtime.foundation.sessions",
        "core.runtime.history.channel_posts -> core.storage.sessions",
        "core.runtime.history.channel_posts -> core.util.time_util",
        "core.runtime.history.pruner -> core.knowledge.memory.sqlite_checkpointer",
        "core.runtime.history.pruner -> core.prompts.memory",
        "core.runtime.observability.transcript -> core.runtime.foundation.context",
        "core.runtime.observability.turn_trace -> core.storage.sessions",
    },
}


def within(module: str, prefixes) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def layer(module: str) -> Optional[int]:
    for rank, prefixes in enumerate(LAYERS):
        if within(module, prefixes):
            return rank
    return None


def module_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(REPO).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def is_module(name: str) -> bool:
    base = REPO.joinpath(*name.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def imports(path: pathlib.Path) -> Iterator[Tuple[str, bool]]:
    """Yields (imported module, inside a function) for each project import in `path`."""
    module = module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]

    def targets(node) -> Iterator[str]:
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
            return
        if node.level:
            parts = package.split(".")
            source = ".".join(parts[: len(parts) - node.level + 1] + ([node.module] if node.module else []))
        else:
            source = node.module or ""
        submodules = [f"{source}.{a.name}" for a in node.names if is_module(f"{source}.{a.name}")]
        yield from submodules or [source]

    def visit(node, in_function: bool) -> Iterator[Tuple[str, bool]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for target in targets(child):
                    if target.split(".")[0] in PROJECT:
                        yield target, in_function
            else:
                nested = in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                yield from visit(child, nested)

    yield from visit(ast.parse(path.read_text(encoding="utf-8")), False)


def violations() -> Dict[str, Set[str]]:
    found: Dict[str, Set[str]] = {rule: set() for rule in ALLOWED}
    for path in sorted((REPO / "core").rglob("*.py")):
        importer = module_name(path)
        in_runtime = within(importer, ("core.runtime",))
        below = within(importer, BELOW_RUNTIME) and not within(importer, ADAPTERS)
        for imported, in_function in imports(path):
            if imported == importer:
                continue
            edge = f"{importer} -> {imported}"
            if in_runtime and within(imported, ADAPTERS):
                found["R1"].add(edge)
            if in_runtime and importer != "core.runtime" and within(imported, ("core.runtime",)):
                if (layer(imported) or 0) > (layer(importer) or 0):
                    found["R2"].add(edge)
            above = within(imported, ADAPTERS) or (within(imported, ("core.runtime",))
                                                     and not within(imported, FOUNDATION))
            if below and above:
                found["R3"].add(edge)
            if path.name == "__init__.py":
                found["R4"].add(importer)
            if in_runtime and in_function:
                found["R5"].add(edge)
    return found


class TestLayers(unittest.TestCase):
    """See core/runtime/__init__.py for the rules and the layers."""

    @classmethod
    def setUpClass(cls):
        cls.found = violations()

    def check(self, rule: str, advice: str):
        new = sorted(self.found[rule] - ALLOWED[rule])
        fixed = sorted(ALLOWED[rule] - self.found[rule])
        self.assertEqual(new, [], f"{rule}: {advice}")
        self.assertEqual(fixed, [], f"{rule}: no longer violated; delete these from ALLOWED")

    def test_r1_the_runtime_never_imports_an_adapter(self):
        self.check("R1", "the runtime imports an adapter; have the adapter pass in what's needed")

    def test_r2_runtime_imports_point_down_the_layers(self):
        self.check("R2", "a runtime module imports from a higher layer")

    def test_r3_packages_below_import_nothing_above_them(self):
        self.check("R3", "a package below the runtime imports an adapter, or runtime outside "
                         "core.runtime.foundation; declare a port and fill it in core/runtime/bootstrap.py")

    def test_r4_package_inits_have_no_imports(self):
        self.check("R4", "a package __init__.py imports; import from the module itself where it's used")

    def test_r5_no_imports_inside_functions_in_the_runtime(self):
        self.check("R5", "a runtime function imports a project module; import it at the top of the file")

    def test_every_runtime_module_has_a_layer(self):
        for path in sorted((REPO / "core" / "runtime").rglob("*.py")):
            if path.name != "__init__.py":
                with self.subTest(module=module_name(path)):
                    self.assertIsNotNone(layer(module_name(path)), "add it to LAYERS")


if __name__ == "__main__":
    # Prints the current violations, in ALLOWED's format.
    for rule, entries in violations().items():
        print(f'    "{rule}": {{')
        for entry in sorted(entries):
            print(f'        "{entry}",')
        print("    },")
