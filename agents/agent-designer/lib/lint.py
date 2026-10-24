"""agent_lint: static checks on an agent's definition, for agent-designer's audits.

Reads an agent the way the runtime will -- its agent.json, the persona files
the prompt loads, its skills' SKILL.md and its schedule prompts -- and reports
what would fail, or cost a round trip, at run time:

- Skills the text names that don't exist; in the agent's own files and
  schedules, also those it isn't granted.
- Repo and PKM paths the text names that no filesystem grant covers or that
  don't exist, and a filesystem write named before a path its grant leaves
  read-only.
- Tools the text uses that aren't granted or don't exist, and actions a tool
  doesn't have or the agent's grant leaves out. A tool exists when it is in
  tools/ (shared) or in the agent's own agents/<agent_id>/tools/.
- Granted tools that exist nowhere, and schedule preconditions that are
  neither a core type nor one of the agent's scans
  (agents/<agent_id>/preconditions/<type>.py).
- `!` deny keys that refuse less than they say: no path after the `!`, an
  action the tool doesn't have or a bundle it doesn't declare, a folder that
  doesn't exist, or a tool that matches its own selectors and ignores them.
- The agent's code against the design (how agents and tools share the work):
  an agent with tools of its own has a WORKFLOW.md and keeps the contract the
  test suite checks (core/tooling/workflow.py); its RULES.md in the PKM, when
  there is one, sets the keys its lib/rules.py declares and no others; and it
  may read every path on its tools' `Reads:` lines, so a broken tool slows it
  down instead of blinding it.
- Files in the agent folder the prompt never loads (a misspelt IDENTITY.md).
  WORKFLOW.md is read on demand, so it is not one of them.
- The persona files' shape (agent_creation's Persona Files): IDENTITY.md has
  its `Agent ID:`, `Name:`, `Emoji:` and `Description:` lines, and AGENTS.md is
  operating instructions, never the design's classification table.
- What the config tests hold every agent to: filesystem grants that name each
  bundle once and never spell one out; channels the concierge hosts, pinned in
  the routing table (tests/core/runtime/foundation/test_routing.py) as the
  configs route them; and, for an agent the voice room switches to, a Chirp 3
  HD voice no other agent has. A voice finding lists the free voices.
- Budgets: persona files against agent_creation's ceilings, preloaded skill
  text against PRELOAD_TOKEN_BUDGET, and the static prompt and tool schemas.

Text is matched with patterns, so a finding is a lead to check, not a verdict.
The patterns favour precision. Shared skills name other agents' tools and
skills as examples, so in skill text only directives count (`load_skill("x")`,
"call `x`", "the `x` tool"); the agent's own files are held to every mention.

Grants are merged from the files under `root` the way ToolsLoader merges them
(agent.json, its skills, its graph, the default PKM workspace), so the checks
run on any checkout without starting the loaders. Tool actions, bundles and
docstrings are read from each tool's source; tool modules are imported only to
expand permission bundles (the loader's own mechanism) and to measure schemas.
An agent's lib/rules.py is imported from its path, for its RULES. Code in the
middle of a change (a tool that doesn't parse, a lib/rules.py that doesn't
import) is a finding, never a crash.

agent-designer's `agent_lint` tool (tools/agent_lint.py) is its front end: it
calls `lint_agents` and `render`. agents/agent-designer/WORKFLOW.md states the
checks rule by rule, with examples the test suite runs.
"""
import ast
import copy
import importlib
import importlib.util
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from core.loaders.agent_schema import known_skill_ids
from core.loaders.skills_loader import PRELOAD_TOKEN_BUDGET, core_section, skill_body
from core.permissions.denies import DENY_PREFIX, deny_covers, is_deny
from core.tooling import AgentTool, Report, Rule
from core.tooling.rules import RULES_FILE, read_rules_text, rule_keys, rules_path
from core.tooling.workflow import DOCSTRING_LINES, WORKFLOW_FILE, contract_problems, parse_workflow
from core.voice.foundation.settings import CHIRP3_HD_VOICES, chirp3_voice

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CHARS_PER_TOKEN = 4  # the estimate PRELOAD_TOKEN_BUDGET and the context pruner use

# The files the system prompt loads from an agent's folder (core/prompts/system.py),
# each with agent_creation's prompt budget: (target, hard ceiling) in characters.
PERSONA_LIMITS = {
    "AGENTS.md": (1500, 2500),
    "INSTRUCTIONS.md": (0, 2500),
    "SOUL.md": (1000, 1500),
    "IDENTITY.md": (250, 400),
}
# USER.md, about the agent's human, lives in the PKM (pkm/agents/<agent_id>/USER.md), never in the repo.
USER_FILE = "USER.md"
USER_LIMIT = (400, 800)
PERSONA_TARGET = 3500
# agent_creation: an agent above ~2000 tokens of static prompt can't run on-device.
LOCAL_PROMPT_TOKENS = 2000

# Finding kinds, in the order a report lists them.
KINDS = (
    "phantom_skill", "skill_not_granted", "unknown_tool", "tool_not_granted", "unresolved_bundle",
    "redundant_grant", "malformed_deny", "unknown_action", "action_not_granted", "path_outside_grants",
    "read_not_granted", "missing_path", "unknown_precondition", "channel_not_hosted", "routing_table_stale",
    "voice_missing", "unknown_voice", "voice_taken", "workflow_missing", "contract_gap", "rules_mismatch",
    "unloaded_file", "persona_format", "preload_missing", "over_budget",
)

# IDENTITY.md's lines, as every agent writes them (agent_creation: Persona Files).
IDENTITY_FIELDS = ("Agent ID", "Name", "Emoji", "Description")
# A header of agent_creation's classification table (step | kind | where it lives |
# why): it is for the plan and the report, never an agent's file.
_CLASSIFICATION_TABLE = re.compile(r"^\s*\|.*\b(?:classification|where it lives)\b", re.IGNORECASE | re.MULTILINE)

# The routing table the test suite pins: TestLiveRoutingTable.EXPECTED, channel -> the
# agent the concierge hands it to (None: it answers itself). The voice room's focus
# test reads the same table, so a new channel is one line here.
ROUTING_TABLE_TEST = "tests/core/runtime/foundation/test_routing.py"

_ID = r"[a-z0-9]+(?:_[a-z0-9]+)*"
_SNAKE = r"[a-z0-9]+(?:_[a-z0-9]+)+"  # has an underscore, so it is safe to match unquoted
_ROOTS = r"(?:~/pkm|pkm|agents|skills|tools|tests|graphs|scripts|core|workspaces)/"
_PATH = re.compile(r"(?<![\w./~-])" + _ROOTS + r"[^\s`'\"()\[\]{},;|]*")
_PATH_SPAN = re.compile(r"`(" + _ROOTS + r"[^`]*)`")  # a whole backticked path, spaces and all
# A path segment that stands for many: `<slug>`, `YYYY-MM.md`, `*.md`.
_PLACEHOLDER = re.compile(r"[<>{}*?\[\]]|(?<![A-Za-z])(?:YYYY|YY|MM|DD|HH|Www)(?![A-Za-z])")
# A line that creates what it names: the folders needn't exist yet.
_CREATES = re.compile(r"\b(?:writ|append|creat|archiv|sav|overwrit|mov|mkdir)\w*", re.IGNORECASE)
_SKILL_DIRECTIVES = (
    re.compile(rf"load_skill\(\s*[\"']?({_ID})[\"']?\s*\)"),
    re.compile(rf"\b(?i:load(?:s|ing)?\s+(?:the\s+)?)`({_ID})`"),
)
_SKILL_MENTIONS = (
    re.compile(rf"`({_ID})`\s+skill\b"),
    re.compile(rf"(?<![`\w])({_SNAKE})\s+skill\b"),
    re.compile(rf"\bskill\s+`({_ID})`"),
)
_TICKED = re.compile(rf"`({_ID})[`(]")
_TOOL_DIRECTIVES = (
    re.compile(rf"`({_ID})\("),
    re.compile(rf"\b(?i:(?:calls?|calling|uses?|using|runs?|running|invokes?|invoking)\s+(?:the\s+)?)`({_ID})`"),
)
_TOOL_WORD = re.compile(rf"`({_ID})`\s+tool\b|(?<![`\w])({_ID})\s+tool\b")
_CALL = re.compile(rf"\b(?i:call(?:s|ing)?\s+(?:the\s+)?)`({_ID})\(")
_ACTION_WORD = re.compile(rf"`({_ID})`\s+action\b")
_JSON_ACTION = re.compile(rf"\"action\"\s*:\s*\"({_ID})\"")
# Literal action tables a tool module may declare.
_ACTION_TABLES = {"ACTIONS", "READ_ACTIONS", "WRITE_ACTIONS", "IMPLEMENTED", "WRITE_IMPLEMENTED"}
# A docstring line that starts a new part: "Computes:", "Leaves out:", "Args:".
_LABEL = re.compile(r"^[A-Z][A-Za-z ]{0,30}:(?:\s|$)")
# What makes a deny key's path a pattern (core/permissions/denies.py).
_WILDCARD = ("*", "?", "[")


@dataclass(frozen=True)
class Finding:
    kind: str
    message: str
    where: str = ""


@dataclass
class Budget:
    persona: Dict[str, int] = field(default_factory=dict)  # file -> characters
    preload: Dict[str, int] = field(default_factory=dict)  # "skill (mode)" -> tokens
    static_tokens: int = 0  # formatting rules, persona, skills list and preloads
    tools: List[str] = field(default_factory=list)  # bound tools, load_skill included
    schema_tokens: Optional[int] = None  # None: not measured
    unmeasured: List[str] = field(default_factory=list)

    @property
    def persona_chars(self) -> int:
        return sum(self.persona.values())

    @property
    def preload_tokens(self) -> int:
        return sum(self.preload.values())

    def render(self) -> List[str]:
        files = ", ".join(f"{name} {chars:,}" for name, chars in self.persona.items())
        over = f", over the {PERSONA_TARGET:,} target" if self.persona_chars > PERSONA_TARGET else ""
        preloads = ", ".join(f"{name} {tokens:,}" for name, tokens in self.preload.items())
        schemas = f", schemas ~{self.schema_tokens:,} tokens" if self.schema_tokens is not None else ""
        unmeasured = f"; unmeasured: {', '.join(self.unmeasured)}" if self.unmeasured else ""
        return [
            f"budget: persona {self.persona_chars:,} chars ({files or 'no files'}{over}) | "
            f"preload ~{self.preload_tokens:,}/{PRELOAD_TOKEN_BUDGET:,} tokens{f' ({preloads})' if preloads else ''} | "
            f"static prompt ~{self.static_tokens:,} tokens",
            f"tools ({len(self.tools)}{schemas}{unmeasured}): {', '.join(self.tools) or 'none'}",
        ]


@dataclass
class AgentReport:
    agent_id: str
    findings: List[Finding]
    budget: Budget
    skills: Dict[str, Any]
    schedules: List[str]

    def render(self) -> str:
        grouped = _collapse(self.findings)
        lines = [f"{self.agent_id}: {len(grouped)} finding{'' if len(grouped) == 1 else 's'}"]
        lines += [f"- {f.kind}: {f.message}" + (f" ({where})" if where else "") for f, where in grouped]
        lines += self.budget.render()
        skills = [f"{s} ({v['preload']})" if isinstance(v, dict) and v.get("preload") else s
                  for s, v in sorted(self.skills.items())]
        lines.append(f"skills: {', '.join(skills) or 'none'}")
        lines.append(f"schedules: {'; '.join(self.schedules) or 'none'}")
        return "\n".join(lines)


@dataclass
class Catalog:
    """What exists: skill ids, each tool's module, action and bundle names, and scan types."""
    skills: Set[str]
    tools: Dict[str, Set[str]]  # tool -> its actions (empty: it takes none)
    modules: Dict[str, str]  # tool -> dotted module path
    words: Set[str] = field(default_factory=set)  # snake_case words in tool sources: args, keys
    scans: Set[str] = field(default_factory=set)  # the agent's own precondition types
    bundles: Dict[str, Set[str]] = field(default_factory=dict)  # tool -> the `@` bundles it declares
    matched: Set[str] = field(default_factory=set)  # tools that match their own selectors (no path grants)

    @classmethod
    def load(cls, root: str = PROJECT_ROOT) -> "Catalog":
        """The shared tools (tools/) and every skill."""
        catalog = cls(known_skill_ids(os.path.join(root, "skills")), {}, {})
        catalog._add_tools(_tool_files(os.path.join(root, "tools"), "tools"))
        return catalog

    def for_agent(self, agent_id: str, root: str = PROJECT_ROOT) -> "Catalog":
        """This catalog plus the agent's own tools and scans, from agents/<agent_id>/."""
        package = agent_package(agent_id)
        catalog = Catalog(set(self.skills), dict(self.tools), dict(self.modules), set(self.words),
                          bundles=dict(self.bundles), matched=set(self.matched))
        catalog._add_tools(_tool_files(os.path.join(root, "agents", agent_id, "tools"), f"agents.{package}.tools"))
        catalog.scans = set(_tool_files(os.path.join(root, "agents", agent_id, "preconditions"), ""))
        return catalog

    def _add_tools(self, files: Dict[str, Tuple[str, str]]) -> None:
        for name, (path, module) in files.items():
            source = _read(path) or ""
            self.tools[name] = actions_in_module(source, name)
            self.modules[name] = module
            self.words |= set(re.findall(rf"\b{_SNAKE}\b", source))
            self.bundles[name], matcher = permission_declarations(source)
            if matcher:
                self.matched.add(name)


def agent_package(agent_id: str) -> str:
    """The import name of the agent's code (`agents.<this>`): its id with "-" as "_"."""
    return agent_id.replace("-", "_")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def agent_ids(root: str = PROJECT_ROOT) -> List[str]:
    agents_dir = os.path.join(root, "agents")
    if not os.path.isdir(agents_dir):
        return []
    return sorted(a for a in os.listdir(agents_dir) if os.path.isfile(os.path.join(agents_dir, a, "agent.json")))


def lint_agents(ids: Optional[Iterable[str]] = None, root: str = PROJECT_ROOT,
                measure_schemas: bool = True) -> List[AgentReport]:
    """Reports for `ids`, or for every agent under `root`."""
    catalog = Catalog.load(root)
    return [lint_agent(a, root, catalog, measure_schemas) for a in (list(ids) if ids else agent_ids(root))]


def lint_agent(agent_id: str, root: str = PROJECT_ROOT, catalog: Optional[Catalog] = None,
               measure_schemas: bool = True) -> AgentReport:
    """One agent's report. Raises LookupError when it has no agent.json."""
    catalog = (catalog or Catalog.load(root)).for_agent(agent_id, root)
    agent_dir = os.path.join(root, "agents", agent_id)
    path = os.path.join(agent_dir, "agent.json")
    if not os.path.isfile(path):
        raise LookupError(f"agents/{agent_id}/agent.json does not exist")
    config = _read_json(path)
    if not isinstance(config, dict):
        raise ValueError(f"agents/{agent_id}/agent.json is not a JSON object")

    skills = granted_skills(config, root)
    grants = merged_grants(config, agent_id, root, skills)
    bound = sorted({t for t in grants if t in catalog.tools}
                   | ({"load_skill"} if _needs_load_skill(skills) else set()))
    checker = _Checker(agent_id, root, catalog, skills, grants, set(bound))
    for source in _sources(agent_id, root, config, skills):
        checker.scan(source)

    findings = checker.findings + _granted_but_missing(agent_id, root, config, skills, catalog)
    findings += _unresolved_bundles(agent_id, grants)
    findings += _redundant_grants(agent_id, root, config, skills)
    findings += _malformed_denies(agent_id, root, config, skills, catalog)
    findings += _unknown_preconditions(agent_id, config, catalog)
    routes = live_routes(root)
    findings += _channels(agent_id, root, config, routes)
    findings += _voices(agent_id, root, config, routes)
    tools = own_tools(agent_id, root)
    rules, rules_error = load_agent_rules(agent_id, root)
    findings += _contract(agent_id, root, tools, rules, rules_error)
    findings += _rules_file(agent_id, rules, rules_error)
    findings += _unreadable_sources(agent_id, root, tools, grants)
    findings += _unloaded_files(agent_dir, agent_id)
    findings += _persona_format(agent_dir, agent_id)
    budget, over = _budget(agent_id, root, config, skills, bound, catalog, measure_schemas)
    return AgentReport(agent_id, findings + over, budget, skills, _schedules(config))


def _unresolved_bundles(agent_id: str, grants: Dict[str, Any]) -> List[Finding]:
    """`@` names left after expansion: the loader found no definition, so they grant nothing.

    Deny keys are left to `_malformed_denies`, which says what they fail to refuse.
    """
    findings = []
    for tool, scope in sorted(grants.items()):
        lists = [a for key, a in scope.items() if not is_deny(key)] if isinstance(scope, dict) else [scope]
        names = sorted({a for actions in lists if isinstance(actions, list) for a in actions
                        if isinstance(a, str) and a.startswith("@")})
        if names:
            findings.append(Finding("unresolved_bundle", f"`{tool}` grants {', '.join(names)}, which the loader "
                                    "can't expand, so they grant nothing", f"agents/{agent_id}/agent.json"))
    return findings


def render(reports: List[AgentReport]) -> str:
    body = "\n\n".join(r.render() for r in reports)
    if len(reports) == 1:
        return body
    total = sum(len(_collapse(r.findings)) for r in reports)
    return f"{len(reports)} agents, {total} findings\n\n{body}"


# ---------------------------------------------------------------------------
# Grants, as ToolsLoader merges them
# ---------------------------------------------------------------------------

def granted_skills(config: Dict[str, Any], root: str = PROJECT_ROOT) -> Dict[str, Any]:
    """Skill id -> settings: the agent's `skills`, plus its graph's skills (on demand)."""
    raw = config.get("skills") or {}
    skills = {s: {} for s in raw} if isinstance(raw, list) else dict(raw) if isinstance(raw, dict) else {}
    for skill_id in _graph_configs(root).get(config.get("graph", "main"), {}).get("skills") or []:
        skills.setdefault(skill_id, {})
    return skills


def merged_grants(config: Dict[str, Any], agent_id: str, root: str = PROJECT_ROOT,
                  skills: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Tool -> scope, bundles expanded: what ToolsLoader would grant this agent."""
    from core.permissions.bundles import expand_permissions
    from core.util.config import Config

    merged = copy.deepcopy(config.get("tools") or {})
    for skill_id in skills if skills is not None else granted_skills(config, root):
        _merge(merged, _skill_json(root, skill_id).get("tools") or {})
    if config.get("graph"):
        _merge(merged, _graph_configs(root).get(config["graph"], {}).get("tools") or {})
    filesystem = merged.setdefault("filesystem", {})
    if isinstance(filesystem, dict):
        filesystem.setdefault(os.path.join(Config().pkm_dir, "agents", agent_id), ["@write", "delete"])
    return expand_permissions(merged)


def granted_actions(scope: Any) -> Optional[Set[str]]:
    """The actions a grant allows anywhere; None when it allows every action."""
    if not scope:
        return None
    if isinstance(scope, list):
        return None if "*" in scope else set(scope)
    if isinstance(scope, dict):
        return {a for key, actions in scope.items()
                if not is_deny(key) and isinstance(actions, list) for a in actions}
    return None


def _merge(merged: Dict[str, Any], extra: Dict[str, Any]) -> None:
    """ToolsLoader's merge: same-shaped scopes combine, anything else is replaced."""
    for tool, scope in extra.items():
        current = merged.get(tool)
        if tool not in merged:
            merged[tool] = copy.deepcopy(scope)
        elif isinstance(current, dict) and isinstance(scope, dict):
            for selector, actions in scope.items():
                if isinstance(current.get(selector), list) and isinstance(actions, list):
                    current[selector] = list(dict.fromkeys(current[selector] + actions))
                else:
                    current[selector] = copy.deepcopy(actions)
        elif isinstance(current, list) and isinstance(scope, list):
            merged[tool] = list(dict.fromkeys(current + scope))
        else:
            merged[tool] = copy.deepcopy(scope)


def _needs_load_skill(skills: Dict[str, Any]) -> bool:
    """SkillsLoader.needs_load_skill: some skill isn't preloaded in full."""
    return any(not (isinstance(v, dict) and v.get("preload") == "full") for v in skills.values())


class _PathGrants:
    """The filesystem actions an agent may take on a path, as check_permission decides."""

    def __init__(self, scope: Any, agent_id: str, root: str):
        self.agent_id = agent_id
        self.root = os.path.realpath(root)
        # A list (or blanket) grant never consults the path.
        self.unscoped = not isinstance(scope, dict) or not scope
        self.everywhere = granted_actions(scope)
        self.bases = [] if self.unscoped else [
            (self.resolve(base), set(actions) if isinstance(actions, list) else set(), base)
            for base, actions in scope.items() if not is_deny(base)
        ]
        # "!<path>" keys refuse their actions under that path (core/permissions/denies.py).
        self.denies = [] if self.unscoped else [
            (key, set(actions) if isinstance(actions, list) else set())
            for key, actions in scope.items() if is_deny(key)
        ]

    def resolve(self, path: str) -> str:
        path = os.path.expanduser(path.replace("<agent_id>", self.agent_id))
        return os.path.realpath(path if os.path.isabs(path) else os.path.join(self.root, path))

    def actions(self, path: str) -> Optional[Set[str]]:
        """The actions allowed on `path`; None means every action, an empty set none."""
        if self.unscoped:
            return self.everywhere
        target = self.resolve(path)
        allowed: Set[str] = set()
        for base, actions, _ in self.bases:
            if _inside(target, base):
                allowed |= actions
        for key, actions in self.denies:
            if deny_covers(key, target, self.root, self.agent_id):
                allowed -= actions
        return allowed

    def granted_inside(self, path: str) -> List[str]:
        """The grants for folders inside `path`, as written."""
        target = self.resolve(path)
        return sorted(name for base, _, name in self.bases if base != target and _inside(base, target))

    def missing_folder(self, path: str, is_folder: bool) -> Optional[str]:
        """The folder `path` needs, when it doesn't exist: the path itself for a folder,
        the part before the first placeholder for a template (`<slug>`, `YYYY-MM.md`),
        else the file's folder. Files are created by a write, so they may be missing."""
        parts = path.split("/")
        concrete = next((i for i, part in enumerate(parts) if _PLACEHOLDER.search(part)), None)
        folder = "/".join(parts[:concrete] if concrete is not None else parts if is_folder else parts[:-1])
        return None if os.path.isdir(self.resolve(folder)) else folder


def _inside(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


# ---------------------------------------------------------------------------
# Text checks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Source:
    label: str
    text: str
    own: bool  # the agent's own files and schedules, held to every mention
    numbered: bool = True


def _sources(agent_id: str, root: str, config: Dict[str, Any], skills: Dict[str, Any]) -> Iterator[_Source]:
    for name in PERSONA_LIMITS:
        text = _read(os.path.join(root, "agents", agent_id, name))
        if text is not None:
            yield _Source(f"agents/{agent_id}/{name}", text, own=True)
    for index, entry in enumerate(config.get("schedules") or []):
        prompt = entry.get("prompt") if isinstance(entry, dict) else None
        text = "\n".join(map(str, prompt)) if isinstance(prompt, list) else prompt
        if isinstance(text, str) and text.strip():
            label = f"agents/{agent_id}/agent.json schedule {entry.get('id') or index}"
            yield _Source(label, text, own=True, numbered=False)
    for skill_id in sorted(skills):
        text = _read(os.path.join(root, "skills", skill_id, "SKILL.md"))
        if text is not None:
            yield _Source(f"skills/{skill_id}/SKILL.md", text, own=False)


class _Checker:
    def __init__(self, agent_id: str, root: str, catalog: Catalog, skills: Dict[str, Any],
                 grants: Dict[str, Any], bound: Set[str]):
        self.catalog = catalog
        self.skills = skills
        self.grants = grants
        self.bound = bound
        self.paths = _PathGrants(grants.get("filesystem"), agent_id, root)
        self.findings: List[Finding] = []
        self._seen: Set[Finding] = set()
        names = "|".join(re.escape(t) for t in sorted(catalog.tools, key=len, reverse=True))
        # "`filesystem` `ls`", "filesystem `read`", "the `git` tool's `log` action"
        self._pair = re.compile(rf"(?<![\w`])`?({names})`?(?:\s+tool(?:'s)?)?\s+`({_ID})`") if names else None
        writes = catalog.tools.get("filesystem", set()) - set(_expand("filesystem", "@read"))
        self._writes = re.compile(rf"`({'|'.join(sorted(writes))})`") if writes else None

    def scan(self, source: _Source) -> None:
        for number, line in enumerate(source.text.splitlines(), 1):
            where = f"{source.label}:{number}" if source.numbered else source.label
            self._skills(line, where, source.own)
            self._tools(line, where, source.own)
            self._actions(line, where)
            self._paths(line, where)

    def _add(self, kind: str, message: str, where: str) -> None:
        finding = Finding(kind, message, where)
        if finding not in self._seen:
            self._seen.add(finding)
            self.findings.append(finding)

    def _skills(self, line: str, where: str, own: bool) -> None:
        refs = [(s, True) for p in _SKILL_DIRECTIVES for s in p.findall(line)]
        refs += [(s, False) for p in _SKILL_MENTIONS for s in p.findall(line)]
        for skill_id, directive in refs:
            if skill_id not in self.catalog.skills:
                self._add("phantom_skill", f"`{skill_id}` is not a skill", where)
            elif skill_id not in self.skills and (own or directive):
                self._add("skill_not_granted", f"`{skill_id}` is not in its skills", where)

    def _tools(self, line: str, where: str, own: bool) -> None:
        call, verb = _TOOL_DIRECTIVES
        used = set(verb.findall(line))
        refs = used | set(call.findall(line)) | (set(_TICKED.findall(line)) if own else set())
        if self._pair:
            refs |= {tool for tool, _ in self._pair.findall(line)}
        # Named as a tool ("`x` tool", "call `x(`"), or used ("use `x`") under a snake_case
        # name that no tool source mentions (so not an argument or action either).
        unknown = set(_CALL.findall(line)) | {n for n in used if re.fullmatch(_SNAKE, n) and n not in self.catalog.words}
        for ticked, plain in _TOOL_WORD.findall(line):
            refs.add(ticked or plain)
            if ticked or re.fullmatch(_SNAKE, plain):
                unknown.add(ticked or plain)
        for name in sorted(unknown):
            if name not in self.catalog.tools and name not in self.catalog.skills:
                self._add("unknown_tool", f"`{name}` is not a tool", where)
        for name in sorted(refs):
            if name in self.catalog.tools and name not in self.bound:
                self._add("tool_not_granted", f"`{name}` is not granted", where)

    def _allows(self, tool: str, action: str) -> bool:
        if tool not in self.bound:
            return False
        allowed = granted_actions(self.grants.get(tool))
        return allowed is None or action in allowed

    def _actions(self, line: str, where: str) -> None:
        paired = set()
        for tool, action in self._pair.findall(line) if self._pair else ():
            paired.add(action)
            actions = self.catalog.tools.get(tool) or set()
            if not actions:  # the tool takes no action names (git, gog, ...)
                continue
            if action not in actions:
                self._add("unknown_action", f"`{tool}` has no action `{action}`", where)
            elif tool in self.bound and not self._allows(tool, action):
                self._add("action_not_granted", f"`{tool}` `{action}` is not granted", where)
        for action in _ACTION_WORD.findall(line) + _JSON_ACTION.findall(line):
            if action in paired:
                continue
            owners = sorted(t for t, actions in self.catalog.tools.items() if action in actions)
            if not owners:
                self._add("unknown_action", f"no tool has an action `{action}`", where)
            elif not any(self._allows(t, action) for t in owners):
                self._add("action_not_granted", f"`{action}` ({', '.join(owners)}) is not granted", where)

    def _paths(self, line: str, where: str) -> None:
        found = _line_paths(line)
        creates = bool(_CREATES.search(line))
        for _, path, is_folder in found:
            if self.paths.actions(path) == set():
                inside = self.paths.granted_inside(path)
                covered = f"only {', '.join(inside)} inside it" if inside else "no filesystem grant covers it"
                self._add("path_outside_grants", f"{path}: {covered}", where)
                continue
            missing = None if creates else self.paths.missing_folder(path, is_folder)
            if missing:
                self._add("missing_path", f"{path}: {missing} does not exist" if missing != path
                          else f"{path} does not exist", where)
        # A filesystem write named just before a path must be granted on that path.
        for match in self._writes.finditer(line) if self._writes else ():
            target = next((p for start, p, _ in found if start > match.end()), None)
            allowed = self.paths.actions(target) if target else None
            if allowed and match.group(1) not in allowed:
                self._add("action_not_granted", f"`filesystem` `{match.group(1)}` on {target} is not granted", where)


def _line_paths(line: str) -> List[Tuple[int, str, bool]]:
    """(offset, path, names a folder) for each path in a line: whole backticked paths
    first, so spaces in file names survive, then paths in the rest of the line."""
    found, rest = [], line
    for match in _PATH_SPAN.finditer(line):
        found.append((match.start(), match.group(1).strip()))
        rest = rest[:match.start()] + " " * (match.end() - match.start()) + rest[match.end():]
    found += [(m.start(), m.group(0)) for m in _PATH.finditer(rest)]
    paths = []
    for start, raw in sorted(found):
        raw = raw.rstrip(".,:;!?*")
        paths.append((start, raw.rstrip("/") or raw, raw.endswith("/")))
    return paths


# ---------------------------------------------------------------------------
# Config checks and budgets
# ---------------------------------------------------------------------------

def _granted_but_missing(agent_id: str, root: str, config: Dict[str, Any], skills: Dict[str, Any],
                         catalog: Catalog) -> List[Finding]:
    """Granted tool names with no module: the loader skips them without a word."""
    grants = [(f"agents/{agent_id}/agent.json", config.get("tools") or {})]
    grants += [(f"skills/{s}/skill.json", _skill_json(root, s).get("tools") or {}) for s in sorted(skills)]
    own = f"agents/{agent_id}/tools"
    return [
        Finding("unknown_tool", f"`{tool}` is granted but is in neither tools/ nor {own}/", where)
        for where, tools in grants for tool in tools if tool not in catalog.tools
    ]


def core_precondition_types() -> Optional[Set[str]]:
    """The precondition types every schedule may use; None if the registry won't import."""
    try:
        from core.scheduler.preconditions import REGISTRY
    except Exception:
        return None
    return set(REGISTRY)


def _unknown_preconditions(agent_id: str, config: Dict[str, Any], catalog: Catalog) -> List[Finding]:
    """Schedule preconditions that are neither a core type nor one of the agent's own scans."""
    core = core_precondition_types()
    if core is None:
        return []
    findings = []
    for index, entry in enumerate(config.get("schedules") or []):
        precondition = entry.get("precondition") if isinstance(entry, dict) else None
        kind = precondition.get("type") if isinstance(precondition, dict) else None
        if isinstance(kind, str) and kind not in core | catalog.scans:
            findings.append(Finding(
                "unknown_precondition",
                f"schedule {entry.get('id') or index}: `{kind}` is neither a core precondition nor a scan in "
                f"agents/{agent_id}/preconditions/", f"agents/{agent_id}/agent.json"))
    return findings


def grant_redundancies(actions: Sequence[Any], tool: str = "filesystem") -> List[str]:
    """Why a grant list says more than it needs to, the way the bundle tests read it
    (tests/core/permissions/test_bundles.py): a bundle named beside one that holds it
    (`["@read", "@write"]` is `["@write"]`), or a bundle's every action listed instead
    of its name. [] when it says each thing once."""
    from core.permissions import bundles as pb

    tokens = [a for a in actions if isinstance(a, str)]
    problems = []
    for token in tokens:
        others = [t for t in tokens if t != token and pb.is_bundle(t)]
        if others and set(pb.expand_actions(tool, [token])) <= set(pb.expand_actions(tool, others)):
            problems.append(f"`{token}` is already in {', '.join(f'`{o}`' for o in others)}")
    granted = set(pb.expand_actions(tool, tokens))
    named = set(pb.expand_actions(tool, [t for t in tokens if pb.is_bundle(t)]))
    for bundle in pb.definitions_for(tool):
        members = set(pb.expand_actions(tool, [bundle]))
        if members <= granted and not members <= named:
            problems.append(f"it spells out `{bundle}`: name it instead")
    return problems


def _redundant_grants(agent_id: str, root: str, config: Dict[str, Any], skills: Dict[str, Any]) -> List[Finding]:
    """Filesystem grant lists in agent.json and its skills' skill.json that repeat or spell out a bundle."""
    sources = [(f"agents/{agent_id}/agent.json", config.get("tools"))]
    sources += [(f"skills/{s}/skill.json", _skill_json(root, s).get("tools")) for s in sorted(skills)]
    findings = []
    for where, tools in sources:
        scope = tools.get("filesystem") if isinstance(tools, dict) else None
        for selector, actions in scope.items() if isinstance(scope, dict) else ():
            if isinstance(actions, list):
                findings += [Finding("redundant_grant", f"`filesystem` [`{selector}`]: {problem}", where)
                             for problem in grant_redundancies(actions)]
    return findings


def channel_routes(hosts: Iterable[Any], channels: Dict[str, Iterable[Any]]) -> Dict[str, Optional[str]]:
    """Each channel the concierge hosts -> the agent it hands it to: the one agent that
    lists it, or None when several or none do and the concierge answers itself.
    `channels` maps every other agent to its `channels`; a worker listing `*` is
    reachable everywhere, so it never counts. Case doesn't matter, as in routing."""
    listed = {agent: {str(c).lower() for c in names} for agent, names in channels.items()}
    listed = {agent: names for agent, names in listed.items() if "*" not in names}
    routes = {}
    for channel in hosts:
        if isinstance(channel, str):
            agents = [agent for agent, names in listed.items() if channel.lower() in names]
            routes[channel] = agents[0] if len(agents) == 1 else None
    return routes


def live_routes(root: str = PROJECT_ROOT) -> Optional[Dict[str, Optional[str]]]:
    """channel_routes for the configs under `root`; None without a concierge
    (agents/main/agent.json) that hosts channels."""
    main = _read_json(os.path.join(root, "agents", "main", "agent.json"), None)
    hosts = main.get("channel_hosts") if isinstance(main, dict) else None
    if not isinstance(hosts, list):
        return None
    channels = {}
    for agent_id in agent_ids(root):
        config = _read_json(os.path.join(root, "agents", agent_id, "agent.json"), None)
        names = config.get("channels") if isinstance(config, dict) else None
        if agent_id != "main" and isinstance(names, list):
            channels[agent_id] = names
    return channel_routes(hosts, channels)


def routing_table(root: str = PROJECT_ROOT) -> Optional[Dict[str, Any]]:
    """TestLiveRoutingTable.EXPECTED, read from ROUTING_TABLE_TEST's source (never
    imported); None when it can't be read."""
    try:
        tree = ast.parse(_read(os.path.join(root, ROUTING_TABLE_TEST)) or "")
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == "TestLiveRoutingTable"):
            continue
        for statement in node.body:
            if isinstance(statement, ast.Assign) and [getattr(t, "id", None) for t in statement.targets] == ["EXPECTED"]:
                try:
                    table = ast.literal_eval(statement.value)
                except (ValueError, TypeError, SyntaxError):
                    return None
                return table if isinstance(table, dict) else None
    return None


def _channels(agent_id: str, root: str, config: Dict[str, Any],
              routes: Optional[Dict[str, Optional[str]]]) -> List[Finding]:
    """Each of the agent's channels is hosted by the concierge, and pinned in the
    routing table as the configs route it now."""
    names = config.get("channels")
    channels = [c for c in names if isinstance(c, str) and c != "*"] if isinstance(names, list) else []
    if agent_id == "main" or routes is None or not channels:
        return []
    hosted = {channel.lower(): channel for channel in routes}
    table = routing_table(root)
    where = f"agents/{agent_id}/agent.json"
    findings = []
    for name in channels:
        channel = hosted.get(name.lower())
        if channel is None:
            findings.append(Finding("channel_not_hosted", f"#{name} is not in the concierge's channel_hosts "
                                    "(agents/main/agent.json), so nothing routes it to this agent", where))
        elif table is not None and table.get(channel, _MISSING) != routes[channel]:
            agent = routes[channel]
            line = f'"{channel}": ' + (f'"{agent}"' if agent else "None")
            findings.append(Finding("routing_table_stale", f"#{channel} goes to {agent or 'the concierge'} now: "
                                    f"set `{line}` in TestLiveRoutingTable.EXPECTED ({ROUTING_TABLE_TEST})", where))
    return findings


def free_voices(taken: Iterable[str]) -> List[str]:
    """The Chirp 3 HD voices (CHIRP3_HD_VOICES, core/voice/foundation/settings.py) not
    in `taken`, as `Name (gender, style)`: what to match a persona against."""
    taken = set(taken)
    return [f"{name} ({gender}, {style})" for name, (gender, style) in sorted(CHIRP3_HD_VOICES.items())
            if name not in taken]


def _voice_key(config: Any) -> Optional[str]:
    """The catalog name of a config's voice (`Puck`), or None."""
    voice = config.get("voice") if isinstance(config, dict) else None
    name = voice.get("name") if isinstance(voice, dict) else None
    return chirp3_voice(name) if isinstance(name, str) else None


def _voices(agent_id: str, root: str, config: Dict[str, Any],
            routes: Optional[Dict[str, Optional[str]]]) -> List[Finding]:
    """A voice of its own, from the catalog, for every agent the voice room switches
    to: there is one voice room, and the voice is how you hear who is answering."""
    taken: Dict[str, List[str]] = {}
    for other in agent_ids(root):
        key = _voice_key(_read_json(os.path.join(root, "agents", other, "agent.json"), None)) if other != agent_id else None
        if key:
            taken.setdefault(key, []).append(other)
    free = ", ".join(free_voices(taken)) or "none"
    where = f"agents/{agent_id}/agent.json"
    if "voice" not in config:
        homes = sorted(channel for channel, agent in (routes or {}).items() if agent == agent_id)
        if not homes:
            return []  # an internal worker, or an agent only in shared channels
        return [Finding("voice_missing", f"the voice room switches to it ({', '.join('#' + h for h in homes)}), "
                        f"so it needs a `voice` of its own; free: {free}", where)]
    voice = config["voice"]
    name = voice.get("name") if isinstance(voice, dict) else voice
    key = _voice_key(config)
    if key is None:
        return [Finding("unknown_voice", f"`{name}` is not a Chirp 3 HD voice (CHIRP3_HD_VOICES in "
                        f"core/voice/foundation/settings.py); free: {free}", where)]
    if key in taken:
        return [Finding("voice_taken", f"`{name}` is already {' and '.join(taken[key])}'s, and every agent needs "
                        f"its own voice; free: {free}", where)]
    return []


def _unloaded_files(agent_dir: str, agent_id: str) -> List[Finding]:
    """Markdown files the prompt never loads. WORKFLOW.md is read on demand, by design."""
    def message(name: str) -> str:
        if name == USER_FILE:
            return f"USER.md belongs in pkm/agents/{agent_id}/USER.md: the prompt reads it there, and the repo is public"
        return f"{name} is not one of the files the prompt loads"

    return [
        Finding("unloaded_file", message(name), f"agents/{agent_id}/{name}")
        for name in sorted(os.listdir(agent_dir))
        if name.lower().endswith(".md") and name not in PERSONA_LIMITS and name != WORKFLOW_FILE
    ]


def _persona_format(agent_dir: str, agent_id: str) -> List[Finding]:
    """The persona files' shape: IDENTITY.md's four lines, and an AGENTS.md (or
    INSTRUCTIONS.md) of operating instructions rather than the design's
    classification table. A file the agent doesn't have isn't checked."""
    findings = []
    identity = _read(os.path.join(agent_dir, "IDENTITY.md"))
    if identity is not None:
        missing = [f for f in IDENTITY_FIELDS if not re.search(rf"^\s*{f}:", identity, re.MULTILINE)]
        if missing:
            findings.append(Finding("persona_format", "IDENTITY.md lacks its " + ", ".join(f"`{f}:`" for f in missing)
                                    + (" lines" if len(missing) > 1 else " line"), f"agents/{agent_id}/IDENTITY.md"))
    for name in ("AGENTS.md", "INSTRUCTIONS.md"):
        text = _read(os.path.join(agent_dir, name))
        if text and _CLASSIFICATION_TABLE.search(text):
            findings.append(Finding("persona_format", f"{name} holds the classification table, which is for the plan "
                                    "and the report; it should be the agent's operating instructions",
                                    f"agents/{agent_id}/{name}"))
    return findings


def _malformed_denies(agent_id: str, root: str, config: Dict[str, Any], skills: Dict[str, Any],
                      catalog: Catalog) -> List[Finding]:
    """`!` keys in agent.json and its skills' skill.json that refuse less than they say."""
    sources = [(f"agents/{agent_id}/agent.json", config.get("tools"))]
    sources += [(f"skills/{s}/skill.json", _skill_json(root, s).get("tools")) for s in sorted(skills)]
    findings = []
    for where, tools in sources:
        for tool, scope in tools.items() if isinstance(tools, dict) else ():
            for key, actions in scope.items() if isinstance(scope, dict) else ():
                if not is_deny(key):
                    continue
                matched = tool in catalog.matched
                problems = deny_problems(key, actions, catalog.tools.get(tool), catalog.bundles.get(tool), matched)
                missing = None if matched else _missing_deny_folder(key, agent_id, root)
                if missing:
                    problems.append(f"{missing} does not exist: check the path for a typo")
                findings += [Finding("malformed_deny", f"`{tool}` `{key}`: {p}", where) for p in problems]
    return findings


def deny_problems(key: str, actions: Any, tool_actions: Optional[Set[str]] = None,
                  bundles: Optional[Set[str]] = None, matched: bool = False) -> List[str]:
    """Why a `!` key refuses less than it says; [] when it is well formed.

    Well formed: a path follows the `!`, and the key lists at least one action,
    each an action of the tool (`tool_actions`) or one of its bundles
    (`bundles`). An action set that is None or empty isn't checked (the tool
    takes no action names); a bundle set of None isn't checked, and an empty
    one means the tool declares none. A tool that matches its own selectors
    (`matched`: Home Assistant) ignores deny keys altogether.
    """
    if matched:
        return ["this tool matches its own selectors, so deny keys don't apply to it"]
    problems = []
    if not key[len(DENY_PREFIX):].strip():
        problems.append("no path after the '!'")
    if not isinstance(actions, list):
        return problems + ["its value must be a list of actions"]
    if not actions:
        problems.append("it lists no action, so it refuses nothing")
    for action in actions:
        if not isinstance(action, str):
            problems.append(f"{action!r} is not an action name")
        elif action.startswith("@"):
            if bundles is not None and action not in bundles:
                known = ", ".join(sorted(bundles)) or "it declares none"
                problems.append(f"`{action}` is not one of its bundles ({known})")
        elif tool_actions and action not in tool_actions:
            problems.append(f"it has no action `{action}`")
    return problems


def _missing_deny_folder(key: str, agent_id: str, root: str) -> Optional[str]:
    """The part of a deny key's path before its first wildcard, when it doesn't exist.

    The key still applies once the folder appears, but a misspelt path never
    does. Resolved the way check_permission resolves it (core/permissions/denies.py).
    """
    path = key[len(DENY_PREFIX):].strip()
    if not path:
        return None
    pattern = os.path.expanduser(path.replace("<agent_id>", agent_id))
    if not os.path.isabs(pattern):
        pattern = os.path.join(root, pattern)
    parts = os.path.normpath(pattern).split(os.sep)
    first_wild = next((i for i, part in enumerate(parts) if any(c in part for c in _WILDCARD)), len(parts))
    fixed = os.sep.join(parts[:first_wild]) or os.sep
    if os.path.exists(fixed):
        return None
    inside = fixed.startswith(os.path.abspath(root) + os.sep)
    return os.path.relpath(fixed, root).replace(os.sep, "/") if inside else fixed


# ---------------------------------------------------------------------------
# The agent's code: its tools, WORKFLOW.md and RULES.md
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OwnTool:
    """One of the agent's own tools (agents/<agent_id>/tools/<name>.py), read from its source."""
    name: str
    agent_tool: bool = False  # the module defines `<name>` under @agent_tool
    docstring: str = ""  # cleaned, as the model reads it
    error: str = ""  # why the source couldn't be read


def own_tool(name: str, source: str) -> OwnTool:
    """What the checks need from a tool module's source, without importing it."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as e:
        return OwnTool(name, error=f"{type(e).__name__}: {e}")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            decorated = any(_decorator_name(d) == "agent_tool" for d in node.decorator_list)
            return OwnTool(name, decorated, ast.get_docstring(node) or "")
    return OwnTool(name)


def own_tools(agent_id: str, root: str = PROJECT_ROOT) -> List[OwnTool]:
    """The modules in agents/<agent_id>/tools/, found the way the contract test finds them."""
    folder = os.path.join(root, "agents", agent_id, "tools")
    tools = []
    for filename in sorted(os.listdir(folder)) if os.path.isdir(folder) else ():
        if filename.endswith(".py") and not filename.startswith("_"):
            name, source = filename[:-3], _read(os.path.join(folder, filename))
            tools.append(own_tool(name, source) if source is not None else OwnTool(name, error="can't be read"))
    return tools


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def read_paths(docstring: str) -> List[str]:
    """The repo and PKM paths on a tool docstring's `Reads:` line, and on the lines that continue it."""
    lines = (docstring or "").splitlines()
    label = DOCSTRING_LINES[0]
    for index, line in enumerate(lines):
        if not line.strip().startswith(label):
            continue
        text = [line.strip()[len(label):]]
        for more in lines[index + 1:]:
            if not more.strip() or _LABEL.match(more.strip()):
                break
            text.append(more.strip())
        return list(dict.fromkeys(path for _, path, _ in _line_paths(" ".join(text))))
    return []


def load_agent_rules(agent_id: str, root: str = PROJECT_ROOT) -> Tuple[Optional[tuple], str]:
    """(RULES, "") from agents/<agent_id>/lib/rules.py, imported from its path.

    (None, "") when the agent has no lib/rules.py; (None, why) when the module
    doesn't import, or RULES isn't a non-empty tuple of Rule.
    """
    path = os.path.join(root, "agents", agent_id, "lib", "rules.py")
    if not os.path.isfile(path):
        return None, ""
    try:
        spec = importlib.util.spec_from_file_location(f"_agent_lint_rules_{agent_package(agent_id)}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:  # code in the middle of a change: a finding, not a crash
        return None, f"doesn't import ({type(e).__name__}: {e})"
    rules = getattr(module, "RULES", None)
    if not isinstance(rules, tuple) or not rules or not all(isinstance(rule, Rule) for rule in rules):
        return None, "RULES must be a non-empty tuple of Rule"
    return rules, ""


def _contract(agent_id: str, root: str, tools: List[OwnTool], rules: Optional[tuple],
              rules_error: str) -> List[Finding]:
    """An agent with tools has a WORKFLOW.md, and keeps the contract the test suite checks.

    `contract_problems` takes tool objects; these tools are read from source, so
    each @agent_tool goes in as a stand-in that carries its name and docstring.
    """
    path = os.path.join(root, "agents", agent_id, WORKFLOW_FILE)
    text = _read(path)
    if text is None:
        if not tools:
            return []
        names = ", ".join(tool.name for tool in tools)
        return [Finding("workflow_missing", f"its tools ({names}) have no {WORKFLOW_FILE}: a `## <tool>` section "
                        "each, with examples (core/tooling/workflow.py)", f"agents/{agent_id}/{WORKFLOW_FILE}")]
    problems = [f"tools/{tool.name}.py: doesn't parse ({tool.error})" for tool in tools if tool.error]
    stand_ins = {tool.name: _stand_in(tool) for tool in tools if not tool.error}
    problems += contract_problems(parse_workflow(text, path), stand_ins, rules)
    if rules_error:
        problems.append(f"lib/rules.py: {rules_error}")
    findings = []
    for problem in problems:
        file, _, message = problem.partition(": ")
        findings.append(Finding("contract_gap", message, f"agents/{agent_id}/{file}"))
    return findings


def _stand_in(tool: OwnTool) -> Optional[AgentTool]:
    """An @agent_tool as contract_problems sees it, its name and docstring; None for any other tool."""
    if not tool.agent_tool:
        return None
    return AgentTool.from_function(func=_stand_in_body, name=tool.name, description=tool.docstring)


def _stand_in_body() -> str:
    return ""


def rules_file_problems(text: str, rules: Sequence[Rule]) -> List[str]:
    """Where a RULES.md's text and lib/rules.py's RULES disagree; [] when they match.

    The tools ignore an unknown key, a value that doesn't read and a line out
    of place (the default applies), and a key the file doesn't set takes its
    default without a word.
    """
    report = Report()
    read_rules_text(text, rules, RULES_FILE, report)
    problems = [issue.problem for issue in report.issues]
    found = set(rule_keys(text))
    problems += [f"doesn't set `{rule.key}`; the default {_rule_value(rule.default)} applies"
                 for rule in rules if rule.key not in found]
    return problems


def _rule_value(default: Any) -> str:
    """A default the way RULES.md writes it."""
    if isinstance(default, bool):
        return "yes" if default else "no"
    if isinstance(default, dict):
        return "table"
    if isinstance(default, (list, tuple)):
        return ", ".join(_rule_value(item) for item in default)
    return str(default)


def _rules_file(agent_id: str, rules: Optional[tuple], rules_error: str) -> List[Finding]:
    """pkm/agents/<agent_id>/RULES.md, when it exists, against lib/rules.py."""
    from core.util.config import Config

    text = _read(rules_path(agent_id, Config().pkm_dir))
    if text is None:
        return []
    where = f"pkm/agents/{agent_id}/{RULES_FILE}"
    if rules_error:
        return [Finding("rules_mismatch", f"can't be checked: lib/rules.py {rules_error}", where)]
    if rules is None:
        keys = rule_keys(text)
        if not keys:
            return []
        named = ", ".join(f"`{key}`" for key in keys)
        return [Finding("rules_mismatch", f"sets {named}, but agents/{agent_id}/lib/rules.py doesn't exist, "
                        "so no tool reads them", where)]
    return [Finding("rules_mismatch", problem, where) for problem in rules_file_problems(text, rules)]


def _unreadable_sources(agent_id: str, root: str, tools: List[OwnTool], grants: Dict[str, Any]) -> List[Finding]:
    """Paths on a tool's `Reads:` line the agent may not read itself. A tool is a shortcut, not the only path."""
    paths = _PathGrants(grants.get("filesystem"), agent_id, root)
    findings = []
    for tool in tools:
        for path in read_paths(tool.docstring):
            allowed = paths.actions(path)
            if allowed is not None and "read" not in allowed:
                findings.append(Finding("read_not_granted", f"`{tool.name}` reads {path}, but no filesystem grant "
                                        "lets the agent read it", f"agents/{agent_id}/tools/{tool.name}.py"))
    return findings


def _user_path(agent_id: str, root: str) -> str:
    """The agent's USER.md: in the configured PKM for this checkout, else under `<root>/pkm`."""
    if os.path.realpath(root) == os.path.realpath(PROJECT_ROOT):
        from core.prompts.system import user_path

        return user_path(agent_id)
    return os.path.join(root, "pkm", "agents", agent_id, USER_FILE)


def _budget(agent_id: str, root: str, config: Dict[str, Any], skills: Dict[str, Any], bound: List[str],
            catalog: Catalog, measure_schemas: bool) -> Tuple[Budget, List[Finding]]:
    from core.prompts.system import get_formatting_prompt

    budget, findings = Budget(tools=list(bound)), []
    files = [(name, os.path.join(root, "agents", agent_id, name), f"agents/{agent_id}/{name}", ceiling)
             for name, (_, ceiling) in PERSONA_LIMITS.items()]
    files.append((USER_FILE, _user_path(agent_id, root), f"pkm/agents/{agent_id}/{USER_FILE}", USER_LIMIT[1]))
    for name, path, where, ceiling in files:
        text = _read(path)
        if text is None:
            continue
        budget.persona[name] = len(text)
        if len(text) > ceiling:
            findings.append(Finding("over_budget", f"{name} is {len(text):,} chars (ceiling {ceiling:,})", where))

    skills_text = 0
    for skill_id, settings in sorted(skills.items()):
        info = _skill_json(root, skill_id)
        skills_text += len(f"- {info.get('name', skill_id)} (id:{skill_id}): {info.get('description', '')}\n")
        mode = settings.get("preload") if isinstance(settings, dict) else None
        if not mode:
            continue
        text = _preloaded_text(root, skill_id, mode)
        if text is None:
            missing = "SKILL.md" if mode == "full" else "'## Core' section"
            findings.append(Finding("preload_missing", f"`{skill_id}` is preloaded as {mode} but has no {missing}",
                                    f"skills/{skill_id}/SKILL.md"))
            continue
        budget.preload[f"{skill_id} ({mode})"] = len(text) // CHARS_PER_TOKEN
        skills_text += len(text)
    if budget.preload_tokens > PRELOAD_TOKEN_BUDGET:
        findings.append(Finding("over_budget", f"preloaded skills are ~{budget.preload_tokens:,} tokens "
                                f"(cap {PRELOAD_TOKEN_BUDGET:,})", f"agents/{agent_id}/agent.json"))

    budget.static_tokens = (len(get_formatting_prompt()) + budget.persona_chars + skills_text) // CHARS_PER_TOKEN
    if measure_schemas and os.path.realpath(root) == os.path.realpath(PROJECT_ROOT):
        budget.schema_tokens, budget.unmeasured = tool_schema_tokens(bound, catalog)
    if config.get("provider") == "local":
        total = budget.static_tokens + (budget.schema_tokens or 0)
        if total > LOCAL_PROMPT_TOKENS:
            findings.append(Finding("over_budget", f"static prompt and tool schemas are ~{total:,} tokens; "
                                    f"an on-device agent fits ~{LOCAL_PROMPT_TOKENS:,}", f"agents/{agent_id}/agent.json"))
    return budget, findings


def tool_schema_tokens(tools: Iterable[str], catalog: Catalog) -> Tuple[int, List[str]]:
    """~Tokens of the schemas the tools send with every request, and the tools that
    couldn't be measured. Imports each tool's module."""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    total, failed = 0, []
    for name in tools:
        try:
            tool = getattr(importlib.import_module(catalog.modules[name]), name)
            total += len(json.dumps(convert_to_openai_tool(tool), ensure_ascii=False)) // CHARS_PER_TOKEN
        except Exception:
            failed.append(name)
    return total, failed


def _schedules(config: Dict[str, Any]) -> List[str]:
    """One line per schedule: id (kind, precondition type, thinking level, cron)."""
    lines = []
    for index, entry in enumerate(config.get("schedules") or []):
        if not isinstance(entry, dict):
            lines.append(f"#{index} (not an object)")
            continue
        precondition = entry.get("precondition")
        parts = [entry.get("kind") or "no kind",
                 precondition.get("type") if isinstance(precondition, dict) else None,
                 entry.get("thinking_level"), f"cron {entry.get('cron')}"]
        lines.append(f"{entry.get('id') or f'#{index}'} ({', '.join(str(p) for p in parts if p)})")
    return lines


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _tool_files(directory: str, package: str) -> Dict[str, Tuple[str, str]]:
    """Name -> (file, dotted module) for the modules of `package` in `directory`, found the
    way ToolsLoader finds tools: its files, and the files one folder down."""
    found = {}
    if not os.path.isdir(directory):
        return found
    for item in sorted(os.listdir(directory)):
        path = os.path.join(directory, item)
        if item.startswith("__"):
            continue
        if os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                if name.endswith(".py") and not name.startswith("__"):
                    found[name[:-3]] = (os.path.join(path, name), f"{package}.{item}.{name[:-3]}")
        elif item.endswith(".py"):
            found[item[:-3]] = (path, f"{package}.{item[:-3]}")
    return found


def permission_declarations(source: str) -> Tuple[Set[str], bool]:
    """(bundle names, declares a matcher) for a tool module, read from its source:
    the `@` keys of its `PERMISSION_BUNDLES`, and whether it defines `PERMISSION_MATCHER`
    (core/permissions/matchers.py)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set(), False
    bundles, matcher = set(), False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "PERMISSION_MATCHER":
            matcher = True
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if "PERMISSION_BUNDLES" in names and isinstance(node.value, ast.Dict):
            bundles |= {name for name in _strings(node.value) if name.startswith("@")}
        matcher = matcher or "PERMISSION_MATCHER" in names
    return bundles, matcher


def actions_in_module(source: str, tool: str) -> Set[str]:
    """A tool's action names, read from its source without importing it.

    Taken from comparisons against `action` (`action == "read"`, `norm_action in
    ("search", "cached_search")`), the default of the tool's `action` argument,
    literal action tables (`READ_ACTIONS`, ...) and `PERMISSION_BUNDLES` members.
    Empty for a tool that takes no action names.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    actions: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) \
                and re.fullmatch(r"(?:\w+_)?action", node.left.id):
            for comparator in node.comparators:
                actions |= _strings(comparator)
        elif isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & _ACTION_TABLES:
                actions |= _strings(node.value)
            if "PERMISSION_BUNDLES" in names and isinstance(node.value, ast.Dict):
                actions |= {a for members in node.value.values for a in _strings(members)}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == tool:
            args = node.args.posonlyargs + node.args.args
            pairs = list(zip(args[len(args) - len(node.args.defaults):], node.args.defaults))
            pairs += [(a, d) for a, d in zip(node.args.kwonlyargs, node.args.kw_defaults) if d is not None]
            actions |= {s for a, d in pairs if a.arg == "action" for s in _strings(d)}
    return {a for a in actions if re.fullmatch(_ID, a)}


def _strings(node: ast.AST) -> Set[str]:
    """The string constants of a literal: a string, a tuple, list or set of them, a
    dict's keys, or frozenset/set/sorted/tuple/list of one of those."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    if isinstance(node, ast.Dict):
        return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.args \
            and node.func.id in ("frozenset", "set", "sorted", "tuple", "list"):
        return _strings(node.args[0])
    return set()


def _expand(tool: str, bundle: str) -> List[str]:
    from core.permissions.bundles import expand_actions
    return [a for a in expand_actions(tool, [bundle]) if not a.startswith("@")]


def _preloaded_text(root: str, skill_id: str, mode: str) -> Optional[str]:
    """SkillsLoader.preloaded_text: the body, or the Core section; None if missing."""
    if not os.path.isfile(os.path.join(root, "skills", skill_id, "skill.json")):
        return None
    text = _read(os.path.join(root, "skills", skill_id, "SKILL.md"))
    if text is None:
        return None
    return skill_body(text) if mode == "full" else core_section(text)


def _graph_configs(root: str) -> Dict[str, Dict[str, Any]]:
    """Graph id -> graph.json, keyed the way GraphsLoader keys them."""
    graphs = {}
    graphs_dir = os.path.join(root, "graphs")
    for item in sorted(os.listdir(graphs_dir)) if os.path.isdir(graphs_dir) else ():
        config = _read_json(os.path.join(graphs_dir, item, "graph.json"), None)
        if isinstance(config, dict):
            graphs[config.get("graph_id") or config.get("id") or item] = config
    return graphs


def _skill_json(root: str, skill_id: str) -> Dict[str, Any]:
    config = _read_json(os.path.join(root, "skills", skill_id, "skill.json"), {})
    return config if isinstance(config, dict) else {}


def _read(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


_MISSING = object()


def _read_json(path: str, default: Any = _MISSING) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        if default is _MISSING:
            raise
        return default


def _collapse(findings: List[Finding]) -> List[Tuple[Finding, str]]:
    """One line per (kind, message), citing the first place and how many more."""
    groups: Dict[Tuple[str, str], List[Finding]] = {}
    for finding in findings:
        groups.setdefault((finding.kind, finding.message), []).append(finding)
    ordered = sorted(groups.values(), key=lambda g: (KINDS.index(g[0].kind), g[0].message))
    return [(g[0], g[0].where + (f", +{len(g) - 1} more" if len(g) > 1 else "")) for g in ordered]
