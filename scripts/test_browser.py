#!/usr/bin/env python
"""Check that the NAS can reach the desktop Agent Chrome, then optionally run a task.

    docker exec -it aoc python scripts/test_browser.py           # connectivity only
    docker exec -it aoc python scripts/test_browser.py --run     # + a harmless task

Connectivity reads BROWSER_CDP_URL_FILE and BROWSER_PERSONAL_INFO_FILE exactly as
tools/browser.py does, so a pass here means the tool will connect.
"""
import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import browser as browser_mod  # noqa: E402

GOAL = (
    "Open https://example.com and report the page heading. "
    "Do not click anything else."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", action="store_true", help="Also run a harmless browsing task (costs model calls).")
    args = parser.parse_args()

    try:
        url = browser_mod._endpoint()
        fields, domains = browser_mod._personal_info()
    except browser_mod.BrowserConfigError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK: Agent Chrome reachable at {url}")
    print(f"Personal fields: {', '.join(sorted(fields)) or '(none configured)'}")
    print(f"Allowed domains: {', '.join(domains) if domains else '(unrestricted)'}")

    if args.run:
        if domains and not any("example.com" in d for d in domains):
            print("Skipping --run: allowed_domains does not include example.com.")
            return 0
        print(f"\nRunning: {GOAL}\n(watch the Agent Chrome window)\n")
        print(browser_mod.browser.invoke({"goal": GOAL, "max_steps": 5}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
