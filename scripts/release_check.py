#!/usr/bin/env python3
"""release_check.py -- the pre-release gate. Run it before tagging a release.

    python3 scripts/release_check.py              # check the working tree
    python3 scripts/release_check.py --tag v1.2.0 # also check the tag you are about to create

Checks, all against git-tracked files:

1. Versions agree: src/lightroom_bridge/__init__.py, both versions in server.json,
   the newest released CHANGELOG.md entry, and --tag if given.
2. Every Python file compiles, and the CLI starts (`lrc --help`, no Lightroom needed).
3. The knowledge base ships EMPTY: kb/ holds only README.md, INDEX.md,
   learned/TEMPLATE.md and .gitkeep files, and `kb.py validate` passes.
4. No personal data: no absolute home-directory paths in tracked files.
5. No em-dashes or en-dashes in tracked text (house writing rule).
6. Every relative Markdown link resolves.
7. The working tree is clean (everything that ships is committed).

Stdlib only. Exit code 0 = ready to tag, 1 = fix the FAIL lines first.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".py", ".md", ".lua", ".toml", ".json", ".sh", ".txt", ".yml", ".yaml"}
KB_ALLOWED = {"kb/README.md", "kb/INDEX.md", "kb/learned/TEMPLATE.md"}
EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)  # spelled as codes so this file passes its own check

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS  " if ok else "FAIL  ") + name + (f"\n      {detail}" if detail and not ok else ""))


def tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / line for line in out.stdout.splitlines() if line]


def text_files(files):
    return [f for f in files if f.suffix in TEXT_SUFFIXES or f.name == "lrc"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tag", help="the tag you are about to create, e.g. v1.2.0")
    args = ap.parse_args()
    files = tracked()

    # 1. Versions agree
    init = (ROOT / "src/lightroom_bridge/__init__.py").read_text()
    pkg = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)
    server = json.loads((ROOT / "server.json").read_text())
    srv = {server["version"]} | {p["version"] for p in server.get("packages", [])}
    changelog = (ROOT / "CHANGELOG.md").read_text()
    released = [v for v in re.findall(r"^## \[([^\]]+)\]", changelog, re.M) if v != "Unreleased"]
    top = released[0] if released else None
    versions = {"package": pkg, "server.json": ",".join(sorted(srv)), "CHANGELOG": top}
    if args.tag:
        versions["tag"] = args.tag.lstrip("v")
    check("versions agree " + str(versions), len(set(versions.values())) == 1 and srv == {pkg},
          "bump every location listed in docs/RELEASING.md step 2")

    # 2. Code compiles, CLI starts
    bad = []
    for f in files:
        if f.suffix == ".py":
            try:
                compile(f.read_text(), str(f), "exec")  # syntax check, writes nothing
            except SyntaxError as e:
                bad.append(f"{f.relative_to(ROOT)}:{e.lineno}: {e.msg}")
    check("python files compile", not bad, "; ".join(bad))
    cli = subprocess.run([sys.executable, "bridge/lrc.py", "--help"], cwd=ROOT, capture_output=True, text=True)
    check("CLI starts (lrc --help)", cli.returncode == 0, cli.stderr.strip()[-300:])

    # 3. KB ships empty
    kb_extra = [str(f.relative_to(ROOT)) for f in files
                if str(f.relative_to(ROOT)).startswith("kb/")
                and f.name != ".gitkeep" and str(f.relative_to(ROOT)) not in KB_ALLOWED]
    check("kb/ ships empty (no cards)", not kb_extra, "remove from the release: " + ", ".join(kb_extra))
    val = subprocess.run([sys.executable, "bridge/kb.py", "validate"], cwd=ROOT, capture_output=True, text=True)
    check("kb.py validate", val.returncode == 0, (val.stdout + val.stderr).strip()[-300:])

    texts = text_files(files)
    this = Path(__file__).resolve()

    # 4. No personal paths (this file is exempt: it spells the patterns out)
    home = re.compile(r"/Users/(?!you\b)[A-Za-z0-9_.-]+/|/home/[A-Za-z0-9_.-]+/")
    hits = [f"{f.relative_to(ROOT)}:{i}" for f in texts if f.resolve() != this
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1) if home.search(line)]
    check("no absolute home-directory paths", not hits, ", ".join(hits[:10]))

    # 5. No em/en dashes
    dash = [f"{f.relative_to(ROOT)}:{i}" for f in texts
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1)
            if EM_DASH in line or EN_DASH in line]
    check("no em-dashes or en-dashes", not dash, ", ".join(dash[:10]))

    # 6. Relative Markdown links resolve
    broken = []
    for f in files:
        if f.suffix != ".md":
            continue
        for link in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", f.read_text()):
            if not link.startswith(("http://", "https://", "mailto:")) and not (f.parent / link).exists():
                broken.append(f"{f.relative_to(ROOT)} -> {link}")
    check("relative Markdown links resolve", not broken, ", ".join(broken[:10]))

    # 7. Clean tree
    st = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    check("working tree clean", not st.stdout.strip(), st.stdout.strip()[:300])

    ok = all(results)
    print("\nREADY TO TAG" if ok else "\nNOT READY: fix the FAIL lines above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
