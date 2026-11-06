#!/usr/bin/env python3
"""Sets a project's GitHub repository up for the coding graph's deploy step, through `gh`.

The graph only watches deployments: the repository has to deploy itself. For
GitHub Pages that takes three things, which this does in one run:

  1. Pages switched on, with "GitHub Actions" as its source.
  2. A workflow on the default branch that builds the site and deploys it on
     every push, in one job that reports to the `github-pages` environment the
     graph's watcher reads. A Vite app is built with `--base=/<repo>/`, so the
     app's own code doesn't change.
  3. With --project or --manifest: the manifest's `deploy` block, so the graph
     checks every merge.

Then it waits for the first deployment and prints the URL.

It runs as you, not as the bot: `gh` must be logged in with admin on the
repository (switching Pages on needs it) and the `workflow` scope (committing
a workflow needs it). The workflow is committed straight to the default branch.
Every step looks before it acts, so running it again changes nothing, and
--dry-run only looks. The workflow carries a digest of itself: a re-run
refreshes it until someone edits it by hand, then leaves it alone (--force
replaces it anyway).

Usage:
    scripts/coding_deploy_setup.py --project NAME [--dry-run] [--no-wait]
    scripts/coding_deploy_setup.py --repo OWNER/NAME [--dry-run] [--no-wait]
    Overrides: --base PATH  --build CMD  --dist DIR  --timeout SECONDS  --force

Exit code 0 means the project deploys on every merge and, unless --no-wait or
--dry-run, that its first deployment is live. 1 means it doesn't, and the ✗
line says why. 2 is a usage error.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bootstrap import ensure_project_interpreter, enter_project_root  # noqa: E402
from _bootstrap import project_root as _project_root  # noqa: E402

project_root = _project_root()
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Every GitHub call goes through `GH(args, stdin) -> (exit code, stdout,
# stderr)`, and the deployment is watched through `make_watcher()`, so a test
# can hand in a fake GitHub instead of patching subprocess.
GH: Any = None

WORKFLOW_PATH = ".github/workflows/pages.yml"
WORKFLOW_FILE = os.path.basename(WORKFLOW_PATH)
PAGES_ENVIRONMENT = "github-pages"
# Each action's current major version (their latest releases, 2026-11).
ACTIONS = {
    "checkout": "actions/checkout@v7",
    "setup-node": "actions/setup-node@v7",
    "configure-pages": "actions/configure-pages@v6",
    "upload-pages-artifact": "actions/upload-pages-artifact@v5",
    "deploy-pages": "actions/deploy-pages@v5",
}
MARKER = "# Managed by scripts/coding_deploy_setup.py"
MARKER_RE = re.compile(r"^# Managed by scripts/coding_deploy_setup\.py \(digest ([0-9a-f]{12})\)")

VITE_CONFIGS = ("vite.config.ts", "vite.config.mts", "vite.config.cts",
                "vite.config.js", "vite.config.mjs", "vite.config.cjs")
OTHER_LOCKFILES = ("pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb")
NODE_VERSION_FILES = (".nvmrc", ".node-version")
VITE_BUILD = re.compile(r"^(?:npx\s+)?vite\s+build\b")
# A value YAML reads back exactly as written, without quotes.
PLAIN_SCALAR = re.compile(r"^[A-Za-z0-9_./~][A-Za-z0-9_./=@+,~ -]*$")

POLL_SECONDS = 10.0
# What the wait for the first deployment measures its timeout with.
clock = time.monotonic
DEFAULT_TIMEOUT_SECONDS = 600
GH_TIMEOUT_SECONDS = 60
LOGIN_HINT = " (is `gh` logged in? `gh auth status`)"
SCOPE_HINT = " (committing a workflow needs the `workflow` scope: `gh auth refresh -h github.com -s workflow`)"


class Report:
    """Prints each result as it comes, numbering the sections; remembers whether anything failed."""

    def __init__(self) -> None:
        self.failed = False
        self.sections = 0

    def section(self, title: str) -> None:
        self.sections += 1
        print(f"\n{self.sections}. {title}")

    def ok(self, message: str) -> None:
        print(f"  ✓ {message}")

    def did(self, message: str) -> None:
        print(f"  → {message}")

    def would(self, message: str) -> None:
        print(f"  ○ would {message}")

    def warn(self, message: str) -> None:
        print(f"  ⚠ {message}")

    def fail(self, message: str) -> None:
        self.failed = True
        print(f"  ✗ {message}")

    def info(self, message: str) -> None:
        print(f"    {message}")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Set a project's GitHub repository up for the coding graph's deploy step."
    )
    parser.add_argument("--project", default="",
                        help="The project in pkm/wiki/software/<project>/build_request.json.")
    parser.add_argument("--manifest", default="", help="The project in this build_request.json.")
    parser.add_argument("--repo", default="",
                        help="OWNER/NAME on GitHub: without a manifest, or instead of its repo.slug.")
    parser.add_argument("--dry-run", action="store_true", help="Only look, and say what would change.")
    parser.add_argument("--no-wait", action="store_true",
                        help="Don't wait for the first deployment to go live.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
                        help=f"How long to wait for it, in seconds (default {DEFAULT_TIMEOUT_SECONDS}).")
    parser.add_argument("--force", action="store_true",
                        help=f"Replace {WORKFLOW_PATH} even if it was edited by hand.")
    parser.add_argument("--base", default="",
                        help="The path the site is served under (default /<repo>/, or / for a custom domain).")
    parser.add_argument("--build", default="", help="The build command (default: worked out from package.json).")
    parser.add_argument("--dist", default="", help="The folder the build writes the site to (default dist).")
    return parser.parse_args(argv)


# --- GitHub, through gh -----------------------------------------------------

class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _run_gh(args: List[str], stdin: Optional[str] = None) -> Tuple[int, str, str]:
    try:
        done = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True,
                              timeout=GH_TIMEOUT_SECONDS)
    except FileNotFoundError:
        return 127, "", "the GitHub CLI (gh) is not installed"
    except subprocess.TimeoutExpired:
        return 124, "", f"gh took longer than {GH_TIMEOUT_SECONDS}s"
    return done.returncode, done.stdout, done.stderr


def api(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
    """One GitHub API call through `gh`: the parsed answer (None when empty), or GitHubError."""
    args = ["api", "-X", method, "-H", "Accept: application/vnd.github+json", path]
    stdin = None
    if body is not None:
        args += ["--input", "-"]
        stdin = json.dumps(body)
    code, out, err = (GH or _run_gh)(args, stdin)
    if code != 0:
        status = re.search(r"\(HTTP (\d{3})\)", err or "")
        raise GitHubError(int(status.group(1)) if status else 0,
                          _api_message(out) or (err or "").strip() or f"gh exited {code}")
    text = (out or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError as e:
        raise GitHubError(0, f"unreadable answer from GitHub ({e})") from e


def _api_message(out: str) -> str:
    """GitHub's own explanation, from an error's body."""
    try:
        data = json.loads(out or "")
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    details = [str(e.get("message") if isinstance(e, dict) else e) for e in data.get("errors") or []]
    return "; ".join([str(data.get("message") or "")] + [d for d in details if d]).strip("; ")


def get_or_none(path: str) -> Any:
    """GET, with "not found" as None rather than an error."""
    try:
        return api("GET", path)
    except GitHubError as e:
        if e.status == 404:
            return None
        raise


def contents_path(slug: str, path: str, ref: str) -> str:
    return f"repos/{slug}/contents{'/' + quote(path) if path else ''}?ref={quote(ref, safe='')}"


def read_file(slug: str, path: str, ref: str) -> Optional[Tuple[str, str]]:
    """(text, blob sha) of a file on `ref`; None when there is no such file."""
    data = get_or_none(contents_path(slug, path, ref))
    if not isinstance(data, dict) or data.get("type", "file") != "file":
        return None
    text = str(data.get("content") or "")
    if data.get("encoding") == "base64":
        text = base64.b64decode(text).decode("utf-8", errors="replace")
    return text, str(data.get("sha") or "")


def list_files(slug: str, path: str, ref: str) -> List[str]:
    data = get_or_none(contents_path(slug, path, ref))
    if not isinstance(data, list):
        return []
    return sorted(str(e.get("name")) for e in data if isinstance(e, dict) and e.get("type") == "file")


def head_sha(slug: str, branch: str) -> str:
    data = api("GET", f"repos/{slug}/commits/{quote(branch, safe='')}")
    return str((data or {}).get("sha") or "")


def latest_deployment_id(slug: str, sha: str) -> Optional[int]:
    """The newest Pages deployment of `sha`, if any."""
    data = api("GET", f"repos/{slug}/deployments?sha={sha}&environment={PAGES_ENVIRONMENT}&per_page=1")
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("id") is not None:
        return int(data[0]["id"])
    return None


class RepoFiles:
    """The default branch's files, read through the contents API as they are needed."""

    def __init__(self, slug: str, ref: str):
        self.slug, self.ref = slug, ref
        self._names: Optional[Set[str]] = None
        self._texts: Dict[str, Optional[str]] = {}

    def names(self) -> Set[str]:
        """What is at the top of the tree; empty for an empty repository."""
        if self._names is None:
            data = get_or_none(contents_path(self.slug, "", self.ref))
            self._names = ({str(e.get("name")) for e in data if isinstance(e, dict)}
                           if isinstance(data, list) else set())
        return self._names

    def text(self, path: str) -> Optional[str]:
        if "/" not in path and path not in self.names():
            return None
        if path not in self._texts:
            found = read_file(self.slug, path, self.ref)
            self._texts[path] = found[0] if found else None
        return self._texts[path]


# --- what to build ----------------------------------------------------------

@dataclass
class BuildPlan:
    kind: str                        # "vite" | "node" | "static"
    install: str = ""
    build: str = ""
    dist: str = "."
    cache: bool = False              # setup-node's npm cache needs package-lock.json
    node_version_file: str = ""
    notes: List[str] = field(default_factory=list)
    problem: str = ""                # set when the site can't be built as found

    def describe(self) -> str:
        folder = self.dist.rstrip("/") + "/"
        if self.kind == "static":
            if self.build:
                return f"static site: `{self.build}`, publishing `{folder}`"
            return ("static site: publishes the repository as it is" if self.dist in (".", "./")
                    else f"static site: publishes `{folder}`")
        label = "Vite app" if self.kind == "vite" else "Node app"
        return f"{label}: `{self.install}`, then `{self.build}`, publishing `{folder}`"


def site_base(slug: str, pages: Optional[Dict[str, Any]], override: str = "") -> str:
    """The path the site is served under, which a Vite build needs as `base`."""
    if override:
        inner = override.strip().strip("/")
        return f"/{inner}/" if inner else "/"
    if pages and pages.get("cname"):
        return "/"  # a custom domain serves from its root
    owner, _, name = slug.partition("/")
    if name.lower() == f"{owner.lower()}.github.io":
        return "/"  # the owner's own site
    return f"/{name}/"


def default_url(slug: str, base: str) -> str:
    return f"https://{slug.partition('/')[0].lower()}.github.io{base}"


def _ends_with_vite_build(script: str) -> bool:
    """Whether arguments after `npm run build --` reach `vite build`: it has to run last."""
    last = re.split(r"&&|\|\||;", script)[-1].strip()
    return bool(VITE_BUILD.match(last))


def plan_build(files: RepoFiles, base: str, build: str = "", dist: str = "") -> BuildPlan:
    """How the workflow builds the site, worked out from the files on the default branch."""
    names = files.names()
    package_text = files.text("package.json")
    if package_text is None:
        return BuildPlan(kind="static", build=build, dist=dist or ".")

    try:
        package = json.loads(package_text)
    except ValueError:
        return BuildPlan(kind="node", problem="package.json isn't valid JSON")
    if not isinstance(package, dict):
        return BuildPlan(kind="node", problem="package.json isn't a JSON object")

    has_lock = "package-lock.json" in names
    other_locks = [n for n in OTHER_LOCKFILES if n in names]
    if other_locks and not has_lock:
        return BuildPlan(kind="node", problem=(
            f"found {other_locks[0]}: only npm projects are set up automatically. Commit a "
            "package-lock.json, or write the workflow by hand (the graph watches any workflow "
            "that deploys with actions/deploy-pages)"))

    dependencies = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
    plan = BuildPlan(
        kind="vite" if "vite" in dependencies else "node",
        install="npm ci" if has_lock else "npm install",
        cache=has_lock,
        dist=dist or "dist",
        node_version_file=next((n for n in NODE_VERSION_FILES if n in names), ""),
    )
    if not has_lock:
        plan.notes.append("no package-lock.json: the workflow runs `npm install`, unpinned, and the "
                          "E2B setup's `npm ci` fails without one. Commit the lockfile")

    script = str((package.get("scripts") or {}).get("build") or "")
    if build:
        plan.build = build
    elif not script:
        plan.problem = "package.json has no `build` script: add one, or pass --build"
    elif plan.kind == "vite":
        config_name = next((n for n in VITE_CONFIGS if n in names), "")
        config = (files.text(config_name) if config_name else None) or ""
        if re.search(r"\bbase\s*:", config):
            plan.build = "npm run build"
            plan.notes.append(f"{config_name} sets `base` itself: it has to be `{base}`")
        elif _ends_with_vite_build(script):
            plan.build = f"npm run build -- --base={base}"
        else:
            plan.build = "npm run build"
            plan.notes.append(f"the build script doesn't end in `vite build`, so `--base` can't be "
                              f"passed to it: set `base: '{base}'` in vite.config")
        if re.search(r"\boutDir\s*:", config) and not dist:
            plan.notes.append(f"{config_name} sets `build.outDir`: pass --dist if it isn't `dist`")
    else:
        plan.build = "npm run build"
        if not dist:
            plan.notes.append(f"not a Vite app: check that `npm run build` writes the site to dist/ "
                              f"(or pass --dist) and that it works when served under {base}")
    return plan


# --- the workflow -----------------------------------------------------------

def _scalar(value: str) -> str:
    return value if PLAIN_SCALAR.match(value) and not value.endswith(" ") else json.dumps(value)


def _workflow_body(plan: BuildPlan, branch: str) -> str:
    lines = [
        "# Running the script again refreshes this file; once it is edited by hand, it is left alone.",
        "name: Deploy to GitHub Pages",
        "",
        "on:",
        "  push:",
        "    branches:",
        f"      - {_scalar(branch)}",
        "  workflow_dispatch:",
        "",
        "permissions:",
        "  contents: read",
        "  pages: write",
        "  id-token: write",
        "",
        "# One deploy at a time, and a started one always finishes, so every commit the",
        "# coding graph waits on gets a real status.",
        "concurrency:",
        "  group: pages",
        "  cancel-in-progress: false",
        "",
        "jobs:",
        "  # One job, so a failed build fails the deployment itself and the coding graph",
        "  # hears about it at once rather than after its timeout.",
        "  deploy:",
        "    environment:",
        f"      name: {PAGES_ENVIRONMENT}",
        "      url: ${{ steps.deployment.outputs.page_url }}",
        "    runs-on: ubuntu-latest",
        "    steps:",
        f"      - uses: {ACTIONS['checkout']}",
    ]
    if plan.kind in ("vite", "node"):
        lines += [f"      - uses: {ACTIONS['setup-node']}", "        with:"]
        lines.append(f"          node-version-file: {_scalar(plan.node_version_file)}"
                     if plan.node_version_file else "          node-version: lts/*")
        if plan.cache:
            lines.append("          cache: npm")
        lines.append(f"      - run: {_scalar(plan.install)}")
    if plan.build:
        lines.append(f"      - run: {_scalar(plan.build)}")
    lines += [
        f"      - uses: {ACTIONS['configure-pages']}",
        f"      - uses: {ACTIONS['upload-pages-artifact']}",
        "        with:",
        f"          path: {_scalar(plan.dist)}",
        "      - id: deployment",
        f"        uses: {ACTIONS['deploy-pages']}",
    ]
    return "\n".join(lines) + "\n"


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def render_workflow(plan: BuildPlan, branch: str) -> str:
    body = _workflow_body(plan, branch)
    return f"{MARKER} (digest {_digest(body)}).\n{body}"


def workflow_state(text: str) -> str:
    """'managed' (written here, untouched since), 'edited' (written here, then by hand) or 'foreign'."""
    first, _, rest = text.partition("\n")
    match = MARKER_RE.match(first)
    if not match:
        return "foreign"
    return "managed" if _digest(rest) == match.group(1) else "edited"


# --- the steps --------------------------------------------------------------

@dataclass
class Target:
    slug: str
    project: str
    manifest_path: str = ""
    manifest: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Outcome:
    ok: bool = False
    branch: str = ""
    url: str = ""
    commit_sha: str = ""                     # the workflow commit this run made, if any
    started_sha: str = ""                    # the commit a workflow was started on by hand, if any
    stale_deployment: Optional[int] = None   # that commit's deployment from before Pages was on


def resolve_target(args) -> Tuple[Optional[Target], str]:
    """The repository to set up, and the manifest that names it if there is one."""
    from graphs.coding.utils.dag import manifest_path_for_project
    from graphs.coding.utils.manifest import load_manifest
    from graphs.coding.utils.repo import get_repo_descriptor

    if args.repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
        return None, "--repo must be OWNER/NAME."
    path = ""
    if args.manifest:
        path = os.path.abspath(os.path.expanduser(args.manifest))
    elif args.project:
        path = manifest_path_for_project(args.project)
    if not path:
        if args.repo:
            return Target(slug=args.repo, project=args.repo.partition("/")[2]), ""
        return None, "Say which project: --project NAME, --manifest PATH or --repo OWNER/NAME."
    if not os.path.exists(path):
        return None, f"No manifest at {path}."
    try:
        manifest = load_manifest(path)
    except ValueError as e:
        return None, str(e)
    slug = args.repo or get_repo_descriptor(manifest).get("slug") or ""
    if not slug:
        return None, f"{path} names no `repo.slug`: pass --repo OWNER/NAME."
    return Target(slug=slug, project=str(manifest.get("project_name") or args.project or slug),
                  manifest_path=path, manifest=manifest), ""


def chosen_backend(target: Target) -> Tuple[str, str]:
    """(backend to set up, or why not): the manifest's `deploy.backend`, GitHub Pages by default."""
    from graphs.coding.utils.deploy import deploy_backend

    name = deploy_backend(target.manifest.get("deploy"))
    if not name:
        return "github_pages", ""
    if name == "none":
        return "", ("The manifest's `deploy.backend` is `none`: the project asked not to be "
                    "deployed. Change that first.")
    if name not in SETUPS:
        return "", (f"Setting up `{name}` isn't automated yet (this sets up {', '.join(sorted(SETUPS))}). "
                    "For `github_deployment`, add a workflow job that deploys with "
                    "`environment: <deploy.environment>`: the graph watches that environment.")
    return name, ""


def ensure_pages(report: Report, slug: str, pages: Optional[Dict[str, Any]],
                 url: str, dry_run: bool) -> Tuple[bool, bool, str]:
    """(ok, changed, url): Pages on, with GitHub Actions as its source."""
    if pages is None:
        if dry_run:
            report.would("switch GitHub Pages on, with GitHub Actions as its source")
            return True, True, url
        try:
            created = api("POST", f"repos/{slug}/pages", {"build_type": "workflow"})
        except GitHubError as e:
            report.fail(f"couldn't switch Pages on: {e.message}")
            return False, False, url
        url = str((created or {}).get("html_url") or url)
        report.did(f"switched GitHub Pages on, source GitHub Actions: {url}")
        return True, True, url

    if pages.get("build_type") != "workflow":
        branch = (pages.get("source") or {}).get("branch") or "a branch"
        if dry_run:
            report.would(f"switch Pages' source from `{branch}` to GitHub Actions")
            return True, True, url
        try:
            api("PUT", f"repos/{slug}/pages", {"build_type": "workflow"})
        except GitHubError as e:
            report.fail(f"couldn't switch Pages' source to GitHub Actions: {e.message}")
            return False, False, url
        report.did(f"switched Pages' source from `{branch}` to GitHub Actions")
        return True, True, url

    report.ok(f"on, source GitHub Actions: {url}")
    return True, False, url


def other_pages_workflows(slug: str, branch: str) -> List[Tuple[str, str]]:
    """(file name, text) of workflows besides ours that already deploy to Pages."""
    found = []
    for name in list_files(slug, ".github/workflows", branch):
        if name == WORKFLOW_FILE or not name.endswith((".yml", ".yaml")):
            continue
        file = read_file(slug, f".github/workflows/{name}", branch)
        if file and "actions/deploy-pages" in file[0]:
            found.append((name, file[0]))
    return found


def ensure_workflow(report: Report, slug: str, branch: str, workflow: str,
                    dry_run: bool, force: bool) -> Tuple[bool, bool, str, str]:
    """(ok, changed, sha of a commit it made, a deploying workflow that can be started by hand).

    `changed` is true when it committed the workflow, or would have in a dry
    run: that push runs the workflow by itself.
    """
    existing = read_file(slug, WORKFLOW_PATH, branch)
    if existing is None:
        others = other_pages_workflows(slug, branch)
        if others:
            name, text = others[0]
            report.ok(f".github/workflows/{name} already deploys to Pages, so {WORKFLOW_FILE} isn't added")
            return True, False, "", name if "workflow_dispatch" in text else ""
        ok, sha = commit_workflow(report, slug, branch, workflow, "", dry_run)
        return ok, ok, sha, WORKFLOW_FILE

    text, blob_sha = existing
    if text == workflow:
        report.ok(f"{WORKFLOW_PATH} is up to date")
        return True, False, "", WORKFLOW_FILE
    state = workflow_state(text)
    if state != "managed" and not force:
        report.warn(f"{WORKFLOW_PATH} was " + ("edited by hand" if state == "edited" else "not written here")
                    + ", so it's left as it is (--force replaces it)")
        return True, False, "", WORKFLOW_FILE if "workflow_dispatch" in text else ""
    ok, sha = commit_workflow(report, slug, branch, workflow, blob_sha, dry_run)
    return ok, ok, sha, WORKFLOW_FILE


def commit_workflow(report: Report, slug: str, branch: str, workflow: str,
                    blob_sha: str, dry_run: bool) -> Tuple[bool, str]:
    verb = "update" if blob_sha else "commit"
    if dry_run:
        report.would(f"{verb} {WORKFLOW_PATH} on `{branch}`:")
        for line in workflow.splitlines():
            report.info(f"│ {line}")
        return True, ""
    body = {
        "message": (f"Deploy to GitHub Pages on every push to {branch}\n\n"
                    "Set up by aoc's scripts/coding_deploy_setup.py: one job builds the site and "
                    "deploys it to the github-pages environment, which the coding graph watches."),
        "content": base64.b64encode(workflow.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if blob_sha:
        body["sha"] = blob_sha
    try:
        answer = api("PUT", f"repos/{slug}/contents/{quote(WORKFLOW_PATH)}", body)
    except GitHubError as e:
        report.fail(f"couldn't {verb} {WORKFLOW_PATH}: {e.message}"
                    + (SCOPE_HINT if e.status in (403, 404) else ""))
        return False, ""
    sha = str(((answer or {}).get("commit") or {}).get("sha") or "")
    report.did(f"{'updated' if blob_sha else 'committed'} {WORKFLOW_PATH} on `{branch}` ({sha[:7]})")
    return True, sha


def start_workflow(report: Report, slug: str, branch: str, workflow_file: str,
                   outcome: Outcome, dry_run: bool) -> None:
    """Runs the workflow once by hand: its last run came before Pages was on, so it deployed nothing."""
    if dry_run:
        report.would(f"start {workflow_file} once, since its last run came before Pages was on")
        return
    outcome.started_sha = head_sha(slug, branch)
    # That run may have left a failed deployment of this very commit behind,
    # which mustn't be mistaken for the new run's.
    outcome.stale_deployment = latest_deployment_id(slug, outcome.started_sha) if outcome.started_sha else None
    api("POST", f"repos/{slug}/actions/workflows/{quote(workflow_file)}/dispatches", {"ref": branch})
    report.did(f"started {workflow_file}, since its last run came before Pages was on")


def ensure_deploy_block(report: Report, target: Target, dry_run: bool) -> None:
    from graphs.coding.utils import manifest as manifest_store
    from graphs.coding.utils.deploy import deploy_backend

    if deploy_backend(target.manifest.get("deploy")) == "github_pages":
        report.ok("`deploy.backend` is `github_pages`: the graph checks every merge")
        return
    if dry_run:
        report.would('add `"deploy": {"backend": "github_pages"}` to the manifest')
        return
    # Unmigrated, so the write changes this block and nothing else.
    with manifest_store.locked_manifest(target.manifest_path, migrate=False) as data:
        block = data.get("deploy") if isinstance(data.get("deploy"), dict) else {}
        data["deploy"] = {**block, "backend": "github_pages"}
    report.did(f'added `"deploy": {{"backend": "github_pages"}}` to {os.path.relpath(target.manifest_path)}')
    report.info("Sync pkm so the ticking host sees it: scripts/sync_git.py --skip-codebase --prefer local")


async def setup_github_pages(report: Report, target: Target, args) -> Outcome:
    slug = target.slug
    outcome = Outcome()

    report.section("Repository")
    try:
        repo = api("GET", f"repos/{slug}") or {}
    except GitHubError as e:
        report.fail(f"can't read {slug}: {e.message}" + (LOGIN_HINT if e.status in (0, 401) else ""))
        return outcome
    branch = outcome.branch = str(repo.get("default_branch") or "main")
    if not (repo.get("permissions") or {}).get("admin"):
        report.fail(f"`gh` is logged in without admin on {slug}, which switching Pages on needs: "
                    "run this as the repository's owner (`gh auth status`)")
        return outcome
    report.ok(f"{slug}: {'private' if repo.get('private') else 'public'}, "
              f"default branch `{branch}`, admin access")
    if repo.get("private"):
        report.warn("Pages on a private repository needs a paid GitHub plan, and the site itself is public")
    declared = str((target.manifest.get("repo") or {}).get("default_branch") or "")
    if declared and declared != branch:
        report.warn(f"the manifest says `{declared}` but GitHub says `{branch}`: the workflow deploys `{branch}`")

    try:
        pages = get_or_none(f"repos/{slug}/pages")
        base = site_base(slug, pages, args.base)

        report.section("Build")
        files = RepoFiles(slug, branch)
        if not files.names():
            report.fail(f"`{branch}` has no files yet: run this once the project's first task has merged")
            return outcome
        plan = plan_build(files, base, args.build, args.dist)
        if plan.problem:
            report.fail(plan.problem)
            return outcome
        report.ok(plan.describe())
        for note in plan.notes:
            report.warn(note)

        report.section("GitHub Pages")
        url = str((pages or {}).get("html_url") or default_url(slug, base))
        ok, pages_changed, outcome.url = ensure_pages(report, slug, pages, url, args.dry_run)
        if not ok:
            return outcome

        report.section("Workflow")
        ok, pushed, outcome.commit_sha, startable = ensure_workflow(
            report, slug, branch, render_workflow(plan, branch), args.dry_run, args.force)
        if not ok:
            return outcome
        if pages_changed and not pushed and startable:
            start_workflow(report, slug, branch, startable, outcome, args.dry_run)
    except GitHubError as e:
        report.fail(f"GitHub said: {e.message}" + (LOGIN_HINT if e.status in (0, 401) else ""))
        return outcome

    if target.manifest_path:
        report.section("Manifest")
        ensure_deploy_block(report, target, args.dry_run)

    outcome.ok = not report.failed
    return outcome


SETUPS = {"github_pages": setup_github_pages}


# --- the first deployment ---------------------------------------------------

def make_watcher():
    """The watcher the graph's deploy node uses, so this sees what the graph will see."""
    from graphs.coding.utils.deploy.github_deployments import GitHubPagesWatcher

    return GitHubPagesWatcher()


async def wait_until_live(report: Report, slug: str, branch: str, sha: str, timeout: int,
                          stale_deployment: Optional[int] = None) -> Tuple[bool, str]:
    """(live, url) for `sha`, asking every POLL_SECONDS until it's live, failed or out of time."""
    from graphs.coding.utils.deploy import FAILED, LIVE, DeployTarget

    watcher = make_watcher()
    target = DeployTarget(repo=slug, sha=sha, branch=branch, config={"backend": "github_pages"})
    minutes = max(1, round(timeout / 60))
    deadline = clock() + timeout
    last = ""
    report.info(f"waiting for `{sha[:7]}` to go live (up to {minutes} min)")
    while True:
        if stale_deployment is not None and latest_deployment_id(slug, sha) == stale_deployment:
            news = "waiting for the new run to start"
        else:
            stale_deployment = None
            status = await watcher.advance(target)
            if status.state == LIVE:
                report.ok(f"live at {status.url}")
                return True, status.url
            if status.state == FAILED:
                report.fail(f"the deployment of `{sha[:7]}` failed: {status.detail or 'no reason given'}"
                            + (f" ({status.log_url})" if status.log_url else ""))
                return False, ""
            news = status.infra_error or status.detail or ""
        if news and news != last:
            report.info(f"· {news}")
            last = news
        if clock() >= deadline:
            report.fail(f"`{sha[:7]}` isn't live after {minutes} min: "
                        f"see https://github.com/{slug}/actions")
            return False, ""
        await asyncio.sleep(POLL_SECONDS)


async def first_deployment(report: Report, target: Target, outcome: Outcome, timeout: int) -> Tuple[bool, str]:
    report.section("First deployment")
    try:
        sha = outcome.commit_sha or outcome.started_sha or head_sha(target.slug, outcome.branch)
        if not sha:
            report.fail(f"GitHub named no commit for `{outcome.branch}`")
            return False, ""
        return await wait_until_live(report, target.slug, outcome.branch, sha, timeout,
                                     None if outcome.commit_sha else outcome.stale_deployment)
    except GitHubError as e:
        report.fail(f"couldn't follow the first deployment: {e.message}")
        return False, ""


async def main(argv=None) -> int:
    args = parse_args(argv)
    if args.timeout <= 0:
        print("--timeout must be a positive number of seconds.", file=sys.stderr)
        return 2
    target, problem = resolve_target(args)
    if target is None:
        print(problem, file=sys.stderr)
        return 2
    backend, problem = chosen_backend(target)
    if problem:
        print(problem, file=sys.stderr)
        return 1

    report = Report()
    print(f"Deploy setup for {target.project} ({target.slug}): GitHub Pages"
          + (" — dry run, nothing will change" if args.dry_run else ""))
    outcome = await SETUPS[backend](report, target, args)
    if not outcome.ok:
        print("\n✗ Not set up: fix the ✗ line above and run this again (every step is safe to repeat).")
        return 1
    if args.dry_run:
        print("\n○ Dry run: nothing was changed. Run it again without --dry-run to apply.")
        return 0
    if args.no_wait:
        print(f"\n✓ {target.project} deploys to GitHub Pages on every push to `{outcome.branch}`: "
              f"{outcome.url} (first deployment: https://github.com/{target.slug}/actions)")
        return 0

    live, url = await first_deployment(report, target, outcome, args.timeout)
    if not live:
        print("\n✗ Set up, but the first deployment isn't live: see the ✗ line above. "
              "The graph also reports every merge's deployment.")
        return 1
    print(f"\n✓ {target.project} deploys to GitHub Pages on every push to `{outcome.branch}`: {url}")
    return 0


if __name__ == "__main__":
    ensure_project_interpreter()
    enter_project_root()
    sys.exit(asyncio.run(main()))
