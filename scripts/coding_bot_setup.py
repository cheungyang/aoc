#!/usr/bin/env python3
"""
Verifies (and finishes) the machine-user setup the coding graph pushes with.

The account, the collaborator invite and the token are created by hand on
GitHub — that part cannot be scripted. This checks that what exists on GitHub
matches what the manifest declares, and creates the `approved` label used as an
approval signal.

Usage:
    python3 scripts/coding_bot_setup.py [--manifest pkm/wiki/software/build_request.json]
                                        [--repo owner/name] [--login bot-account]
                                        [--no-label]

Exit code 0 means the graph can push and open PRs as the machine user.
"""

import sys
import os
import argparse
import asyncio

# Add project root to sys.path
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from core.util import git_ops
from core.util.push_identity import PushIdentityError, describe, resolve_token_path
from graphs.coding.utils.dag import load_manifest, resolve_manifest_path
from graphs.coding.utils.repo import get_repo_descriptor, resolve_push_identity


def parse_args():
    parser = argparse.ArgumentParser(description="Verify the coding graph's machine-user setup.")
    parser.add_argument("--manifest", type=str, default=None, help="Path to build_request.json")
    parser.add_argument("--repo", type=str, default=None, help="Override the target repo slug (owner/name)")
    parser.add_argument("--login", type=str, default=None, help="Override the machine-user login")
    parser.add_argument("--no-label", action="store_true", help="Skip creating the `approved` label")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()

    manifest_path = resolve_manifest_path(args.manifest)
    manifest = load_manifest(manifest_path)
    descriptor = get_repo_descriptor(manifest)
    if args.login:
        descriptor["push_identity"] = args.login
    if args.repo:
        descriptor["slug"] = args.repo

    print(f"Manifest:  {manifest_path}")
    print(f"Repo:      {descriptor.get('slug') or '(discover from git remote)'}")

    login = descriptor.get("push_identity")
    if not login:
        print(
            "\nNo machine user configured. Add one to the manifest:\n"
            '  "repo": { "slug": "owner/name", "push_identity": "your-bot-account" }\n'
            f"and put its fine-grained PAT in {resolve_token_path()} (chmod 600).\n"
            "Without it the graph pushes as you, and GitHub will not let you approve your own PR."
        )
        return 1

    identity, error = resolve_push_identity(descriptor)
    if error:
        print(f"\n✗ {error}")
        return 1

    print(f"Identity:  {describe(identity)}")

    ok, message = await git_ops.preflight_push_access(
        target_repo=descriptor.get("slug"),
        cwd=project_root,
        push_identity=identity
    )
    print(("\n✓ " if ok else "\n✗ ") + message)
    if not ok:
        return 1

    if not args.no_label:
        slug = descriptor.get("slug") or await git_ops.discover_target_repo(project_root, ".")
        label_ok, label_msg = await git_ops.ensure_label(
            target_repo=slug, name="approved", cwd=project_root, push_identity=identity
        )
        print(("✓ " if label_ok else "✗ ") + label_msg)
        if not label_ok:
            return 1

    print("\nMachine user is ready. PRs will be authored by "
          f"`{identity.login}`, so you can approve them with GitHub's own review flow.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except PushIdentityError as e:
        print(f"✗ {e}")
        sys.exit(1)
