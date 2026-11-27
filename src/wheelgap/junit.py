"""JUnit XML report for CI dashboards (Jenkins, GitLab, Azure Pipelines, CircleCI)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

from packaging.utils import canonicalize_name

from .check import Row, Status
from .targets import Target


def _cell_fails(status: Status, fail_on: str) -> bool:
    """Whether this target cell should fail the run, matching main()'s --fail-on rules."""
    if status in (Status.MISSING, Status.UNKNOWN):
        return True
    if status is Status.SOURCE:
        return fail_on != "missing"
    return False


def _rank(cell: tuple[str | None, str | None]) -> int:
    fail, skip = cell
    if fail is not None:
        return 4 if fail == Status.SOURCE.value else 5
    if skip is not None and skip.startswith("advisory: "):
        # A gap reported as a skip: it beats a pass, so another input's wheel can't hide it, and as on a
        # required target, a package that can't install (or couldn't be checked) beats a source build.
        return 2 if skip == f"advisory: {Status.SOURCE.value}" else 3
    return 0 if skip is not None else 1


def junit_xml(
    rows: list[Row],
    targets: list[Target],
    fail_on: str,
    advisory: frozenset[Target] = frozenset(),
    warnings: Sequence[str] = (),
) -> str:
    """One testsuite per target, one testcase per package.

    `warnings` (`Dockerfile:8: message`, from --dockerfile-inputs) are lines the answer doesn't cover: each suite
    gets a `wheelgap.dockerfile-warning` property and a system-out line for each, so a dashboard shows them.

    A cell is a failure when its status fails the run (`missing`, `unknown`,
    and `source` unless `--fail-on missing`). `n/a` is skipped, and so is a
    cell that would fail on a target `--ignore NAME:TARGET` ignores. On an
    `--advisory` target such a cell is skipped with the message `advisory:
    STATUS`, so the gap stays visible without failing the report (Jenkins
    marks a build unstable on any failed test). Other statuses (wheel,
    bundled, and source with `--fail-on missing`) are passes.

    Dashboards key a test on classname + name, so each name appears once per
    suite: a package locked at several versions gets the version in its name,
    and one that several inputs lock at the same version is one testcase,
    which fails if any of them fails.
    """
    versions: dict[str, set[str | None]] = {}
    for row in rows:
        versions.setdefault(row.package.key, set()).add(row.package.version)
    multi_py = len({t.python for t in targets}) > 1
    # A <testsuites> wrapper is accepted by every dashboard and keeps several
    # suites in one file.
    root = ET.Element("testsuites")
    for target in targets:
        label = target.label(multi_py)
        # Keyed like the rest of wheelgap, so PyYAML and pyyaml are one testcase,
        # named as the first input writes it.
        names: dict[tuple[str, str | None], str] = {}
        merged: dict[tuple[str, str | None], tuple[str | None, str | None]] = {}  # failure, skip
        # Save the origin with the winning cell: merging can replace a source gap with a missing one.
        direct_via: dict[tuple[str, str | None], tuple[str, ...]] = {}
        for row in rows:
            status = row.results[target]
            ident = (row.package.key, row.package.version)
            if ident not in names:
                name = row.package.name
                if row.package.version and len(versions[row.package.key]) > 1:
                    name = f"{name} {row.package.version}"
                names[ident] = name
            if status is Status.NOT_NEEDED:
                cell: tuple[str | None, str | None] = (None, "n/a")
            elif _cell_fails(status, fail_on) and target in row.ignored:
                cell = (None, "ignored")  # --ignore NAME:TARGET: accepted, so not a failure
            elif _cell_fails(status, fail_on) and target in advisory:
                cell = (None, f"advisory: {status.value}")  # reported, but doesn't fail the run
            elif _cell_fails(status, fail_on):
                cell = (status.value, None)
            else:
                cell = (None, None)
            # The worst cell wins: missing or unknown, then source, then an advisory gap (missing or unknown, then
            # source), then a pass, then skipped.
            if ident not in merged or _rank(cell) > _rank(merged[ident]):
                merged[ident] = cell
                # This target's own direct dependencies (not the table's union across targets), each once, spelt
                # as first seen, as the tables spell it.
                seen: dict[str, str] = {}
                for name in row.direct_via.get(target, ()):
                    seen.setdefault(canonicalize_name(name), name)
                direct_via[ident] = tuple(seen.values())
        cases = [(names[ident], fail, skip, direct_via[ident]) for ident, (fail, skip) in merged.items()]
        failures = sum(1 for _, fail, _, _ in cases if fail is not None)
        skipped = sum(1 for _, _, skip, _ in cases if skip is not None)
        suite = ET.SubElement(
            root,
            "testsuite",
            name=label,
            tests=str(len(cases)),
            failures=str(failures),
            skipped=str(skipped),
        )
        if warnings:
            properties = ET.SubElement(suite, "properties")
            for warning in warnings:
                ET.SubElement(properties, "property", name="wheelgap.dockerfile-warning", value=warning)
        for name, fail, skip, via in cases:
            case = ET.SubElement(suite, "testcase", name=name, classname=label)
            # Keep the testcase identity stable; only enrich failed and advisory-skipped messages.
            details = ""
            if via and (fail is not None or (skip is not None and skip.startswith("advisory: "))):
                suffix = f" +{len(via) - 1}" if len(via) > 1 else ""
                details = f" (via {via[0]}{suffix})"
            if fail is not None:
                text = fail + details
                if details:
                    text += "\nDirect dependencies: " + ", ".join(via)
                ET.SubElement(case, "failure", message=fail + details).text = text
            elif skip is not None:
                node = ET.SubElement(case, "skipped", message=skip + details)
                if details:
                    node.text = "Direct dependencies: " + ", ".join(via)
        if warnings:
            ET.SubElement(suite, "system-out").text = "".join(f"wheelgap: warning: {w}\n" for w in warnings)
    # XML declaration so picky parsers don't have to guess the encoding.
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def write_junit(
    path: str,
    rows: list[Row],
    targets: list[Target],
    fail_on: str,
    advisory: frozenset[Target] = frozenset(),
    warnings: Sequence[str] = (),
) -> None:
    Path(path).write_text(junit_xml(rows, targets, fail_on, advisory, warnings), encoding="utf-8")
