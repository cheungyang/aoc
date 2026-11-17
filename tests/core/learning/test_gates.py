"""The static checks on a proposal (core/learning/gates.py), each on a small repo of its own."""
import json
import os
import shutil
import tempfile
import unittest

from core.learning import gates
from core.learning.gates import Draft

AGENTS_MD = ("# AGENTS.md\n\n## Operating Instructions\n"
             "1. **Mornings**: follow the `morning_digest` skill. List every item due today.\n"
             "2. Get the user's approval before writing anything.\n")
SKILL_MD = ("---\nname: demo_skill\ndescription: A demo.\n---\n## Core\n\n1. Read the list.\n2. Summarize it in 5 lines.\n"
            "\n## Reference\n\nMore detail here.\n")
TEMPLATE = "- Answer in the user's language.\n{{#workflow}}\n- See agents/{{agent_id}}/WORKFLOW.md.\n{{/workflow}}\n"
RULES_MD = "- top_items: 3  # how many items the digest leads with\n"
FEEDBACK_MD = "# Feedback\n\n- Keep summaries brief · src:demo · 2026-10-01\n"
CASES = "no, only the first three things on Friday at 18:30 please, Marguerite said so"


def edit(find, replace):
    return [{"find": find, "replace": replace}]


class _Repo(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.files = {
            "agents/demo/AGENTS.md": AGENTS_MD,
            "agents/demo/agent.json": json.dumps({"agent_id": "demo", "name": "Demo", "skills": {"demo_skill": {}}}),
            "skills/demo_skill/SKILL.md": SKILL_MD,
            "core/prompts/templates/system/rules.md": TEMPLATE,
            "core/prompts/templates/voice/near_miss.md": "Say what you heard.\n",
            "tools/web_search.py": "",
            "pkm/agents/demo/RULES.md": RULES_MD,
            "pkm/agents/demo/FEEDBACK.md": FEEDBACK_MD,
        }
        for path, text in self.files.items():
            full = os.path.join(self.root, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(text)

    def draft(self, path, edits, **kwargs):
        return Draft(path=path, before=self.files[path], edits=edits, root=self.root, **kwargs)

    def run_checks(self, draft, **overrides):
        overrides.setdefault("notes_check", lambda path, text: [])
        overrides.setdefault("lint", lambda agent, root: [])
        overrides.setdefault("agent_names", {"Demo"})
        return {c.gate: c for c in gates.run(draft, **overrides)}

    def failed(self, checks):
        return sorted(gate for gate, c in checks.items() if not c.ok)


class TestAGoodProposal(_Repo):

    def test_a_small_rewording_passes_every_check(self):
        draft = self.draft("agents/demo/AGENTS.md", edit("List every item due today.",
                                                         "Lead with the 3 highest-impact items."))
        checks = self.run_checks(draft, tools={"web_search"})
        self.assertEqual(list(checks), list(gates.ORDER))
        self.assertEqual(self.failed(checks), [], {g: c.detail for g, c in checks.items()})
        self.assertEqual(draft.after, AGENTS_MD.replace("List every item due today.", "Lead with the 3 highest-impact items."))


class TestWhereAndBase(_Repo):

    def test_a_file_it_may_not_change_stops_at_the_first_check(self):
        checks = gates.run(Draft(path="agents/demo/agent.json", before="{}", edits=edit("{", "[")))
        self.assertEqual([(c.gate, c.ok) for c in checks], [("where", False)])

    def test_an_edit_that_doesnt_apply_stops_at_base(self):
        for find, count in (("Not in the file", 0), ("the", 2)):
            with self.subTest(find):
                checks = self.run_checks(self.draft("agents/demo/AGENTS.md", edit(find, "x")))
                self.assertEqual(list(checks), ["where", "base"])
                self.assertIn(f"occurs {count} times, not once", checks["base"].detail)

    def test_edits_that_change_nothing_fail(self):
        checks = self.run_checks(self.draft("agents/demo/AGENTS.md", edit("Mornings", "Mornings")))
        self.assertIn("change nothing", checks["base"].detail)


class TestSize(_Repo):

    def test_a_big_cut_is_staged_as_several(self):
        checks = self.run_checks(self.draft("skills/demo_skill/SKILL.md", edit("1. Read the list.\n2. Summarize it in 5 lines.\n", "")))
        self.assertFalse(checks["size"].ok)
        self.assertIn("stage the cut", checks["size"].detail)

    def test_what_a_cut_drops_survives_or_is_named(self):
        cut = edit("2. Summarize it in 5 lines.\n", "")
        draft = self.draft("skills/demo_skill/SKILL.md", cut)
        gates.check_base(draft)
        self.assertEqual(gates.check_size(draft).ok, True, "one digit isn't a number worth keeping")
        draft = self.draft("agents/demo/AGENTS.md", edit("follow the `morning_digest` skill. ", ""))
        gates.check_base(draft)
        check = gates.check_size(draft)
        self.assertFalse(check.ok)
        self.assertIn("`morning_digest`", check.detail)
        draft = self.draft("agents/demo/AGENTS.md", edit("follow the `morning_digest` skill. ", ""), dropped=["morning_digest"])
        gates.check_base(draft)
        self.assertTrue(gates.check_size(draft).ok)

    def test_more_than_thirty_lines_is_too_big(self):
        long = "".join(f"line {i}\n" for i in range(40))
        draft = Draft(path="skills/demo_skill/SKILL.md", before=SKILL_MD, edits=edit("More detail here.\n", long))
        gates.check_base(draft)
        self.assertIn("more than 30", gates.check_size(draft).detail)


class TestBudget(_Repo):

    def budget(self, path, find, replace):
        draft = self.draft(path, edit(find, replace))
        gates.check_base(draft)
        return gates.check_budget(draft)

    def test_a_persona_file_stays_under_its_ceiling_and_grows_only_under_target(self):
        self.assertIn("ceiling", self.budget("agents/demo/AGENTS.md", "Mornings", "x" * 2600).detail)
        padded = AGENTS_MD + "y" * 1500
        self.files["agents/demo/AGENTS.md"] = padded
        check = self.budget("agents/demo/AGENTS.md", "Mornings", "Mornings, first thing")
        self.assertFalse(check.ok)
        self.assertIn("target", check.detail)
        self.assertTrue(self.budget("agents/demo/AGENTS.md", "Mornings", "Morning").ok, "shrinking is always fine")

    def test_the_templates_every_agent_loads_never_grow(self):
        check = self.budget("core/prompts/templates/system/rules.md", "language.", "language, always.")
        self.assertIn("every agent's prompt", check.detail)
        self.assertTrue(self.budget("core/prompts/templates/voice/near_miss.md", "heard.", "heard, briefly.").ok)

    def test_text_without_a_budget_grows_by_a_little(self):
        self.assertFalse(self.budget("skills/demo_skill/SKILL.md", "More detail here.", "z" * 400).ok)
        self.assertTrue(self.budget("skills/demo_skill/SKILL.md", "More detail here.", "More detail, here.").ok)

    def test_feedback_has_its_memory_budget(self):
        self.assertFalse(self.budget("pkm/agents/demo/FEEDBACK.md", "Keep summaries brief", "w" * 2100).ok)


class TestProtected(_Repo):

    def protected(self, path, find, replace):
        draft = self.draft(path, edit(find, replace))
        gates.check_base(draft)
        return gates.check_protected(draft)

    def test_a_line_about_approval_cant_be_removed_or_reworded(self):
        check = self.protected("agents/demo/AGENTS.md", "2. Get the user's approval before writing anything.\n", "")
        self.assertFalse(check.ok)
        self.assertIn("approval before writes", check.detail)
        self.assertFalse(self.protected("agents/demo/AGENTS.md", "before writing anything", "before big writes").ok)

    def test_a_new_line_cant_skip_one(self):
        check = self.protected("skills/demo_skill/SKILL.md", "1. Read the list.", "1. Read the list and save it without asking.")
        self.assertFalse(check.ok)
        self.assertIn("skips approval", check.detail)

    def test_other_lines_are_free_to_change(self):
        self.assertTrue(self.protected("skills/demo_skill/SKILL.md", "Summarize it in 5 lines.", "Summarize it in 3 lines.").ok)


class TestCapability(_Repo):

    def capability(self, replace, find="More detail here."):
        draft = self.draft("skills/demo_skill/SKILL.md", edit(find, replace))
        gates.check_base(draft)
        return gates.check_capability(draft, tools={"web_search", "agent_call"})

    def test_new_wiring_is_refused(self):
        for text, kind in (("See https://example.com/x.", "URL"), ("Mail ops@example.com.", "email"),
                           ("Post in #general.", "channel"), ("Read pkm/wallet/finance/.", "path"),
                           ("Call `agent_call` first.", "tool"), ("Grant it @manage.", "mention or grant"),
                           ("Call +1 415 555 0100.", "phone number")):
            with self.subTest(kind):
                check = self.capability(text)
                self.assertFalse(check.ok)
                self.assertIn(f"a new {kind}", check.detail)

    def test_wiring_already_there_or_plain_words_pass(self):
        self.assertTrue(self.capability("More detail here, in 2026-11-17 terms.").ok, "a date isn't a phone number")
        self.assertTrue(self.capability("Searching the web is fine.").ok)


class TestPersonal(_Repo):

    def personal(self, path, find, replace, notes=lambda p, t: [], cases=CASES):
        draft = self.draft(path, edit(find, replace), cases=cases)
        gates.check_base(draft)
        return gates.check_personal(draft, notes, agent_names={"Demo"})

    def test_words_numbers_and_names_from_the_cases_stay_out_of_the_repo(self):
        for replace, what in (("Only the first three things on Friday.", "run(s) of 5 words"),
                              ("Plan around 18:30.", "number(s)"),
                              ("Ask Marguerite first.", "name(s)")):
            with self.subTest(what):
                check = self.personal("skills/demo_skill/SKILL.md", "More detail here.", replace)
                self.assertFalse(check.ok)
                self.assertIn(what, check.detail)
                self.assertIn("goes to pkm/", check.detail)

    def test_the_vault_may_hold_them(self):
        self.assertTrue(self.personal("pkm/agents/demo/FEEDBACK.md", "Keep summaries brief", "Ask Marguerite first").ok)

    def test_the_privacy_guard_runs_on_what_is_added(self):
        seen = []
        check = self.personal("skills/demo_skill/SKILL.md", "More detail here.", "Lead with three.",
                              notes=lambda p, t: seen.append((p, t)) or ["an email address"])
        self.assertEqual(seen, [("skills/demo_skill/SKILL.md", "Lead with three.")])
        self.assertIn("an email address", check.detail)

    def test_a_guard_that_cant_run_fails_the_check(self):
        def broken(path, text):
            raise RuntimeError("no notes")
        check = self.personal("skills/demo_skill/SKILL.md", "More detail here.", "Lead with three.", notes=broken)
        self.assertFalse(check.ok)
        self.assertIn("couldn't run", check.detail)

    def test_without_notes_it_refuses_rather_than_pass(self):
        draft = self.draft("skills/demo_skill/SKILL.md", edit("More detail here.", "Lead with three."))
        draft.pkm = os.path.join(self.root, "no-notes-here")
        gates.check_base(draft)
        self.assertFalse(gates.check_personal(draft, agent_names={"Demo"}).ok)


class TestRender(_Repo):

    def render(self, path, find, replace):
        draft = self.draft(path, edit(find, replace))
        gates.check_base(draft)
        return gates.check_render(draft)

    def test_a_template_keeps_its_tags(self):
        self.assertFalse(self.render("core/prompts/templates/system/rules.md", "{{/workflow}}\n", "").ok)
        self.assertTrue(self.render("core/prompts/templates/system/rules.md", "user's language", "user's own language").ok)

    def test_a_skill_keeps_its_frontmatter_and_sections(self):
        self.assertIn("frontmatter", self.render("skills/demo_skill/SKILL.md", "description: A demo.\n", "").detail)
        self.assertIn("sections", self.render("skills/demo_skill/SKILL.md", "## Reference", "## More").detail)

    def test_rules_keep_their_keys_and_feedback_its_entry_lines(self):
        self.assertTrue(self.render("pkm/agents/demo/RULES.md", "top_items: 3", "top_items: 5").ok)
        self.assertFalse(self.render("pkm/agents/demo/RULES.md", "top_items: 3", "lead_items: 3").ok)
        self.assertFalse(self.render("pkm/agents/demo/FEEDBACK.md", " · src:demo · 2026-10-01", "").ok)


class TestLint(_Repo):

    def test_the_files_owners(self):
        self.assertEqual(gates.owners("agents/demo/AGENTS.md", self.root), ["demo"])
        self.assertEqual(gates.owners("skills/demo_skill/SKILL.md", self.root), ["demo"])
        self.assertEqual(gates.owners("core/prompts/templates/system/rules.md", self.root), [])

    def test_only_new_findings_fail_and_the_copy_holds_the_change(self):
        seen = {}

        def lint(agent, root):
            with open(os.path.join(root, "agents", "demo", "AGENTS.md"), encoding="utf-8") as f:
                seen[root] = f.read()
            found = [("over_budget", "persona is long")]
            return found + ([("phantom_skill", "names `nowhere`")] if "nowhere" in seen[root] else [])

        draft = self.draft("agents/demo/AGENTS.md", edit("`morning_digest`", "`nowhere`"))
        gates.check_base(draft)
        check = gates.check_lint(draft, lint)
        self.assertFalse(check.ok)
        self.assertEqual(check.detail, "demo: phantom_skill: names `nowhere`")
        [copy] = [root for root in seen if root != self.root]
        self.assertFalse(os.path.exists(copy), "the copy is removed")
        self.assertEqual(seen[self.root], AGENTS_MD, "the real file is untouched")

    def test_the_overlay_is_symlinks_but_for_the_changed_file(self):
        copy = gates.overlay(self.root, "agents/demo/AGENTS.md", "changed")
        self.addCleanup(shutil.rmtree, copy, True)
        self.assertTrue(os.path.islink(os.path.join(copy, "skills")))
        self.assertTrue(os.path.islink(os.path.join(copy, "agents", "demo", "agent.json")))
        self.assertFalse(os.path.islink(os.path.join(copy, "agents", "demo", "AGENTS.md")))
        with open(os.path.join(copy, "agents", "demo", "AGENTS.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "changed")
        with open(os.path.join(self.root, "agents", "demo", "AGENTS.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), AGENTS_MD)


if __name__ == "__main__":
    unittest.main()
