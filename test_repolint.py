import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import repolint as rl

HAVE_GIT = shutil.which("git") is not None
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

MIT = """MIT License

Copyright (c) 2026 Jane Doe

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction.
"""
README = """# demo

A small tool that does one useful thing and does it well, written so that the first impression on a stranger
visiting the repository is a good one.

## Install

```bash
pip install demo
```

## Usage

```bash
demo --help
```

See the [source](demo.py) and the [license text](LICENSE).

## License

MIT
"""
WORKFLOW = """name: tests
on: [push]
permissions:
  contents: read
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@%s
      - run: python -m unittest
""" % ("a" * 40)

GOOD = {
    "README.md": README, "LICENSE": MIT, ".gitignore": "__pycache__/\n.env\n", "tests/test_demo.py": "import unittest\n",
    ".github/workflows/tests.yml": WORKFLOW, "CHANGELOG.md": "# Changelog\n", "SECURITY.md": "report privately\n",
    "pyproject.toml": "[project]\nname='demo'\n", "demo.py": "print('hi')\n",
}


def rules(result):
    return {f["rule"] for f in result["findings"]}


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.d = self._t.name

    def tearDown(self):
        self._t.cleanup()

    def project(self, files=None, git=True, remove=(), **overrides):
        spec = dict(GOOD if files is None else files)
        spec.update(overrides)
        for r in remove:
            spec.pop(r, None)
        for rel, content in spec.items():
            p = os.path.join(self.d, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(content if isinstance(content, bytes) else content.encode())
        if git:
            subprocess.run(["git", "init", "-q", "-b", "main", self.d], check=True, env=ENV)
            subprocess.run(["git", "-C", self.d, "add", "-A"], check=True, env=ENV)
        return self.d

    def lint(self, **kw):
        self.project(**kw)
        return rl.lint(self.d)


@unittest.skipUnless(HAVE_GIT, "git not installed")
class HealthyProjectTests(Base):
    def test_a_good_project_has_nothing_to_fix(self):
        r = self.lint()
        self.assertEqual(r["findings"], [])
        self.assertEqual((r["score"], r["grade"], r["git"]), (100, "A", True))

    def test_without_git_only_the_git_hint_appears(self):
        r = self.lint(git=False)
        self.assertEqual(rules(r), {"not-a-git-repo"})
        self.assertFalse(r["git"])


@unittest.skipUnless(HAVE_GIT, "git not installed")
class ReadmeTests(Base):
    def test_missing_readme(self):
        r = self.lint(remove=["README.md"])
        self.assertIn("no-readme", rules(r))
        self.assertEqual(r["findings"][0]["severity"], "HIGH")

    def test_other_readme_names_count(self):
        for name in ("README", "readme.rst", "Readme.txt"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as d:
                    self.d = d
                    r = self.lint(remove=["README.md"], **{name: README})
                    self.assertNotIn("no-readme", rules(r))

    def test_thin_readme(self):
        self.assertIn("thin-readme", rules(self.lint(**{"README.md": "# demo\nstuff\n"})))

    def test_no_install_or_usage(self):
        text = "# demo\n" + "It does a great many wonderful and truly remarkable things for you. " * 8 + "\nMIT license.\n```\nx\n```\n"
        self.assertIn("readme-no-install", rules(self.lint(**{"README.md": text})))

    def test_install_command_without_a_heading_is_enough(self):
        text = "# demo\n" + "A long description sentence about what this does for people. " * 8 + "\n\npip install demo\n\nMIT license\n```\nx\n```\n"
        self.assertNotIn("readme-no-install", rules(self.lint(**{"README.md": text})))

    def test_no_code_example(self):
        text = "# demo\n\n## Usage\n\n" + "Just run the program and look at what it prints for you today. " * 8 + "\n\nLicense: MIT\n"
        self.assertIn("readme-no-example", rules(self.lint(**{"README.md": text})))

    def test_no_licence_mention(self):
        text = README.replace("## License\n\nMIT\n", "").replace("and the [license text](LICENSE)", "")
        self.assertNotIn("icen", text)
        self.assertIn("readme-no-license-mention", rules(self.lint(**{"README.md": text})))

    def test_broken_links_and_images(self):
        text = README + "\n![shot](docs/screenshot.png) and [guide](docs/GUIDE.md#intro) and [web](https://example.com/x)\n"
        r = self.lint(**{"README.md": text})
        messages = [f["message"] for f in r["findings"] if f["rule"] == "broken-link"]
        self.assertEqual(len(messages), 2)
        self.assertTrue(any("docs/screenshot.png" in m for m in messages))
        self.assertTrue(any("docs/GUIDE.md" in m for m in messages))

    def test_links_that_resolve_are_fine(self):
        text = README + "\n[doc](docs/guide.md#top) ![img](docs/a.png) [dir](docs/) [anchor](#install) [mail](mailto:a@b.c)\n"
        r = self.lint(**{"README.md": text, "docs/guide.md": "x", "docs/a.png": b"\x89PNG"})
        self.assertNotIn("broken-link", rules(r))

    def test_links_inside_code_fences_are_ignored(self):
        text = README + "\n```md\n[example](does/not/exist.md)\n```\n"
        self.assertNotIn("broken-link", rules(self.lint(**{"README.md": text})))

    def test_links_in_a_subfolder_readme_are_relative_to_it(self):
        self.project(files={"README.md": README, "LICENSE": MIT, "docs/README.md": README + "\n[up](../CHANGELOG.md) [mine](a.md)\n",
                            "docs/a.md": "x", "CHANGELOG.md": "x"})
        self.assertEqual(rl.broken_links(self.d, "docs/README.md", "[up](../CHANGELOG.md) [mine](a.md)", rl.git_files(self.d)), [])

    def test_links_escaping_the_repository_are_not_judged(self):
        self.assertEqual(rl.broken_links(self.d, "README.md", "[x](../../outside.md)", []), [])


@unittest.skipUnless(HAVE_GIT, "git not installed")
class LicenceTests(Base):
    def test_missing(self):
        r = self.lint(remove=["LICENSE"])
        self.assertIn("no-license", rules(r))

    def test_recognised_licences(self):
        samples = {"MIT": MIT, "Apache-2.0": "Apache License\nVersion 2.0, January 2004",
                   "GPL-3.0": "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007", "GPL-2.0": "GNU General Public License Version 2, June 1991",
                   "BSD": "Redistribution and use in source and binary forms, with or without modification",
                   "ISC": "Permission to use, copy, modify, and/or distribute this software for any purpose",
                   "Unlicense": "This is free and unencumbered software released into the public domain.",
                   "MPL-2.0": "Mozilla Public License Version 2.0", "AGPL-3.0": "GNU AFFERO GENERAL PUBLIC LICENSE Version 3"}
        for name, text in samples.items():
            self.assertEqual(rl.identify_license(text), name, name)
        self.assertIsNone(rl.identify_license("do whatever you like, I guess"))

    def test_unknown_text(self):
        self.assertIn("unknown-license", rules(self.lint(LICENSE="do whatever you like")))

    def test_mit_without_copyright_line(self):
        text = MIT.replace("Copyright (c) 2026 Jane Doe", "")
        self.assertIn("license-no-copyright", rules(self.lint(LICENSE=text)))

    def test_license_file_name_variants(self):
        r = self.lint(remove=["LICENSE"], **{"LICENSE.md": MIT})
        self.assertNotIn("no-license", rules(r))


@unittest.skipUnless(HAVE_GIT, "git not installed")
class TrackedFilesTests(Base):
    def test_secrets_and_caches_tracked(self):
        files = {".env": "TOKEN=1\n", "keys/server.pem": "x", "id_rsa": "x", "node_modules/a/index.js": "x",
                 "pkg/__pycache__/m.cpython-312.pyc": b"x", ".DS_Store": b"x", "debug.log": "x", "venv/bin/python": "x"}
        files[".gitignore"] = "# nothing is ignored here\n"
        r = self.lint(**files)
        found = rules(r)
        for expected in ("tracked-env-file", "tracked-private-key", "tracked-node-modules", "tracked-bytecode",
                         "tracked-ds-store", "tracked-log-files", "tracked-virtualenv"):
            self.assertIn(expected, found, found)
        highs = [f for f in r["findings"] if f["severity"] == "HIGH"]
        self.assertTrue(any(".env" in f["message"] for f in highs))
        self.assertTrue(any("private key" in f["message"] for f in highs))

    def test_env_example_is_fine(self):
        r = self.lint(**{".env.example": "TOKEN=\n", ".env.sample": "X=\n"})
        self.assertFalse([f for f in r["findings"] if ".env" in f["message"]])

    def test_ignored_files_do_not_count_in_a_git_repo(self):
        self.project()
        with open(os.path.join(self.d, ".env"), "w") as fh:
            fh.write("SECRET=1\n")  # exists but is untracked (and listed in .gitignore)
        self.assertEqual([f for f in rl.lint(self.d)["findings"] if ".env" in f["message"]], [])

    def test_large_files(self):
        r = self.lint(**{"data/big.bin": b"0" * (rl.BIG_FILE + 10)})
        big = [f for f in r["findings"] if f["rule"] == "large-file"]
        self.assertEqual(len(big), 1)
        self.assertIn("data/big.bin", big[0]["message"])

    def test_possible_secret_in_source(self):
        key = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIB\n"
        r = self.lint(**{"deploy/notes.txt": key})
        self.assertIn("possible-secret", rules(r))

    def test_secret_like_text_in_tests_is_tolerated(self):
        key = "-----BEGIN " + "RSA PRIVATE KEY-----"
        self.assertNotIn("possible-secret", rules(self.lint(**{"tests/test_keys.py": "FAKE = '%s'\n" % key})))


@unittest.skipUnless(HAVE_GIT, "git not installed")
class ProjectStructureTests(Base):
    def test_no_gitignore(self):
        self.assertIn("no-gitignore", rules(self.lint(remove=[".gitignore"])))

    def test_no_tests(self):
        self.assertIn("no-tests", rules(self.lint(remove=["tests/test_demo.py"])))

    def test_test_file_layouts(self):
        for name in ("test_x.py", "x_test.go", "src/app.spec.ts", "tests/anything.py", "test/foo.rb", "src/app.test.js"):
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as d:
                    self.d = d
                    r = self.lint(remove=["tests/test_demo.py"], **{name: "x"})
                    self.assertNotIn("no-tests", rules(r), name)

    def test_no_ci(self):
        self.assertIn("no-ci", rules(self.lint(remove=[".github/workflows/tests.yml"])))

    def test_other_ci_systems_count(self):
        r = self.lint(remove=[".github/workflows/tests.yml"], **{".gitlab-ci.yml": "test:\n  script: make\n"})
        self.assertNotIn("no-ci", rules(r))

    def test_unpinned_actions(self):
        wf = WORKFLOW.replace("@" + "a" * 40, "@v4") + "      - uses: actions/setup-python@v5\n      - uses: ./local\n"
        r = self.lint(**{".github/workflows/tests.yml": wf})
        found = [f for f in r["findings"] if f["rule"] == "unpinned-actions"]
        self.assertEqual(len(found), 1)
        self.assertIn("2 workflow action", found[0]["message"])

    def test_changelog_and_security_policy(self):
        r = self.lint(remove=["CHANGELOG.md", "SECURITY.md"])
        self.assertTrue({"no-changelog", "no-security-policy"} <= rules(r))

    def test_python_without_packaging(self):
        self.assertIn("no-packaging", rules(self.lint(remove=["pyproject.toml"])))

    def test_findings_are_sorted_by_severity(self):
        r = self.lint(files={"app.py": "x"})
        order = [f["severity"] for f in r["findings"]]
        self.assertEqual(order, sorted(order, key={"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get))

    def test_score_and_grade_fall_with_problems(self):
        good = self.lint()["score"]
        with tempfile.TemporaryDirectory() as d:
            self.d = d
            bad = self.lint(files={"app.py": "x", ".env": "A=1\n"})
        self.assertGreater(good, bad["score"])
        self.assertIn(bad["grade"], ("D", "F"))
        self.assertGreaterEqual(bad["score"], 0)


class NoGitTests(Base):
    def test_folder_scan_when_git_is_missing_or_not_a_repo(self):
        self.project(git=False)
        r = rl.lint(self.d)
        self.assertFalse(r["git"])
        self.assertGreater(r["files"], 5)

    def test_folder_scan_skips_dependency_folders(self):
        self.project(git=False, **{"node_modules/x/index.js": "x", ".venv/bin/x": "x"})
        self.assertNotIn("node_modules", " ".join(rl.walk_files(self.d)))
        self.assertNotIn(".venv", " ".join(rl.walk_files(self.d)))

    def test_git_files_returns_none_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(rl.git_files(d))


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = rl.main(list(argv))
    return code, out.getvalue(), err.getvalue()


@unittest.skipUnless(HAVE_GIT, "git not installed")
class CliTests(Base):
    def test_healthy_project(self):
        self.project()
        code, out, _ = run_main(self.d)
        self.assertEqual(code, 0)
        self.assertIn("grade A", out)
        self.assertIn("ready to share", out)

    def test_findings_exit_one_with_fixes(self):
        self.project(remove=["LICENSE", "tests/test_demo.py"])
        code, out, _ = run_main(self.d)
        self.assertEqual(code, 1)
        self.assertIn("HIGH   no-license", out)
        self.assertIn("fix:", out)

    def test_low_findings_alone_pass_by_default(self):
        self.project(remove=["CHANGELOG.md", "SECURITY.md"])
        self.assertEqual(run_main(self.d)[0], 0)
        self.assertEqual(run_main(self.d, "--fail-on", "LOW")[0], 1)

    def test_min_severity_hides_but_does_not_change_the_score(self):
        self.project(remove=["CHANGELOG.md"])
        _, full, _ = run_main(self.d, "--json")
        _, quiet, _ = run_main(self.d, "--json", "--min-severity", "MEDIUM")
        a, b = json.loads(full)[0], json.loads(quiet)[0]
        self.assertEqual(a["score"], b["score"])
        self.assertEqual(b["findings"], [])

    def test_json_structure(self):
        self.project(remove=["LICENSE"])
        _, out, _ = run_main(self.d, "--json")
        data = json.loads(out)[0]
        self.assertEqual(set(data), {"path", "git", "files", "score", "grade", "findings"})
        self.assertEqual(set(data["findings"][0]), {"severity", "rule", "message", "fix"})

    def test_several_projects(self):
        self.project()
        with tempfile.TemporaryDirectory() as other:
            code, out, _ = run_main(self.d, other)
        self.assertEqual(code, 1)
        self.assertEqual(out.count("== "), 2)

    def test_not_a_folder(self):
        self.assertEqual(run_main("/nonexistent/project")[0], 2)

    def test_default_is_the_current_folder(self):
        self.project()
        cwd = os.getcwd()
        try:
            os.chdir(self.d)
            self.assertEqual(run_main()[0], 0)
        finally:
            os.chdir(cwd)

    def test_never_modifies_the_project(self):
        self.project(remove=["LICENSE"])
        before = sorted(os.listdir(self.d))
        run_main(self.d)
        self.assertEqual(sorted(os.listdir(self.d)), before)


if __name__ == "__main__":
    unittest.main()
