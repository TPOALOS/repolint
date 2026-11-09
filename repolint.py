#!/usr/bin/env python3
"""repolint - is this project ready to share? A checklist for the things people look at first.

Run it in a repository (or point it at one) before you publish. It checks the README (exists, has install and
usage, no broken links to files that do not exist), the licence (present and recognisable), .gitignore, tests,
CI (and whether the actions are pinned), changelog, and whether files that should never be committed (.env,
keys, caches, huge files) are tracked. Offline and read-only; uses `git ls-files` when it is a git repository.
Standard library only. Exit codes: 0 = no MEDIUM/HIGH findings, 1 = findings, 2 = usage error.
"""
import argparse
import json
import os
import re
import subprocess
import sys

__version__ = "1.0.0"

SEV = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
PENALTY = {"HIGH": 20, "MEDIUM": 8, "LOW": 3}
BIG_FILE = 5 * 1024 * 1024
LICENSE_NAMES = ("license", "license.md", "license.txt", "licence", "copying", "unlicense")
LICENSE_SIGNATURES = (
    ("MIT", ("permission is hereby granted, free of charge",)),
    ("Apache-2.0", ("apache license", "version 2.0")),
    ("GPL-3.0", ("gnu general public license", "version 3")),
    ("GPL-2.0", ("gnu general public license", "version 2")),
    ("LGPL", ("gnu lesser general public license",)),
    ("AGPL-3.0", ("gnu affero general public license",)),
    ("MPL-2.0", ("mozilla public license",)),
    ("BSD", ("redistribution and use in source and binary forms",)),
    ("ISC", ("permission to use, copy, modify, and/or distribute this software",)),
    ("Unlicense", ("this is free and unencumbered software",)),
)
SHOULD_NOT_BE_TRACKED = (  # (pattern, severity, rule id, description)
    (re.compile(r"(^|/)\.env(\.(?!example$|sample$|template$|dist$)[^/]+)?$"), "HIGH", "env-file", "an .env file with settings or secrets"),
    (re.compile(r"(^|/)id_(rsa|dsa|ecdsa|ed25519)$|\.(pem|key|p12|pfx|jks|kdbx)$"), "HIGH", "private-key", "a private key or keystore"),
    (re.compile(r"(^|/)(\.aws/credentials|\.netrc|\.pgpass|\.git-credentials|terraform\.tfstate(\.backup)?)$"), "HIGH",
     "credentials-file", "a credentials or state file"),
    (re.compile(r"(^|/)node_modules/"), "MEDIUM", "node-modules", "node_modules"),
    (re.compile(r"(^|/)(__pycache__/|[^/]+\.pyc$)"), "MEDIUM", "bytecode", "Python bytecode caches"),
    (re.compile(r"(^|/)(venv|\.venv)/"), "MEDIUM", "virtualenv", "a virtual environment"),
    (re.compile(r"(^|/)\.DS_Store$"), "LOW", "ds-store", "macOS .DS_Store files"),
    (re.compile(r"(^|/)(\.idea/|\.vscode/settings\.json$)"), "LOW", "editor-settings", "editor settings"),
    (re.compile(r"(^|/)[^/]+\.(log|swp|tmp)$"), "LOW", "log-files", "log or temporary files"),
    (re.compile(r"(^|/)(dist|build)/.*\.(whl|tar\.gz)$|\.egg-info/"), "LOW", "build-artifacts", "build artifacts"),
)
MD_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SECRET_HINTS = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{36,}\b")
SHA_REF = re.compile(r"@[0-9a-f]{40}(\s|$)")


def git_files(root):
    """Tracked files via git, or None when ``root`` is not a git repository (or git is missing)."""
    try:
        res = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return [f for f in res.stdout.decode("utf-8", "replace").split("\0") if f]


def walk_files(root):
    out = []
    for dirpath, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "node_modules", ".venv", "venv", "__pycache__"))
        for n in sorted(names):
            out.append(os.path.relpath(os.path.join(dirpath, n), root).replace(os.sep, "/"))
    return out


def read_text(root, rel, limit=400_000):
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def find(files, names, directory_ok=False):
    """First tracked file at the repository top level whose lower-cased name is in ``names``."""
    for f in files:
        if "/" not in f and f.lower() in names:
            return f
    return None


def identify_license(text):
    low = " ".join(text.lower().split())
    for name, needles in LICENSE_SIGNATURES:
        if all(n in low for n in needles):
            return name
    return None


def broken_links(root, readme_rel, text, files):
    """Relative links/images in a Markdown file that point to files which do not exist."""
    known = set(files)
    base = os.path.dirname(readme_rel)
    broken, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for target in MD_LINK.findall(line):
            if re.match(r"^([a-z][a-z0-9+.-]*:|#|//|mailto:)", target, re.I):
                continue
            path = target.split("#", 1)[0].split("?", 1)[0]
            if not path:
                continue
            rel = os.path.normpath(os.path.join(base, path)).replace(os.sep, "/")
            if rel.startswith(".."):
                continue
            if rel in known or any(f.startswith(rel.rstrip("/") + "/") for f in known) or os.path.exists(os.path.join(root, rel)):
                continue
            broken.append(target)
    return broken


def lint(root):
    root = os.path.abspath(root)
    tracked = git_files(root)
    is_git = tracked is not None
    files = tracked if is_git else walk_files(root)
    findings = []
    add = lambda sev, rule, msg, fix="": findings.append({"severity": sev, "rule": rule, "message": msg, "fix": fix})
    lowered = [f.lower() for f in files]

    readme = find(files, ("readme.md", "readme", "readme.rst", "readme.txt", "readme.markdown"))
    if not readme:
        add("HIGH", "no-readme", "there is no README", "add a README.md: what it is, how to install it, one example")
    else:
        text = read_text(root, readme)
        if len(text.strip()) < 300:
            add("MEDIUM", "thin-readme", "the README is very short (%d characters)" % len(text.strip()),
                "explain what the project does, how to install it and show a short example")
        if not re.search(r"(?im)^#{1,3}\s*.*\b(install|getting started|quick ?start|setup|usage)\b", text) and \
                not re.search(r"(?i)pip install|npm install|cargo install|go install|brew install|apt(-get)? install|docker run", text):
            add("MEDIUM", "readme-no-install", "the README does not say how to install or run it",
                "add an Install or Usage section with the exact commands")
        if "```" not in text and "    " not in text:
            add("LOW", "readme-no-example", "the README has no code example", "show a copy-pasteable example and its output")
        if not re.search(r"(?i)licen[sc]e", text):
            add("LOW", "readme-no-license-mention", "the README does not mention the licence", "add a short License section")
        broken = broken_links(root, readme, text, files) if readme.lower().endswith((".md", ".markdown")) else []
        for target in sorted(set(broken))[:10]:
            add("MEDIUM", "broken-link", "the README links to %s, which is not in the repository" % target,
                "fix the path or add the file")

    lic = find(files, LICENSE_NAMES)
    if not lic:
        add("HIGH", "no-license", "there is no LICENSE file, so legally nobody may reuse the code",
            "add one (MIT and Apache-2.0 are common permissive choices)")
    else:
        kind = identify_license(read_text(root, lic))
        if kind is None:
            add("LOW", "unknown-license", "the licence text in %s is not a licence this tool recognises" % lic,
                "use the unmodified text of a standard licence")
        elif kind == "MIT":
            text = read_text(root, lic)
            if not re.search(r"copyright\s*(\(c\)|©)?\s*\d{4}\s+\S+", text, re.I):
                add("LOW", "license-no-copyright", "the MIT licence has no 'Copyright (c) YEAR NAME' line", "add your name and the year")

    if is_git:
        if ".gitignore" not in files:
            add("MEDIUM", "no-gitignore", "there is no .gitignore", "add one so caches, secrets and build output stay out of git")
    elif not os.path.exists(os.path.join(root, ".gitignore")):
        add("MEDIUM", "no-gitignore", "there is no .gitignore", "add one so caches, secrets and build output stay out of git")
    if not is_git:
        add("LOW", "not-a-git-repo", "this folder is not a git repository, so tracked-file checks used a folder scan",
            "run `git init` before publishing")

    for rx, sev, rule_id, label in SHOULD_NOT_BE_TRACKED:
        hits = [f for f in files if rx.search(f)]
        if hits:
            shown = ", ".join(hits[:3]) + (" and %d more" % (len(hits) - 3) if len(hits) > 3 else "")
            add(sev, "tracked-" + rule_id,
                "%s is %s: %s" % ("tracked" if is_git else "present", label, shown),
                "remove it from git (`git rm --cached`) and list it in .gitignore")

    big = []
    for f in files:
        try:
            size = os.path.getsize(os.path.join(root, f))
        except OSError:
            continue
        if size > BIG_FILE:
            big.append((f, size))
    for f, size in sorted(big, key=lambda x: -x[1])[:5]:
        add("MEDIUM", "large-file", "%s is %.1f MB" % (f, size / 1048576),
            "keep large files out of the repository (Git LFS, a release asset or a download script)")

    has_tests = any(re.match(r"(tests?/|.*(^|/)test_[^/]*\.\w+$|.*[^/]+_test\.\w+$|.*\.(test|spec)\.\w+$)", f) for f in files)
    if not has_tests:
        add("MEDIUM", "no-tests", "no tests were found", "add tests so contributors can change things safely")

    workflows = [f for f in files if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))]
    ci_other = any(f in lowered for f in (".gitlab-ci.yml", "azure-pipelines.yml", ".travis.yml", "jenkinsfile", ".circleci/config.yml")) \
        or any(f.startswith(".circleci/") for f in lowered)
    if not workflows and not ci_other:
        add("MEDIUM", "no-ci", "no continuous integration is configured", "add a workflow that runs the tests on every push")
    unpinned = []
    for wf in workflows:
        for line in read_text(root, wf).splitlines():
            m = re.match(r"^\s*(?:-\s+)?uses:\s*['\"]?([^'\"\s#]+)", line)
            if m and "@" in m.group(1) and not m.group(1).startswith(("./", "docker://")) and not SHA_REF.search(m.group(1) + " "):
                unpinned.append(m.group(1))
    if unpinned:
        add("LOW", "unpinned-actions", "%d workflow action(s) are not pinned to a commit SHA (for example %s)" % (
            len(unpinned), unpinned[0]), "pin to the full commit SHA with the version in a comment")

    if not find(files, ("changelog.md", "changelog", "changes.md", "history.md", "news.md", "releases.md")):
        add("LOW", "no-changelog", "no CHANGELOG", "keep a short changelog so users can see what changed")
    if not any(f.lower() in ("security.md", ".github/security.md") for f in files) and has_tests:
        add("LOW", "no-security-policy", "no SECURITY.md", "say how to report a vulnerability privately")
    manifests = [f for f in files if f in ("pyproject.toml", "setup.py", "package.json", "Cargo.toml", "go.mod", "setup.cfg")]
    if not manifests and any(f.endswith(".py") for f in files):
        add("LOW", "no-packaging", "Python code but no pyproject.toml or setup.py", "add a pyproject.toml so `pip install` works")

    leaked = []
    for f in files:
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz", ".pdf", ".ico", ".woff", ".woff2")):
            continue
        try:
            if os.path.getsize(os.path.join(root, f)) > 400_000:
                continue
        except OSError:
            continue
        if SECRET_HINTS.search(read_text(root, f)) and not re.search(r"(^|/)(test|tests)/|(^|/)test_", f):
            leaked.append(f)
    if leaked:
        add("HIGH", "possible-secret", "something that looks like a private key or token is in %s" % ", ".join(leaked[:3]),
            "remove it, rotate the credential and run a secret scanner over the history")

    findings.sort(key=lambda f: (SEV[f["severity"]], f["rule"]))
    score = max(0, 100 - sum(PENALTY[f["severity"]] for f in findings))
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "F"
    return {"path": root, "git": is_git, "files": len(files), "score": score, "grade": grade, "findings": findings}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check whether a project is ready to share.")
    ap.add_argument("paths", nargs="*", default=["."], help="repository folder(s) (default: .)")
    ap.add_argument("--min-severity", choices=("LOW", "MEDIUM", "HIGH"), default="LOW", help="hide findings below this level")
    ap.add_argument("--fail-on", choices=("LOW", "MEDIUM", "HIGH"), default="MEDIUM", help="exit 1 at this severity or above (default MEDIUM)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    results = []
    for p in args.paths:
        if not os.path.isdir(p):
            print("repolint: not a folder: %s" % p, file=sys.stderr)
            return 2
        results.append(lint(p))
    shown, fail_at = SEV[args.min_severity], SEV[args.fail_on]
    if args.json:
        print(json.dumps([dict(r, findings=[f for f in r["findings"] if SEV[f["severity"]] <= shown]) for r in results], indent=2))
    else:
        for r in results:
            print("== %s  score %d/100  grade %s  (%d files%s)" % (r["path"], r["score"], r["grade"], r["files"], "" if r["git"] else ", not a git repo"))
            visible = [f for f in r["findings"] if SEV[f["severity"]] <= shown]
            for f in visible:
                print("%-6s %-24s %s%s" % (f["severity"], f["rule"], f["message"], "\n         fix: " + f["fix"] if f["fix"] else ""))
            if not visible:
                print("ready to share: nothing to fix")
    return 1 if any(SEV[f["severity"]] <= fail_at for r in results for f in r["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
