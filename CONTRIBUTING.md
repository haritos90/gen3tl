# Contributing to gen3tl

This guide covers contributions to the gen3tl tooling: the gen3tl command, the website with its translation editor, patcher, save converter and Pokédex, and the language packs. A translation of a game is contributed in the translation project of that game, under its `CONTRIBUTING.md`; for Pokemon Unbound that is [haritos90/unbound-tl](https://github.com/haritos90/unbound-tl).

## Where a change goes

| Change | Route |
| --- | --- |
| a translation of a game: texts, glossary, pictures | the translation project of the game: an editor submission or a pull request there |
| a site catalog text, `web/i18n/<code>.po` | an editor submission of the site project to this repository, or a pull request |
| glyph redraws and keyboard records of a language pack, without game texts | an editor submission to this repository, or a pull request |
| commands, checks, pages, ROM profiles, language packs | a pull request against this repository |
| a defect or a missing capability | an issue of this repository |

- A game project pins one toolkit version. A toolkit change reaches it through a toolkit release, the tag `v<version>`, and a raise of the project's pin.

## Toolkit changes

- A pull request against the default branch, from a branch of a fork or of this repository.
- The tests pass: `PYTHONPATH=src .venv/bin/python tests/run.py`.
- A commit that changes `src/gen3tl/` or `web/` raises `__version__` in `src/gen3tl/__init__.py` by Semantic Versioning; a commit that changes only profiles, tests or documents raises nothing.
- Command output is `label  value` lines.
- The toolkit holds no path into a game project and no project data beyond the test fixtures.
- No ROM, no file built from a ROM and no ROM artwork is tracked.
- A comment states why the code does what it does, in a few words. Replaced code is deleted.
- A new language pack and its site catalog come from `gen3tl lang init`.
- A change of the shared mastheads and footers is made in `web/fragments/`; `gen3tl web site-shell --sync` writes it into the pages, and `gen3tl web site-catalog --sync` follows.

| Version part | Raised for | Reset |
| --- | --- | --- |
| MAJOR | a breaking change to a documented interface, such as the command line or a file format | MINOR and PATCH to 0 |
| MINOR | a compatible addition, such as a new command, data format or editor feature | PATCH to 0 |
| PATCH | every other change of `src/gen3tl/` or `web/` | none |

## Commit messages

```text
type(scope): summary (#N)

- a short line of the body
```

| Part | Rule |
| --- | --- |
| `type` | `feat`, `fix`, `refactor`, `docs`, `test`, `chore` or `perf` |
| `scope` | required: the area changed, such as `editor` or `release` |
| `summary` | imperative, begins with a lowercase verb, at most 15 words |
| ` (#N)` | the GitHub issue the commit resolves |
| body | optional, after one blank line; short `- ` lines |

## Review and merge

- A pull request merges with the maintainer's review; a review finding is fixed on its branch.
- Pull requests merge by rebase only: each commit reaches the default branch with its message unchanged.

## License and authorship

- A contribution is licensed under the MIT License of this repository (`LICENSE`).
- The person who submits a contribution is its sole author and answers for it, whatever tools they used.
- The contributor reviews and understands every line submitted, runs the tests and answers the review.
