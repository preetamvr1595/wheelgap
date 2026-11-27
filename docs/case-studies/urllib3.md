# urllib3: the brotli extra on free-threaded 3.14

[urllib3](https://github.com/urllib3/urllib3) is an HTTP client library for Python. This study checks the development
dependencies in its `uv.lock` at revision
[`d9f9b0ad5b99247e06aa9a50b7a5747aa5c2e35d`](https://github.com/urllib3/urllib3/tree/d9f9b0ad5b99247e06aa9a50b7a5747aa5c2e35d),
with **wheelgap 0.13.0** and the package files available on **21 November 2026**.

The result has two parts. Everything the CI test job installs from PyPI has a wheel on CPython 3.15 and on
free-threaded CPython 3.14, on all three platforms. And the one extra that the test session leaves out on
free-threaded builds, `brotli`, is a real gap there: Brotli 1.2.1 has no free-threaded wheel, so `urllib3[brotli]` on
CPython 3.14t builds Brotli from source (see [below](#the-brotli-extra-on-314t)).

## Targets and selection

The pinned [test matrix](https://github.com/urllib3/urllib3/blob/d9f9b0ad5b99247e06aa9a50b7a5747aa5c2e35d/.github/workflows/ci.yml#L43-L53)
runs on `ubuntu-26.04`, `windows-latest` and `macos-26` for Python 3.10 to 3.15 and for free-threaded 3.14t; the 3.15
jobs are marked experimental. This study checks the two newest interpreters, 3.15 and 3.14t, on `linux-x86_64`,
`win-amd64` and `macos-arm64`. It uses a glibc 2.39 floor for Linux and macOS 26.0 for the `macos-26` runner; these
are explicit floors, not measurements of the runners, and the results below are the same with wheelgap's defaults
(glibc 2.36 and macOS 14.0).

The [Nox test session](https://github.com/urllib3/urllib3/blob/d9f9b0ad5b99247e06aa9a50b7a5747aa5c2e35d/noxfile.py#L39-L54)
installs with `uv sync --frozen --group dev` and the `socks`, `brotli`, `zstd` and `h2` extras. On a free-threaded
interpreter it leaves `brotli` out. The first two scans below mirror those choices; the documentation group and the
matrix's other sessions aren't part of this check.

## Reproduce the scans

In an empty folder, download the pinned `uv.lock` and `pyproject.toml`, then scan each interpreter with its own
selection:

```bash
curl -fL https://raw.githubusercontent.com/urllib3/urllib3/d9f9b0ad5b99247e06aa9a50b7a5747aa5c2e35d/uv.lock -o uv.lock
curl -fL https://raw.githubusercontent.com/urllib3/urllib3/d9f9b0ad5b99247e06aa9a50b7a5747aa5c2e35d/pyproject.toml -o pyproject.toml
uvx --from wheelgap==0.13.0 wheelgap uv.lock -p 3.15 \
  -t linux-x86_64,win-amd64,macos-arm64 --glibc 2.39 --macos 26.0 \
  --group dev --extra socks --extra brotli --extra zstd --extra h2 --offline --explain
uvx --from wheelgap==0.13.0 wheelgap uv.lock -p 3.14t \
  -t linux-x86_64,win-amd64,macos-arm64 --glibc 2.39 --macos 26.0 \
  --group dev --extra socks --extra zstd --extra h2 --offline --explain
```

Output of the first scan:

```text
wheelgap 0.13.0 · uv.lock · 64 packages · Python 3.15

skipped 2 non-index packages: hypercorn (git), urllib3 (editable)
✓ All 64 packages have a wheel for every one of 3 targets.
```

The 3.14t scan reports wheels for all **63** packages, with the same two skipped sources; the one package fewer is
Brotli. Both scans exit **0**. The counts are the package rows wheelgap reports. The two skipped packages don't come
from an index: `urllib3` is the editable project, and the lock pins `hypercorn` to a git revision, which uv builds
from that checkout. Whether those two build is outside a wheel check.

## The brotli extra on 3.14t

Adding the extra back on the free-threaded interpreter shows what the test session's selection steps around:

```bash
uvx --from wheelgap==0.13.0 wheelgap uv.lock -p 3.14t \
  -t linux-x86_64,win-amd64,macos-arm64 --glibc 2.39 --macos 26.0 \
  --group dev --extra socks --extra brotli --extra zstd --extra h2 --explain --suggest
```

This scan exits **1**:

```text
wheelgap 0.13.0 · uv.lock · 64 packages · Python 3.14t

package  version  linux-x86_64  win-amd64  macos-arm64
brotli   1.2.1    ● source      ● source   ● source

63 packages with wheels for every target hidden (--all to show)
```

and, after the `--explain` details, ends with:

```text
brotli 1.2.1: no newer release has a wheel for linux-x86_64, win-amd64, macos-arm64

skipped 2 non-index packages: hypercorn (git), urllib3 (editable)
linux-x86_64  1 from source
win-amd64     1 from source
macos-arm64   1 from source
1 of 64 packages has gaps: 1 will build from source.
```

`--explain` gives the same reason on each target, for example on Windows:

```text
brotli 1.2.1 on win-amd64: ● source
  via: urllib3 → brotli
  brotli-1.2.1-cp314-cp314-win_amd64.whl         built for cp314-cp314, not CPython 3.14t
```

Brotli 1.2.1 publishes wheels for the regular builds of CPython up to 3.15 and for PyPy, but none tagged `cp314t`,
and a free-threaded interpreter can't load a `cp314-cp314` wheel. It has an sdist, so wheelgap says `source`, not
`missing`. Brotli 1.2.1 is the newest release, which is why `--suggest` has nothing to offer. On regular CPython 3.15
the same pin is a wheel on every target, as the first scan shows.

`brotli` is one of urllib3's published extras, so this is not only a test-environment detail: at this pin,
`urllib3[brotli]` on CPython 3.14t needs a C compiler on all three platforms. Two options, neither of which was tried
for this study:

- leave the `brotli` extra out on free-threaded interpreters, as the pinned test session does;
- build Brotli from its sdist. Whether that build succeeds, and whether the result works on a free-threaded
  interpreter, is not something a wheel check can say.

## Check Brotli with pip

Both Brotli results were checked against PyPI with pip **26.3**. For the Windows cell on CPython 3.15:

```bash
uvx --from pip==26.3 python -m pip download --no-deps --only-binary=:all: --index-url https://pypi.org/simple \
  --python-version 3.15 --implementation cp --abi cp315 --platform win_amd64 brotli==1.2.1
```

For Linux, pass the `manylinux_2_N_x86_64` tags from 2.39 down to 2.5 and `manylinux2014_x86_64`,
`manylinux2010_x86_64` and `manylinux1_x86_64`, each with its own `--platform`. For macOS, pass
`--platform macosx_26_0_arm64`; pip expands that tag to older macOS versions and to `universal2`. pip downloaded:

| Target | Wheel downloaded for CPython 3.15 |
| --- | --- |
| `linux-x86_64` | `brotli-1.2.1-cp315-cp315-manylinux2014_x86_64.manylinux_2_17_x86_64.whl` |
| `win-amd64` | `brotli-1.2.1-cp315-cp315-win_amd64.whl` |
| `macos-arm64` | `brotli-1.2.1-cp315-cp315-macosx_10_15_universal2.whl` |

With `--python-version 3.14 --abi cp314t`, the same three commands each exit **1** with "No matching distribution
found for brotli==1.2.1", which agrees with the `source` cells above.

A matching wheel is not proof that a dependency works: neither check runs urllib3's tests or any build. Re-running
the scans after a lockfile update, or after a new Brotli release, shows whether the answer has changed.
