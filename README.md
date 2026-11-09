# repolint

**Is this project ready to share?** A checklist for the things people look at first, and a few things that hurt when they are
missed. Run it before you publish a repository.

```
$ repolint .
== /home/me/mytool  score 62/100  grade C  (14 files)
HIGH   no-license                the LICENSE file is missing, so legally nobody may reuse the code
         fix: add one (MIT and Apache-2.0 are common permissive choices)
MEDIUM broken-link               the README links to docs/usage.png, which is not in the repository
         fix: fix the path or add the file
MEDIUM no-ci                     no continuous integration is configured
         fix: add a workflow that runs the tests on every push
MEDIUM no-tests                  no tests were found
         fix: add tests so contributors can change things safely
LOW    unpinned-actions          2 workflow action(s) are not pinned to a commit SHA (for example actions/checkout@v4)
         fix: pin to the full commit SHA with the version in a comment
```

Offline and read-only; uses `git ls-files` so it judges what is actually committed. One file, standard library only.

## Install and use

```bash
pip install git+https://github.com/TPOALOS/repolint          # or copy repolint.py
repolint                                     # the current folder
repolint ~/code/mytool ~/code/other
repolint . --fail-on HIGH --min-severity MEDIUM
repolint . --json
```

Exit codes: `0` nothing at `--fail-on` (default MEDIUM) or above, `1` findings, `2` not a folder.

## What it checks

| Area | Checks |
|---|---|
| README | exists; not tiny; says how to install or run; has a code example; mentions the licence; **relative links and images point to files that exist** (code fences are skipped) |
| Licence | present; recognised (MIT, Apache-2.0, GPL, LGPL, AGPL, MPL, BSD, ISC, Unlicense); MIT has a copyright line |
| Hygiene | `.gitignore` exists; **tracked** `.env` files, private keys and keystores, credentials files, `node_modules`, Python caches, virtualenvs, `.DS_Store`, logs, build artifacts; files over 5 MB; text that looks like a private key or token |
| Quality | tests exist; CI is configured (GitHub Actions, GitLab, CircleCI, Travis, Azure, Jenkins) and its actions are pinned to commit SHAs; a changelog; a `SECURITY.md`; Python code has a `pyproject.toml` |

Findings are `HIGH`, `MEDIUM` or `LOW`; the score starts at 100 and loses 20, 8 or 3 per finding (A 90 and up, B 75, C 60, D 40).
`--min-severity` only hides low findings from the output, the score still counts them. Outside a git repository it scans the
folder (skipping dependency folders) and says so.

## Limits

It checks that the basics exist, not that they are good: a README can pass and still be unclear. The secret check is a light
pattern match, not a replacement for a real secret scanner and for rotating anything that was ever committed.

## Tests

```bash
python3 -m unittest -v
```

## License

MIT (c) TPOALOS
