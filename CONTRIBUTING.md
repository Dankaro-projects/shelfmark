# Contributing

Bug reports, reproductions and small focused patches are all welcome.

## Reporting a bug

Include the output of:

```sh
shelfmark doctor --report
```

It is JSON, about 1.5 KB, and it is built so you do not have to read it
before pasting: counts, states and verdict codes only — no path, no
filename, no root label, no string from your config. It carries the
version, the platform and architecture, the shape of the catalogue, and
the failure streak, which is what separates a transient from something
that has been wrong since an upgrade.

Nothing in shelfmark phones home, which is the point of it and also why a
broken catalogue on your machine is invisible from here. That report is
the only channel there is, so it is worth pasting even when you think the
problem is obvious.

## Before you open a PR

```sh
git clone https://github.com/Dankaro-projects/shelfmark
cd shelfmark
uv run --group dev pytest
```

The test corpus — including its OOXML files — is synthesised on every run.
No fixture binaries are committed and no real document is ever read.

## Two rules that are not negotiable

**Never commit operator data.** No real `config.toml`, no catalogue
(`*.db`), no personal names, paths or client vocabulary — not in code, not
in comments, not in tests. Everything corpus-specific enters through config.
This is why the fixtures are synthetic.

**A guard is only as good as the test that can fail without it.** Before
trusting a new test, break the thing it covers and check it goes red. This
is not ceremony: an early version of this suite passed 75/75 while three
protections were mutated out from under it. Two assertions were written so
they could never fail, and one guard was never reached because a different
guard fired first. Neither class of mistake is visible from a green run.

## What a good patch looks like

- **A test that fails before your change and passes after.** For anything
  touching the trust boundary or the rights model, that test is the point of
  the patch.
- **Explain the *why* in a comment, not the *what*.** The code says what it
  does. Comments here carry the reasons that would otherwise get re-broken —
  why a comparison resolves paths, why a default is not seeded from thin
  evidence.
- **No new option unless the right answer genuinely varies by operator.**
  Corpus vocabulary varies; correct behaviour does not. If the engine can
  decide, it should.

## Areas where a patch is especially welcome

- **Classification rules for other languages.** The built-ins are English
  and Spanish. The mechanism is `[doc_types]` / `[context_types]`, and rules
  should be anchored on both sides — an unanchored `rfi` matches inside
  "Docke**rfi**le".
- **MCP clients other than Claude Code.** If it works, say so; if it does
  not, a reproduction is useful.
- **Reports that a search should have found something.** That is what
  `shelfmark misses` exists to gather, and it is the evidence that decides
  whether content extraction ever gets built.

## Reporting a security issue

Not through a public issue — see [SECURITY.md](SECURITY.md).

## Licence

By contributing you agree your work is released under the MIT licence, the
same as the rest of the project.
