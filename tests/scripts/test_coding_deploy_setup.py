"""The deploy setup script.

It changes a real repository on GitHub, as its owner, so what has to hold:

- it looks before it acts: a second run changes nothing, a dry run only looks,
  and nothing is touched without admin;
- the workflow it commits builds the site the way the project needs (a Vite
  app under its Pages path, without editing the app) and deploys in one job to
  the `github-pages` environment, which the graph's watcher reads;
- a workflow someone edited, or another workflow that already deploys, is left
  alone;
- exit 0 means live, or set up when not waiting; anything else is 1 with the
  reason, or 2 for a usage error.

Everything runs against an in-memory GitHub and a fake watcher: no test calls
`gh`.
"""
import base64
import importlib.util
import io
import itertools
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from urllib.parse import parse_qs, unquote

import yaml

from graphs.coding.utils.deploy import DeployStatus

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT_PATH = os.path.join(PROJECT_ROOT, "scripts", "coding_deploy_setup.py")

SLUG = "example/cards"
URL = "https://example.github.io/cards/"
VITE_PACKAGE = {"name": "cards", "type": "module",
                "scripts": {"dev": "vite", "build": "tsc -b && vite build"},
                "devDependencies": {"vite": "^8.3.6", "typescript": "~7.0.2"}}
VITE_APP = {
    "package.json": json.dumps(VITE_PACKAGE),
    "package-lock.json": "{}",
    "vite.config.ts": "export default defineConfig({\n  plugins: [react()],\n})\n",
    "index.html": "<!doctype html>",
    "src/main.tsx": "render()",
}


def _load_module():
    spec = importlib.util.spec_from_file_location("coding_deploy_setup", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class FakeGitHub:
    """Answers the `gh api` calls the script makes, from an in-memory repository."""

    def __init__(self, files=None, slug=SLUG, admin=True, private=False, pages=None):
        self.slug = slug
        owner, _, name = slug.partition("/")
        self.repo = {"name": name, "default_branch": "main", "private": private,
                     "permissions": {"admin": admin, "push": True}}
        self.files = dict(VITE_APP if files is None else files)
        self.pages = pages
        self.pages_url = f"https://{owner}.github.io/{name}/"
        self.head = "head000000000000"
        self.deployments = {}          # sha -> deployment ids, newest first
        self.on_dispatch = None        # called when a workflow is started by hand
        self.before_deployments = None # called before each deployments query is answered
        self.failures = {}             # (method, path prefix) -> (status, message)
        self.calls = []
        self._commits = itertools.count(1)

    def writes(self):
        return [(method, path) for method, path, _ in self.calls if method != "GET"]

    def __call__(self, args, stdin=None):
        method, path = args[2], args[5]
        body = json.loads(stdin) if stdin else None
        self.calls.append((method, path, body))
        for (fail_method, prefix), (status, message) in self.failures.items():
            if method == fail_method and path.startswith(prefix):
                return self._error(status, message)
        route, _, query = path.partition("?")
        params = {k: v[0] for k, v in parse_qs(query).items()}
        repo = f"repos/{self.slug}"

        if route == repo and method == "GET":
            return self._json(self.repo)
        if route == f"{repo}/pages":
            if method == "GET":
                return self._json(self.pages) if self.pages else self._error(404, "Not Found")
            if method == "POST":
                self.pages = {"build_type": body["build_type"], "html_url": self.pages_url, "cname": None}
                return self._json(self.pages)
            if method == "PUT":
                self.pages["build_type"] = body["build_type"]
                return 0, "", ""
        if route == f"{repo}/contents" and method == "GET":
            if not self.files:
                return self._error(404, "This repository is empty.")
            return self._json(self._listing(""))
        if route.startswith(f"{repo}/contents/"):
            file_path = unquote(route[len(f"{repo}/contents/"):])
            if method == "GET":
                if file_path in self.files:
                    return self._json({"type": "file", "encoding": "base64", "sha": self._blob(file_path),
                                       "content": _b64(self.files[file_path])})
                listing = self._listing(file_path)
                return self._json(listing) if listing else self._error(404, "Not Found")
            if method == "PUT":
                if file_path in self.files and body.get("sha") != self._blob(file_path):
                    return self._error(409, f"{file_path} does not match")
                self.files[file_path] = base64.b64decode(body["content"]).decode("utf-8")
                self.head = f"commit{next(self._commits):010d}"
                return self._json({"commit": {"sha": self.head}})
        if route == f"{repo}/commits/main" and method == "GET":
            return self._json({"sha": self.head})
        if route == f"{repo}/deployments" and method == "GET":
            if self.before_deployments:
                self.before_deployments()
            ids = self.deployments.get(params.get("sha"), [])
            return self._json([{"id": i} for i in ids[:1]])
        if route.startswith(f"{repo}/actions/workflows/") and route.endswith("/dispatches"):
            if self.on_dispatch:
                self.on_dispatch()
            return 0, "", ""
        return self._error(404, "Not Found")

    def _blob(self, path):
        return f"blob-{abs(hash(self.files[path])) % 10 ** 8}"

    def _listing(self, folder):
        prefix = f"{folder}/" if folder else ""
        entries = {}
        for path in self.files:
            if path.startswith(prefix):
                head, _, rest = path[len(prefix):].partition("/")
                entries[head] = "dir" if rest else "file"
        return [{"name": name, "type": kind} for name, kind in sorted(entries.items())]

    @staticmethod
    def _json(data):
        return 0, json.dumps(data), ""

    @staticmethod
    def _error(status, message):
        return 1, json.dumps({"message": message, "status": str(status)}), f"gh: {message} (HTTP {status})"


class FakeWatcher:
    """Answers in turn, repeating the last answer."""

    def __init__(self, *statuses):
        self.statuses = list(statuses)
        self.targets = []

    async def advance(self, target):
        self.targets.append(target)
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]


def workflow_yaml(text):
    data = yaml.safe_load(text)
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 reads a bare `on` as true
    return data


class SetupTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.module = _load_module()
        self.github = FakeGitHub()
        self.module.GH = self.github
        self.module.POLL_SECONDS = 0
        self.watcher = FakeWatcher(DeployStatus("pending", detail="building"), DeployStatus("live", url=URL))
        self.module.make_watcher = lambda: self.watcher

    def write_manifest(self, **fields):
        path = os.path.join(self.tmp.name, "build_request.json")
        manifest = {"version": "3.0", "project_name": "cards", "queue": [{"task_id": "T1", "status": "done"}],
                    "repo": {"mode": "self", "slug": SLUG, "default_branch": "main"}}
        manifest.update(fields)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        return path

    async def run_main(self, *argv):
        """Returns (exit code, everything printed)."""
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["coding_deploy_setup.py", *argv]), \
             redirect_stdout(out), redirect_stderr(err):
            code = await self.module.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def workflow(self):
        return self.github.files.get(self.module.WORKFLOW_PATH)

    def run_steps(self):
        return [step.get("run") for step in workflow_yaml(self.workflow())["jobs"]["deploy"]["steps"]
                if "run" in step]

    def rendered(self, files=None, **plan_overrides):
        """The workflow the script would write for `files` (default the Vite app)."""
        github = FakeGitHub(files=files)
        self.module.GH = github
        try:
            plan = self.module.plan_build(self.module.RepoFiles(SLUG, "main"), "/cards/")
        finally:
            self.module.GH = self.github
        for key, value in plan_overrides.items():
            setattr(plan, key, value)
        return self.module.render_workflow(plan, "main")


class TestAFreshRepository(SetupTestCase):
    async def test_a_vite_app_is_set_up_and_its_first_deployment_followed(self):
        manifest = self.write_manifest()

        code, out = await self.run_main("--manifest", manifest)

        self.assertEqual(code, 0, out)
        self.assertEqual(self.github.pages["build_type"], "workflow")
        workflow = workflow_yaml(self.workflow())
        job = workflow["jobs"]["deploy"]
        self.assertEqual(job["environment"]["name"], "github-pages")
        self.assertEqual(workflow["on"]["push"]["branches"], ["main"])
        self.assertIs(workflow["concurrency"]["cancel-in-progress"], False)
        self.assertEqual(self.run_steps(), ["npm ci", "npm run build -- --base=/cards/"])
        upload = next(s for s in job["steps"] if "upload-pages-artifact" in s.get("uses", ""))
        self.assertEqual(upload["with"]["path"], "dist")
        self.assertEqual(self.module.workflow_state(self.workflow()), "managed")
        self.assertEqual(self.watcher.targets[-1].sha, self.github.head, "follows the workflow's own commit")
        self.assertIn(f"live at {URL}", out)
        self.assertFalse([w for w in self.github.writes() if "dispatches" in w[1]],
                         "the workflow's own push runs it")

    async def test_the_manifest_gets_its_deploy_block_and_nothing_else_changes(self):
        manifest = self.write_manifest()
        with open(manifest, encoding="utf-8") as f:
            before = json.load(f)

        await self.run_main("--manifest", manifest)

        with open(manifest, encoding="utf-8") as f:
            after = json.load(f)
        self.assertEqual(after.pop("deploy"), {"backend": "github_pages"})
        self.assertEqual(after, before, "an old `self` repo mode must not be migrated by this write")

    async def test_running_it_again_changes_nothing(self):
        manifest = self.write_manifest()
        await self.run_main("--manifest", manifest)
        self.github.calls.clear()

        code, out = await self.run_main("--manifest", manifest)

        self.assertEqual(code, 0, out)
        self.assertEqual(self.github.writes(), [])
        self.assertIn("is up to date", out)
        self.assertIn("on, source GitHub Actions", out)

    async def test_a_dry_run_only_looks(self):
        manifest = self.write_manifest()

        code, out = await self.run_main("--manifest", manifest, "--dry-run")

        self.assertEqual(code, 0, out)
        self.assertEqual(self.github.writes(), [])
        self.assertIsNone(self.workflow())
        self.assertIn("would switch GitHub Pages on", out)
        self.assertIn("│ name: Deploy to GitHub Pages", out)
        self.assertNotIn("would start", out, "the commit it would make runs the workflow by itself")
        with open(manifest, encoding="utf-8") as f:
            self.assertNotIn("deploy", json.load(f))

    async def test_a_repo_without_a_manifest_needs_only_its_slug(self):
        code, out = await self.run_main("--repo", SLUG, "--no-wait")

        self.assertEqual(code, 0, out)
        self.assertIsNotNone(self.workflow())
        self.assertNotIn("Manifest", out)
        self.assertEqual(self.watcher.targets, [], "--no-wait doesn't wait")


class TestWhatIsThereAlready(SetupTestCase):
    async def test_pages_built_from_a_branch_is_switched_to_actions(self):
        self.github.pages = {"build_type": "legacy", "source": {"branch": "gh-pages"}, "html_url": URL}

        code, out = await self.run_main("--repo", SLUG)

        self.assertEqual(code, 0, out)
        self.assertEqual(self.github.pages["build_type"], "workflow")
        self.assertIn("from `gh-pages` to GitHub Actions", out)

    async def test_switching_pages_on_under_its_workflow_starts_it_and_ignores_the_old_failure(self):
        self.github.files[self.module.WORKFLOW_PATH] = self.rendered()
        deployments = self.github.deployments[self.github.head] = [5]  # failed: Pages was off
        polls_since_start = []
        self.github.on_dispatch = lambda: polls_since_start.append(0)

        def new_run_shows_up_on_the_third_poll():
            if polls_since_start:
                polls_since_start.append(0)
                if len(polls_since_start) == 4:
                    deployments.insert(0, 6)

        self.github.before_deployments = new_run_shows_up_on_the_third_poll

        class OnlyTheNewRun(FakeWatcher):
            async def advance(watcher, target):
                watcher.targets.append(target)
                if deployments[0] != 6:
                    return DeployStatus("failed", detail="the old run, from before Pages was on")
                return DeployStatus("live", url=URL)

        self.watcher = OnlyTheNewRun()
        self.module.make_watcher = lambda: self.watcher

        code, out = await self.run_main("--repo", SLUG)

        self.assertEqual(code, 0, out)
        self.assertIn(("POST", f"repos/{SLUG}/actions/workflows/pages.yml/dispatches"), self.github.writes())
        self.assertIn("waiting for the new run to start", out)
        self.assertEqual(len(self.watcher.targets), 1, "asked only once the new run's deployment existed")
        self.assertIn("live at", out)

    async def test_a_workflow_edited_by_hand_is_left_alone_unless_forced(self):
        edited = self.rendered().replace("runs-on: ubuntu-latest", "runs-on: macos-latest")
        self.github.files[self.module.WORKFLOW_PATH] = edited
        self.github.pages = {"build_type": "workflow", "html_url": URL}

        code, out = await self.run_main("--repo", SLUG, "--no-wait")

        self.assertEqual(code, 0, out)
        self.assertEqual(self.workflow(), edited)
        self.assertIn("edited by hand", out)

        code, out = await self.run_main("--repo", SLUG, "--no-wait", "--force")

        self.assertEqual(code, 0, out)
        self.assertEqual(self.workflow(), self.rendered())

    async def test_a_workflow_it_wrote_and_nobody_touched_is_refreshed(self):
        old = self.rendered()
        self.module.ACTIONS = {**self.module.ACTIONS, "deploy-pages": "actions/deploy-pages@v6"}
        self.github.files[self.module.WORKFLOW_PATH] = old
        self.github.pages = {"build_type": "workflow", "html_url": URL}

        code, out = await self.run_main("--repo", SLUG, "--no-wait")

        self.assertEqual(code, 0, out)
        self.assertIn("actions/deploy-pages@v6", self.workflow())
        self.assertIn("updated .github/workflows/pages.yml", out)

    async def test_another_workflow_that_deploys_pages_is_respected(self):
        self.github.files[".github/workflows/deploy.yml"] = "jobs: {d: {steps: [{uses: actions/deploy-pages@v5}]}}"
        self.github.pages = {"build_type": "workflow", "html_url": URL}

        code, out = await self.run_main("--repo", SLUG, "--no-wait")

        self.assertEqual(code, 0, out)
        self.assertIsNone(self.workflow())
        self.assertIn("deploy.yml already deploys to Pages", out)


class TestHowItBuilds(SetupTestCase):
    async def test_a_vite_config_that_sets_its_own_base_is_built_as_it_is(self):
        self.github.files["vite.config.ts"] = "export default defineConfig({ base: '/cards/' })\n"

        code, out = await self.run_main("--repo", SLUG, "--no-wait")

        self.assertEqual(code, 0, out)
        self.assertEqual(self.run_steps(), ["npm ci", "npm run build"])
        self.assertIn("sets `base` itself", out)

    async def test_a_build_script_that_doesnt_end_in_vite_build_gets_a_note(self):
        package = {**VITE_PACKAGE, "scripts": {"build": "vite build && node scripts/sitemap.js"}}
        self.github.files["package.json"] = json.dumps(package)

        code, out = await self.run_main("--repo", SLUG, "--no-wait")

        self.assertEqual(code, 0, out)
        self.assertEqual(self.run_steps(), ["npm ci", "npm run build"])
        self.assertIn("set `base: '/cards/'` in vite.config", out)

    async def test_without_a_lockfile_it_installs_unpinned_and_says_so(self):
        del self.github.files["package-lock.json"]

        code, out = await self.run_main("--repo", SLUG, "--no-wait")

        self.assertEqual(code, 0, out)
        self.assertEqual(self.run_steps()[0], "npm install")
        setup_node = next(s for s in workflow_yaml(self.workflow())["jobs"]["deploy"]["steps"]
                          if "setup-node" in s.get("uses", ""))
        self.assertNotIn("cache", setup_node["with"], "setup-node's npm cache needs a lockfile")
        self.assertIn("no package-lock.json", out)

    async def test_a_node_version_file_is_used(self):
        self.github.files[".nvmrc"] = "22\n"

        await self.run_main("--repo", SLUG, "--no-wait")

        self.assertIn("node-version-file: .nvmrc", self.workflow())

    async def test_a_static_site_is_published_as_it_is(self):
        self.github.files = {"index.html": "<!doctype html>", "style.css": "body {}"}

        code, out = await self.run_main("--repo", SLUG, "--no-wait")

        self.assertEqual(code, 0, out)
        steps = workflow_yaml(self.workflow())["jobs"]["deploy"]["steps"]
        self.assertFalse([s for s in steps if "setup-node" in s.get("uses", "") or "run" in s])
        self.assertEqual(next(s for s in steps if "upload-pages-artifact" in s.get("uses", ""))["with"]["path"], ".")

    async def test_overrides_are_used_and_quoted_safely(self):
        code, out = await self.run_main("--repo", SLUG, "--no-wait", "--build", "make site # docs: yes",
                                        "--dist", "public")

        self.assertEqual(code, 0, out)
        self.assertEqual(self.run_steps(), ["npm ci", "make site # docs: yes"])
        steps = workflow_yaml(self.workflow())["jobs"]["deploy"]["steps"]
        self.assertEqual(next(s for s in steps if "upload-pages-artifact" in s.get("uses", ""))["with"]["path"],
                         "public")

    def test_where_the_site_is_served(self):
        self.assertEqual(self.module.site_base("example/cards", None), "/cards/")
        self.assertEqual(self.module.site_base("Example/example.github.io", None), "/")
        self.assertEqual(self.module.site_base("example/cards", {"cname": "cards.example.com"}), "/")
        self.assertEqual(self.module.site_base("example/cards", None, "app"), "/app/")
        self.assertEqual(self.module.site_base("example/cards", None, "/"), "/")


class TestRefusals(SetupTestCase):
    async def test_without_admin_nothing_is_touched(self):
        self.github.repo["permissions"]["admin"] = False

        code, out = await self.run_main("--repo", SLUG)

        self.assertEqual(code, 1)
        self.assertEqual(self.github.writes(), [])
        self.assertIn("without admin", out)

    async def test_an_empty_repository_is_too_early(self):
        self.github.files = {}

        code, out = await self.run_main("--repo", SLUG)

        self.assertEqual(code, 1)
        self.assertEqual(self.github.writes(), [])
        self.assertIn("has no files yet", out)

    async def test_a_pnpm_project_is_not_guessed_at(self):
        del self.github.files["package-lock.json"]
        self.github.files["pnpm-lock.yaml"] = "lockfileVersion: 9"

        code, out = await self.run_main("--repo", SLUG)

        self.assertEqual(code, 1)
        self.assertEqual(self.github.writes(), [])
        self.assertIn("only npm projects", out)

    async def test_a_missing_workflow_scope_says_how_to_get_it(self):
        self.github.failures[("PUT", f"repos/{SLUG}/contents/")] = (404, "Not Found")

        code, out = await self.run_main("--repo", SLUG)

        self.assertEqual(code, 1)
        self.assertIn("gh auth refresh -h github.com -s workflow", out)

    async def test_backends_it_cannot_set_up_are_named_before_github_is_asked(self):
        for deploy, expected in (({"backend": "github_deployment", "environment": "production"}, "isn't automated"),
                                 ({"backend": "none"}, "asked not to be deployed")):
            with self.subTest(deploy=deploy):
                code, out = await self.run_main("--manifest", self.write_manifest(deploy=deploy))
                self.assertEqual(code, 1)
                self.assertIn(expected, out)
        self.assertEqual(self.github.calls, [])

    async def test_usage_errors(self):
        for argv in ((), ("--repo", "not-a-slug"), ("--repo", SLUG, "--timeout", "0"),
                     ("--manifest", os.path.join(self.tmp.name, "missing.json"))):
            with self.subTest(argv=argv):
                code, _ = await self.run_main(*argv)
                self.assertEqual(code, 2)
        self.assertEqual(self.github.calls, [])


class TestTheFirstDeployment(SetupTestCase):
    async def test_a_failed_first_deployment_fails_the_run_with_its_log(self):
        self.watcher = FakeWatcher(DeployStatus("failed", detail="tsc: 3 errors",
                                                log_url="https://github.com/example/cards/actions/runs/7"))
        self.module.make_watcher = lambda: self.watcher

        code, out = await self.run_main("--repo", SLUG)

        self.assertEqual(code, 1)
        self.assertIn("tsc: 3 errors", out)
        self.assertIn("actions/runs/7", out)

    async def test_a_deployment_that_never_goes_live_runs_out_of_time(self):
        self.watcher = FakeWatcher(DeployStatus("pending", detail="queued"))
        self.module.make_watcher = lambda: self.watcher
        ticks = itertools.count(0, 120)
        self.module.clock = lambda: next(ticks)

        code, out = await self.run_main("--repo", SLUG, "--timeout", "300")

        self.assertEqual(code, 1)
        self.assertIn("isn't live after 5 min", out)
        self.assertIn("· queued", out)


class TestTheWorkflowFile(SetupTestCase):
    def test_it_knows_its_own_file_and_notices_an_edit(self):
        text = self.rendered()

        self.assertEqual(self.module.workflow_state(text), "managed")
        self.assertEqual(self.module.workflow_state(text + "# a note\n"), "edited")
        self.assertEqual(self.module.workflow_state("name: Deploy\n"), "foreign")

    def test_awkward_values_survive_yaml(self):
        for value in ("npm run build -- --base=/cards/", "make site # docs: yes", "echo ${{ secrets.X }}",
                      "- dash first", "dist "):
            with self.subTest(value=value):
                self.assertEqual(yaml.safe_load(f"key: {self.module._scalar(value)}")["key"], value)


if __name__ == "__main__":
    unittest.main()
