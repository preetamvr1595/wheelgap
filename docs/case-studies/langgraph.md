
# LangGraph: test dependencies on Python 3.10–3.14

[LangGraph](https://github.com/langchain-ai/langgraph) is a framework for building stateful, multi-actor applications with LLMs. This case study pins revision [`3e3dbd42c5329dd404669ccd923c005c814bee3a`](https://github.com/langchain-ai/langgraph/tree/3e3dbd42c5329dd404669ccd923c005c814bee3a), uses **wheelgap 0.14.0**, and records package availability checked on **26 November 2026**.

## Input, selection and targets

The input is LangGraph's [`libs/langgraph/uv.lock`](https://github.com/langchain-ai/langgraph/blob/3e3dbd42c5329dd404669ccd923c005c814bee3a/libs/langgraph/uv.lock).

The package-specific [CI workflow](https://github.com/langchain-ai/langgraph/blob/3e3dbd42c5329dd404669ccd923c005c814bee3a/.github/workflows/_test_langgraph.yml) tests Python 3.10, 3.11, 3.12, 3.13 and 3.14 on `ubuntu-latest`. It runs from `libs/langgraph` and installs the test group using `uv sync --frozen --group test --no-dev`.

Accordingly, this scan checks:

- **Python:** 3.10, 3.11, 3.12, 3.13 and 3.14
- **Platform:** `linux-x86_64`, reflecting the Ubuntu CI runner
- **Dependency selection:** test group, excluding development dependencies
- **Lockfile:** the pinned `uv.lock` from the revision above

The Linux target uses wheelgap's default glibc compatibility floor of 2.36; this is a scan assumption, not a measured version from the CI runner.

## Reproduce

Download the two input files from the pinned revision:

```bash
curl -fL https://raw.githubusercontent.com/langchain-ai/langgraph/3e3dbd42c5329dd404669ccd923c005c814bee3a/libs/langgraph/uv.lock -o uv.lock
curl -fL https://raw.githubusercontent.com/langchain-ai/langgraph/3e3dbd42c5329dd404669ccd923c005c814bee3a/libs/langgraph/pyproject.toml -o pyproject.toml
```

Run the scan with wheelgap 0.14.0:

```bash
uvx --python 3.12 --from wheelgap==0.14.0 wheelgap uv.lock -p 3.10 -p 3.11 -p 3.12 -p 3.13 -p 3.14 -t linux-x86_64 --group test --no-dev --explain --suggest
```

## Results

The scan examined **103 indexed packages**.

- **102 packages** had wheels for every selected Python/platform target.
- **1 package** had a source-build gap.
- **7 editable local workspace packages** were skipped because they are not index packages.

The affected dependency is:

| Package | Version | Affected targets | Result |
|---|---:|---|---|
| `forbiddenfruit` | `0.1.4` | Python 3.11, 3.12 and 3.13 on `linux-x86_64` | Source distribution required; no wheels published for this version |

The dependency path reported by wheelgap is:

`test group → langgraph-cli → langgraph-runtime-inmem → blockbuster → forbiddenfruit`

No source gap was reported for this package on Python 3.10 or 3.14 in the selected target matrix.

## Gaps and options

Wheelgap reported that `forbiddenfruit` **0.1.5** has wheels for the three affected targets:

| Current pin | Suggested version | Targets with wheels |
|---|---|---|
| `forbiddenfruit==0.1.4` | `0.1.5` | Python 3.11, 3.12 and 3.13 on `linux-x86_64` |

This is a wheel-availability suggestion, not proof that changing the dependency is compatible with LangGraph. Any update should be resolved through the project's dependency constraints and tested before adoption.

## Scope and limitations

This study reports wheel availability for the selected locked dependency set. It does not establish that a source build will succeed, nor does it test LangGraph runtime behavior.

The seven editable local workspace packages are outside the index-package scan. Results may change when the lockfile or package files are updated.
