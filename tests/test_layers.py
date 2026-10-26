"""The dependency rules in core/runtime/__init__.py, core/voice/__init__.py and
the __init__.py of core/config, core/util and core/git, checked against every
import in core/.

  R1  The runtime never imports an adapter.
  R2  Inside the runtime, imports point down the layers.
  R3  The packages the runtime builds on import nothing above them: no
      adapter, and from the runtime only core.runtime.foundation.
  R4  A package __init__.py has no imports.
  R5  The runtime imports no project module inside a function.
  R6  No module imports another package's underscore names.

  V1  Voice imports no other adapter.
  V2  Inside voice, imports point down the layers, and the parts (listening,
      speech, room) never import each other.
  V3  Of the adapters, only the Discord runner, which hosts the voice room,
      imports voice.
  V5  Voice imports no project module inside a function, except one that loads
      an optional heavy dependency (HEAVY).

  C1  core.config imports nothing else from the project.
  U1  core.util imports only core.config.
  G1  core.git imports only core.config and core.util.

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
                 "core.tooling", "core.permissions", "core.channel", "core.config", "core.git")
FOUNDATION = ("core.runtime.foundation",)

# The runtime's layers, lowest first.
LAYERS = (
    FOUNDATION,
    ("core.runtime.commands", "core.runtime.model", "core.runtime.history", "core.runtime.observability"),
    ("core.runtime.agent", "core.runtime.base_agent", "core.runtime.script_executor",
     "core.runtime.agent_factory", "core.runtime.graph_builder", "core.runtime.streaming"),
    ("core.runtime.delegation",),
    ("core.runtime.bootstrap",),
)

# Voice's layers, lowest first. The parts share a layer and never import each
# other: they meet in VoiceManager, through the Protocols each declares.
VOICE_PARTS = ("core.voice.listening", "core.voice.speech", "core.voice.room")
VOICE_LAYERS = (
    ("core.voice.foundation",),
    ("core.voice.engines",),
    VOICE_PARTS,
    ("core.voice.voice_manager", "core.voice.turn", "core.voice.hooks"),
    ("core.voice.briefing",),
)
# The adapters that may import voice.
VOICE_HOSTS = ("core.channel.discord.runner",)
# Modules that load an optional heavy dependency when imported: importing one
# inside a function is how it stays optional.
HEAVY = (
    "core.voice.engines.stt_engine",  # faster_whisper, the local fallback
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
    },
    "R5": {
        # Phase 3: Agent's side work moves into turn hooks.
        "core.runtime.agent -> core.runtime.observability.turn_trace",
        "core.runtime.agent -> core.runtime.graph_builder",
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
        # Not yet scheduled. Tests patch most of these at their source module, so
        # moving one to the top of its file means re-pointing those patches too.
        "core.runtime.history.channel_posts -> core.knowledge.memory.sqlite_checkpointer",
        "core.runtime.history.channel_posts -> core.loaders.agents_loader",
        "core.runtime.history.channel_posts -> core.runtime.foundation.sessions",
        "core.runtime.history.channel_posts -> core.storage.sessions",
        "core.runtime.history.pruner -> core.knowledge.memory.sqlite_checkpointer",
        "core.runtime.history.pruner -> core.prompts.memory",
        "core.runtime.observability.transcript -> core.runtime.foundation.context",
        "core.runtime.observability.turn_trace -> core.storage.sessions",
    },
    "R6": set(),
    "V1": set(),
    "V2": set(),
    "V3": set(),
    "V5": set(),
    "C1": set(),
    "U1": set(),
    "G1": set(),
}


def within(module: str, prefixes) -> bool:
    return any(module == p or module.startswith(p + ".") for p in prefixes)


def layer(module: str, layers=LAYERS) -> Optional[int]:
    for rank, prefixes in enumerate(layers):
        if within(module, prefixes):
            return rank
    return None


def part(module: str) -> Optional[str]:
    return next((p for p in VOICE_PARTS if within(module, (p,))), None)


def area(module: str) -> str:
    """The package a module belongs to, for R6: core.<package>, else its top level."""
    parts = module.split(".")
    return ".".join(parts[:2]) if parts[0] == "core" else parts[0]


def module_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(REPO).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def is_module(name: str) -> bool:
    base = REPO.joinpath(*name.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def source_of(node: ast.ImportFrom, package: str) -> str:
    if node.level:
        parts = package.split(".")
        return ".".join(parts[: len(parts) - node.level + 1] + ([node.module] if node.module else []))
    return node.module or ""


def imports(path: pathlib.Path) -> Iterator[Tuple[str, bool]]:
    """Yields (imported module, inside a function) for each project import in `path`."""
    module = module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]

    def targets(node) -> Iterator[str]:
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
            return
        source = source_of(node, package)
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


def private_names(path: pathlib.Path) -> Iterator[Tuple[str, str]]:
    """Yields (source module, name) for each underscore name `path` imports."""
    module = module_name(path)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name.startswith("_") and not alias.name.startswith("__"):
                    yield source_of(node, package), alias.name


def violations() -> Dict[str, Set[str]]:
    found: Dict[str, Set[str]] = {rule: set() for rule in ALLOWED}
    for path in sorted((REPO / "core").rglob("*.py")):
        importer = module_name(path)
        in_runtime = within(importer, ("core.runtime",))
        in_voice = within(importer, ("core.voice",))
        in_config = within(importer, ("core.config",))
        in_util = within(importer, ("core.util",))
        in_git = within(importer, ("core.git",))
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
            if in_voice and within(imported, ADAPTERS) and not within(imported, ("core.voice",)):
                found["V1"].add(edge)
            if in_voice and importer != "core.voice" and within(imported, ("core.voice",)):
                upward = (layer(imported, VOICE_LAYERS) or 0) > (layer(importer, VOICE_LAYERS) or 0)
                across = part(importer) and part(imported) and part(importer) != part(imported)
                if upward or across:
                    found["V2"].add(edge)
            if (not in_voice and within(imported, ("core.voice",)) and within(importer, ADAPTERS)
                    and not within(importer, VOICE_HOSTS)):
                found["V3"].add(edge)
            if in_voice and in_function and not within(imported, HEAVY):
                found["V5"].add(edge)
            if in_config and not within(imported, ("core.config",)):
                found["C1"].add(edge)
            if in_util and not within(imported, ("core.config", "core.util")):
                found["U1"].add(edge)
            if in_git and not within(imported, ("core.config", "core.util", "core.git")):
                found["G1"].add(edge)
        for source, name in private_names(path):
            if area(source) != area(importer):
                found["R6"].add(f"{importer} -> {source}.{name}")
    return found


class TestLayers(unittest.TestCase):
    """See core/runtime/__init__.py and core/voice/__init__.py for the rules and the layers."""

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

    def test_r6_no_underscore_names_from_another_package(self):
        self.check("R6", "a module imports another package's private name; make it public where it belongs")

    def test_v1_voice_imports_no_other_adapter(self):
        self.check("V1", "voice imports another adapter; have the Discord runner pass in what's needed")

    def test_v2_voice_imports_point_down_the_layers(self):
        self.check("V2", "a voice module imports from a higher layer, or one part imports another")

    def test_v3_only_the_discord_runner_imports_voice(self):
        self.check("V3", "an adapter imports voice; move what it needs below the adapters")

    def test_v5_no_imports_inside_functions_in_voice(self):
        self.check("V5", "a voice function imports a project module; import it at the top of the file, "
                         "or list it in HEAVY if it loads an optional heavy dependency")

    def test_c1_config_imports_nothing_from_the_project(self):
        self.check("C1", "core.config imports from the project; everything reads settings, so they can't "
                         "depend on anything")

    def test_u1_util_imports_only_config(self):
        self.check("U1", "a util imports more than core.config; it isn't a util: move it to the package "
                         "that uses it")

    def test_g1_git_imports_only_config_and_util(self):
        self.check("G1", "core.git imports more than core.config and core.util; have the caller pass in "
                         "what's needed")

    def test_every_runtime_module_has_a_layer(self):
        for path in sorted((REPO / "core" / "runtime").rglob("*.py")):
            if path.name != "__init__.py":
                with self.subTest(module=module_name(path)):
                    self.assertIsNotNone(layer(module_name(path)), "add it to LAYERS")

    def test_every_voice_module_has_a_layer(self):
        for path in sorted((REPO / "core" / "voice").rglob("*.py")):
            if path.name != "__init__.py":
                with self.subTest(module=module_name(path)):
                    self.assertIsNotNone(layer(module_name(path), VOICE_LAYERS), "add it to VOICE_LAYERS")


if __name__ == "__main__":
    # Prints the current violations, in ALLOWED's format.
    for rule, entries in violations().items():
        print(f'    "{rule}": {{')
        for entry in sorted(entries):
            print(f'        "{entry}",')
        print("    },")
