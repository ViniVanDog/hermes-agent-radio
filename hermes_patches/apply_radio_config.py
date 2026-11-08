#!/usr/bin/env python3
"""Trim what every Hermes LLM call carries on the radio VM. Idempotent.

Edits ~/.hermes/config.yaml (comments kept, via ruamel.yaml from Hermes' venv):
  * auxiliary.background_review.enabled: false
      the post-turn self-review fork: 113 of the first 590 calls, 28% of the input;
  * auxiliary.title_generation.model_upgrade_enabled: false
      keeps instant local titles without an auxiliary model call;
  * platform_toolsets.cli: [skills]
      drops the file, terminal and vision schemas (~3k tokens per call) and with them
      the "approval" calls; MCP server tools are unaffected;
  * skills.disabled: every installed skill except the ones passed with --keep
      the <available_skills> list was ~6k characters of unused skills per call.
      The global list is used because the CLI does not always set HERMES_PLATFORM.

A backup goes to config.yaml.bak-radio-config-<time> before any change; if the
result does not parse, the backup is restored.

Run with Hermes' Python (it has ruamel.yaml):
    <hermes-venv>/bin/python apply_radio_config.py [--hermes-home ~/.hermes] [--dry-run]
Then: systemctl --user restart hermes-radio
"""

import argparse
import io
import re
import shutil
import sys
import time
from pathlib import Path

KEEP_SKILLS = ()  # skills you still want on the radio: --keep name [--keep name ...]
ESSENTIAL_SKILLS = {"hermes-agent"}   # Hermes refuses to disable it anyway
RADIO_TOOLSETS = ["skills"]


def skill_names(skills_dir):
    """Frontmatter `name:` of every SKILL.md (falls back to the folder name)."""
    names = set()
    for skill in Path(skills_dir).rglob("SKILL.md"):
        match = re.search(r"^name:\s*['\"]?([^'\"\n]+)", skill.read_text(encoding="utf-8", errors="replace"),
                          re.MULTILINE)
        names.add(match.group(1).strip() if match else skill.parent.name)
    return names


def _child(mapping, key):
    from ruamel.yaml.comments import CommentedMap
    if not isinstance(mapping.get(key), dict):
        mapping[key] = CommentedMap()
    return mapping[key]


def _flow(items):
    from ruamel.yaml.comments import CommentedSeq
    seq = CommentedSeq(items)
    seq.fa.set_flow_style()
    return seq


def apply(config, installed_skills, keep=KEEP_SKILLS):
    """Mutates the loaded config; returns the list of changes made."""
    changes = []
    review = _child(_child(config, "auxiliary"), "background_review")
    if review.get("enabled") is not False:
        review["enabled"] = False
        changes.append("auxiliary.background_review.enabled: false")

    titles = _child(_child(config, "auxiliary"), "title_generation")
    if titles.get("model_upgrade_enabled") is not False:
        titles["model_upgrade_enabled"] = False
        changes.append("auxiliary.title_generation.model_upgrade_enabled: false (local titles kept)")

    toolsets = _child(config, "platform_toolsets")
    if list(toolsets.get("cli") or []) != RADIO_TOOLSETS:
        changes.append(f"platform_toolsets.cli: {list(toolsets.get('cli') or [])} -> {RADIO_TOOLSETS}")
        toolsets["cli"] = _flow(RADIO_TOOLSETS)

    skills = _child(config, "skills")
    current = [str(name) for name in (skills.get("disabled") or [])]
    wanted = sorted((set(current) | set(installed_skills)) - set(keep) - ESSENTIAL_SKILLS)
    if sorted(current) != wanted:
        changes.append(f"skills.disabled: {len(current)} -> {len(wanted)} skills (kept: {', '.join(keep) or 'none'})")
        skills["disabled"] = _flow(wanted)
    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hermes-home", type=Path, default=Path("~/.hermes").expanduser())
    parser.add_argument("--keep", nargs="*", default=list(KEEP_SKILLS), help="skills that stay enabled")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        from ruamel.yaml import YAML
    except ImportError:
        sys.exit("ruamel.yaml missing: run this with Hermes' venv Python")

    path = args.hermes_home / "config.yaml"
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)   # Hermes' own style: "  key:\n    - item"
    config = yaml.load(path.read_text(encoding="utf-8"))
    changes = apply(config, skill_names(args.hermes_home / "skills"), args.keep)
    if not changes:
        print(f"already applied: {path}")
        return
    for change in changes:
        print(("would change: " if args.dry_run else "change: ") + change)
    if args.dry_run:
        return

    out = io.StringIO()
    yaml.dump(config, out)
    backup = path.with_name(f"config.yaml.bak-radio-config-{int(time.time())}")
    shutil.copy2(path, backup)
    path.write_text(out.getvalue(), encoding="utf-8")
    try:
        YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001 - any parse failure means roll back
        shutil.copy2(backup, path)
        sys.exit(f"config did not parse after the edit, restored {backup.name}: {error}")
    print(f"written: {path} (backup {backup.name})")
    print("Restart Hermes:  systemctl --user restart hermes-radio")


if __name__ == "__main__":
    main()
