"""Fail if the skill folders, .claude-plugin/plugin.json and the README skill table disagree."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

on_disk = {p.parent.name for p in ROOT.glob("lightning-*/SKILL.md")}
manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
in_plugin = {Path(s).name for s in manifest.get("skills", [])}
readme = (ROOT / "README.md").read_text(encoding="utf-8")
in_readme = set(re.findall(r"^\| \[`(lightning-[a-z0-9-]+)`\]", readme, re.MULTILINE))

errors = []
for label, names in (("plugin.json skills", in_plugin), ("README skill table", in_readme)):
    if missing := sorted(on_disk - names):
        errors.append(f"{label} is missing: {', '.join(missing)}")
    if extra := sorted(names - on_disk):
        errors.append(f"{label} lists skills with no folder: {', '.join(extra)}")

for name in sorted(on_disk):
    head = (ROOT / name / "SKILL.md").read_text(encoding="utf-8").split("\n---\n", 1)[0]
    for field in ("license", "compatibility"):
        if not re.search(rf"^{field}:", head, re.MULTILINE):
            errors.append(f"{name}/SKILL.md frontmatter has no `{field}`")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print(f"OK: {len(on_disk)} skills agree across folders, plugin.json and README")
