"""Match the ignore patterns BuildKit applies to a local build context."""

from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# BuildKit's local source filters each path while walking its parents, using the bundled Moby matcher:
# https://github.com/moby/buildkit/blob/cedaeca8850a2bb536bbd1db72419f3cf491bdb5/vendor/github.com/tonistiigi/fsutil/filter.go#L254-L299
# https://github.com/moby/patternmatcher/blob/v0.6.1/patternmatcher.go
_GLOB_CHARS = frozenset("*?[]\\")
_PRUNE_CHARS = _GLOB_CHARS | {"^"}
# unicode.IsSpace as used by Go strings.TrimSpace.
_SPACE = (
    " \t\n\v\f\r\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)

# Docker's client matches on the machine that sends the context. On Windows a `\` in a pattern is a path separator,
# not an escape (filepath.Match has no escapes there), and filepath.Clean keeps volume names and UNC prefixes, so
# these patterns can't be read there by the rules below.
_WINDOWS_CLIENT = os.sep == "\\"


class IgnoreError(ValueError):
    """An ignore rule cannot be read with BuildKit's semantics."""


@dataclass(frozen=True)
class Rule:
    label: str
    pattern: str
    include: bool
    matches: Callable[[str], bool]

    @property
    def prefix(self) -> str:
        # fsutil allows a final /* or /** in exceptions while pruning directories.
        return self.pattern.removesuffix("/**").removesuffix("/*")


def read_rules(path: Path) -> tuple[Rule, ...]:
    """Read the selected ignore file and name the line when it cannot be interpreted."""
    try:
        lines = path.read_bytes().removeprefix(b"\xef\xbb\xbf").split(b"\n")
    except OSError as e:
        raise IgnoreError(f"{path.name}: cannot read ignore file: {e.strerror}") from None
    rules = []
    for line, raw in enumerate(lines, 1):
        label = f"{path.name}:{line}"
        try:
            if len(raw) >= 65536 or b"\x00" in raw:
                raise ValueError
            text = raw.decode("utf-8")
            if text.startswith("#"):
                continue
            pattern = text.strip(_SPACE)
            if not pattern:
                continue
            include = pattern.startswith("!")
            if include:
                pattern = pattern[1:].strip(_SPACE)
                if not pattern:
                    raise ValueError
            if _WINDOWS_CLIENT and ("\\" in pattern or ":" in pattern or pattern.startswith("//")):
                raise ValueError
            # Go filepath.Clean collapses all leading slashes, unlike posixpath.normpath.
            if pattern.startswith("/"):
                pattern = "/" + pattern.lstrip("/")
            pattern = posixpath.normpath(pattern)
            if pattern == "/":
                if include:
                    raise ValueError  # the matcher cleans "!/" to a bare !
                continue  # a rooted / cannot match a path relative to the context
            pattern = pattern.lstrip("/")
            # The matcher (patternmatcher.New) cleans an exception with its ! still on, so `!../../a` loses the ! and
            # becomes the rule `a`, which excludes.
            if include and posixpath.normpath("!" + pattern) != "!" + pattern:
                raise ValueError
            # The matcher trims and cleans each pattern once more (patternmatcher.New): whitespace that cleaning left
            # at an end (`./ a`) goes, and a pattern left starting with ! (`./!a`) becomes an exception.
            if pattern != pattern.strip(_SPACE) or (not include and pattern.startswith("!")):
                raise ValueError
            if pattern == ".":
                continue  # historical Docker exception
            rules.append(Rule(f"{label} ({text.strip(_SPACE)!r})", pattern, include, _compile(pattern)))
        except UnicodeDecodeError:
            raise IgnoreError(f"{label}: ignore file is not UTF-8") from None
        except (ValueError, re.error):
            raise IgnoreError(f"{label}: unsupported or invalid Docker ignore pattern") from None
    return tuple(rules)


def ignored(path: str, rules: tuple[Rule, ...]) -> str | None:
    """Return the rule that excludes a context-relative path, if any."""
    if not rules or path == ".":
        return None
    parents = path.split("/")
    matched_above = [False] * len(rules)
    prefixes = [rule.prefix + "/" for rule in rules if rule.include]
    can_prune = all(_PRUNE_CHARS.isdisjoint(prefix) for prefix in prefixes)
    for i in range(1, len(parents) + 1):
        name = "/".join(parents[:i])
        ignored_by = None
        for j, rule in enumerate(rules):
            matched = matched_above[j]
            if not matched and rule.include == (ignored_by is not None):
                matched = rule.matches(name)
                matched_above[j] = matched
            if matched:
                ignored_by = None if rule.include else rule.label
        if ignored_by and can_prune and not any(prefix.startswith(name + "/") for prefix in prefixes):
            return ignored_by
    return ignored_by


def pruned(path: str, rules: tuple[Rule, ...]) -> str | None:
    """Return the rule that excludes a folder when no exception rule can re-include anything under it."""
    if not (rule := ignored(path, rules)):
        return None
    prefixes = [r.prefix + "/" for r in rules if r.include]
    if all(_PRUNE_CHARS.isdisjoint(prefix) for prefix in prefixes) and not any(
        prefix.startswith(path + "/") for prefix in prefixes
    ):
        return rule
    return None


_Token = Literal["*", "**", "**/", "^"] | re.Pattern[str]


def _escaped_char(pattern: str, i: int) -> str:
    # Filepath permits an escaped rune, but the Moby compiler passes escapes to Go's regexp.
    # Reject letter and number escapes whose meanings differ between those grammars.
    if i >= len(pattern) or pattern[i].isalnum() or not pattern[i].isascii():
        raise ValueError
    return pattern[i]


def _character_class(pattern: str, i: int) -> tuple[re.Pattern[str], int]:
    negate = pattern.startswith("^", i)
    if negate:
        i += 1

    def character(index: int) -> tuple[str, int]:
        if index == len(pattern) or pattern[index] in "-][*?":
            raise ValueError
        if pattern[index] == "\\":
            return _escaped_char(pattern, index + 1), index + 2
        return pattern[index], index + 1

    parts = []
    while i < len(pattern) and pattern[i] != "]":
        first, i = character(i)
        if pattern.startswith("-", i):
            last, i = character(i + 1)
            parts.append(f"{re.escape(first)}-{re.escape(last)}")
        else:
            parts.append(re.escape(first))
    if not parts or i == len(pattern):
        raise ValueError
    return re.compile("[" + ("^" if negate else "") + "".join(parts) + "]"), i + 1


def _compile(pattern: str) -> Callable[[str], bool]:
    # The Moby matcher special-cases a pure prefix or suffix glob before using regex matching.
    if pattern.startswith("**") and _GLOB_CHARS.isdisjoint(pattern[2:]):
        suffix = pattern[2:]
        return lambda path: path.endswith(suffix) or (suffix.startswith("/") and path == suffix[1:])
    if pattern.endswith("**") and _GLOB_CHARS.isdisjoint(pattern[:-2]):
        return lambda path: path.startswith(pattern[:-2])
    if _GLOB_CHARS.isdisjoint(pattern):
        return pattern.__eq__

    tokens: list[_Token] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        i += 1
        if char == "*":
            if not pattern.startswith("*", i):
                tokens.append("*")
                continue
            i += 1
            if pattern.startswith("/", i):
                i += 1
            tokens.append("**" if i == len(pattern) else "**/")
        elif char == "?":
            tokens.append(re.compile("[^/]"))
        elif char == "[":
            parsed_class, i = _character_class(pattern, i)
            tokens.append(parsed_class)
        elif char == "\\":
            tokens.append(re.compile(re.escape(_escaped_char(pattern, i))))
            i += 1
        elif char == "^":
            tokens.append("^")  # Moby passes this through as a regex anchor
        else:
            tokens.append(re.compile(re.escape(char)))
    return lambda path: _match_tokens(path, tokens)


def _match_tokens(path: str, tokens: list[_Token]) -> bool:
    # Track reachable positions, so many adjacent wildcards cannot cause regex backtracking.
    positions = {0}
    for token in tokens:
        if isinstance(token, re.Pattern):
            positions = {i + 1 for i in positions if i < len(path) and token.fullmatch(path[i])}
        elif token == "^":
            positions.intersection_update({0})
        else:
            reachable = False
            for i, char in enumerate(path):
                reachable = reachable or i in positions
                if token == "*" and char == "/" or token != "*" and char == "\n":
                    reachable = False
                elif reachable and (token != "**/" or char == "/"):
                    positions.add(i + 1)
        if not positions:
            return False
    return len(path) in positions


def context_rules(context: Path, dockerfile: Path) -> tuple[Rule, ...]:
    """A Dockerfile-specific ignore file takes precedence over the root ignore file."""
    specific, root = dockerfile.with_name(f"{dockerfile.name}.dockerignore"), context / ".dockerignore"
    if not os.path.lexists(specific):
        return read_rules(root) if os.path.lexists(root) else ()
    rules = read_rules(specific)
    # BuildKit falls back to the root file when the specific file's content reaches it as nil rather than as empty
    # (`bc.dockerignore == nil`), which depends on how the frontend runs: the built-in one reads an empty file as
    # empty, a `# syntax=` one gets it over gRPC, where empty bytes arrive as nil.
    # https://github.com/moby/buildkit/blob/dd75b4955087a5a2d52188d6ce25b509b323280e/frontend/dockerui/config.go#L421-L429
    # https://github.com/moby/buildkit/blob/dd75b4955087a5a2d52188d6ce25b509b323280e/frontend/dockerui/config.go#L543-L592
    try:
        empty = not specific.read_bytes()
    except OSError as e:
        raise IgnoreError(f"{specific.name}: cannot read ignore file: {e.strerror}") from None
    if empty and os.path.lexists(root) and read_rules(root):
        raise IgnoreError(
            f"{specific.name} is empty, and whether Docker then reads .dockerignore depends on the Dockerfile "
            "frontend: put a comment in it, or delete it"
        )
    return rules
