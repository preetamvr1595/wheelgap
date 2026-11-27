"""`wheelgap DIR`: which of a directory's files is read (sources.load_directory)."""

import json
import os
import shlex
import subprocess

import pytest
from test_mcp import call, init
from test_mcp import run as run_mcp

from wheelgap import sources
from wheelgap.api import check_lockfile, make_targets
from wheelgap.cli import main
from wheelgap.sources import SourceError, load_inputs

ARGS = ("-p", "3.13", "-t", "linux-x86_64")
PROJECT = '[project]\nname = "app"\ndependencies = ["purewheel==3.1", "nowheels==0.9"]\n'
NO_DEPENDENCIES = '[project]\nname = "app"\n'


def run(capsys, *argv):
    code = main([str(arg) for arg in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def write(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def command(path):
    quoted = subprocess.list2cmdline([str(path)]) if os.name == "nt" else shlex.quote(str(path))
    return f"wheelgap {quoted}"


def notes(err):
    return [line.removeprefix("wheelgap: note: ") for line in err.splitlines() if line.startswith("wheelgap: note: ")]


def names(out):
    return sorted(row["name"] for row in json.loads(out)["packages"])


# No lockfile at the root: pyproject.toml


def test_pyproject_is_checked_when_there_is_no_lockfile(capsys, fake_pypi, tmp_path):
    write(tmp_path, {"pyproject.toml": PROJECT})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    by_file = run(capsys, tmp_path / "pyproject.toml", *ARGS, "--json")
    report = json.loads(out)
    assert code == by_file[0] == 1
    assert report["packages"] == json.loads(by_file[1])["packages"]  # exactly as `wheelgap DIR/pyproject.toml`
    assert report["directory_inputs"] == [
        {
            "directory": str(tmp_path),
            "file": "pyproject.toml",
            "reason": "pyproject",
            "empty": [],
            "other_requirements": [],
        }
    ]
    assert "directory_inputs" not in json.loads(by_file[1])
    first, second = notes(err)
    assert first == (
        f"no lockfile and no file named requirements.txt in {tmp_path}, so {tmp_path / 'pyproject.toml'} was checked"
    )
    assert second.startswith("pyproject.toml lists direct dependencies only") and "add --deps for PyPI" in second
    assert notes(by_file[2]) == [second]


def test_note_naming_the_file_is_kept_with_quiet(capsys, fake_pypi, tmp_path):
    write(tmp_path, {"pyproject.toml": PROJECT})
    code, _, err = run(capsys, tmp_path, *ARGS, "--quiet")
    assert code == 1 and f"so {tmp_path / 'pyproject.toml'} was checked" in err


@pytest.mark.parametrize("name", ["requirements.txt", "uv.lock"])
def test_root_lockfile_wins_over_pyproject_without_a_note(capsys, fake_pypi, tmp_path, fixtures, name):
    text = "purewheel==3.1\n" if name == "requirements.txt" else (fixtures / "uvproj" / "uv.lock").read_text("utf-8")
    write(tmp_path, {"pyproject.toml": PROJECT, name: text, "backend/requirements.txt": "nowheels==0.9\n"})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    report = json.loads(out)
    assert report["sources"] == [name]
    assert report["directory_inputs"] == [
        {"directory": str(tmp_path), "file": name, "reason": "lockfile", "empty": [], "other_requirements": []}
    ]
    assert not any("was checked" in note for note in notes(err))


def test_pyproject_without_dependencies_is_the_answer_when_nothing_else_is_found(capsys, fake_pypi, tmp_path):
    write(tmp_path, {"pyproject.toml": NO_DEPENDENCIES})
    code, out, err = run(capsys, tmp_path, *ARGS)
    assert code == 0 and "No packages to check." in out
    assert f"so {tmp_path / 'pyproject.toml'} was checked" in err


def test_pyproject_with_only_extras_is_checked_so_extra_can_select(capsys, fake_pypi, tmp_path):
    project = NO_DEPENDENCIES + '[project.optional-dependencies]\nfast = ["purewheel==3.1"]\n'
    write(tmp_path, {"pyproject.toml": project, "backend/requirements.txt": "nowheels==0.9\n"})
    code, out, _ = run(capsys, tmp_path, *ARGS, "--json", "--extra", "fast")
    assert code == 0 and names(out) == ["purewheel"]


def test_pyproject_without_dependencies_gives_way_to_the_only_requirements_file(capsys, fake_pypi, tmp_path):
    write(tmp_path, {"pyproject.toml": NO_DEPENDENCIES, "requirements/base.txt": "nowheels==0.9\n"})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 1 and names(out) == ["nowheels"]
    assert json.loads(out)["directory_inputs"] == [
        {
            "directory": str(tmp_path),
            "file": "requirements/base.txt",
            "reason": "requirements",
            "empty": [{"file": "pyproject.toml", "why": "no dependencies"}],
            "other_requirements": [],
        }
    ]
    assert notes(err)[0] == (
        f"{tmp_path / 'pyproject.toml'} has no dependencies, so "
        f"{tmp_path / 'requirements' / 'base.txt'} was checked instead"
    )


@pytest.mark.parametrize("name", ["requirements-dev.txt", "docs/requirements.txt"])
def test_pyproject_without_dependencies_stays_the_answer_beside_dev_or_docs_inputs(capsys, fake_pypi, tmp_path, name):
    write(tmp_path, {"pyproject.toml": NO_DEPENDENCIES, name: "nowheels==0.9\n"})
    code, out, _ = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 0 and json.loads(out)["sources"] == ["pyproject.toml"] and not fake_pypi


# Requirements files under another name


@pytest.mark.parametrize("name", ["requirements-gpu.txt", "requirements/base.txt", "requirements.in"])
def test_the_only_requirements_file_under_another_name_is_checked(capsys, fake_pypi, tmp_path, name):
    # A pyproject.toml that can't be read (no [project] table) and a nested input don't change that.
    write(tmp_path, {name: "nowheels==0.9\n", "pyproject.toml": "[tool.x]\n", "api/requirements.txt": "fastlib\n"})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 1 and names(out) == ["nowheels"]
    assert json.loads(out)["directory_inputs"] == [
        {"directory": str(tmp_path), "file": name, "reason": "requirements", "empty": [], "other_requirements": []}
    ]
    assert notes(err)[0] == (
        f"no lockfile, requirements.txt or pyproject.toml dependencies in {tmp_path}, so its only requirements "
        f"file, {tmp_path / name}, was checked"
    )


@pytest.mark.parametrize("name", ["requirements-dev.txt", "requirements_docs.txt", "requirements/test-ci.txt"])
def test_the_only_requirements_file_is_offered_not_checked_when_its_name_says_dev_tests_or_docs(
    capsys, fake_pypi, tmp_path, name
):
    write(tmp_path, {name: "nowheels==0.9\n"})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 2 and not fake_pypi
    assert err.splitlines() == [
        f"wheelgap: error: no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}, "
        "and the only inputs it has are, by their names, for development, tests, docs or examples, which wheelgap "
        "doesn't check on its own. To check one:",
        f"  {command(tmp_path / name)}",
    ]
    assert json.loads(out)["error"]["candidates"] == [str(tmp_path / name)]


def test_several_requirements_files_are_listed_never_picked(capsys, fake_pypi, tmp_path):
    files = ["requirements-dev.txt", "requirements_test.txt", "requirements/base.txt", "requirements/prod.txt"]
    write(tmp_path, dict.fromkeys(files, "nowheels==0.9\n"))
    # Not candidates: a constraints file, a .in beside its compiled .txt, and other names.
    write(tmp_path, dict.fromkeys(["requirements/constraints.txt", "requirements/base.in", "notes.txt"], ""))
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 2 and not fake_pypi
    assert err.splitlines() == [
        f"wheelgap: error: no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}, "
        "and more than one requirements file to choose from. Check one:",
        *(f"  {command(tmp_path / name)}" for name in files),
    ]
    error = json.loads(out)["error"]
    assert error["type"] == "DirectoryChoiceError"
    assert error["candidates"] == [str(tmp_path / name) for name in files]
    # Each line is a command that works.
    code, out, _ = run(capsys, tmp_path / files[2], *ARGS, "--json")
    assert code == 1 and names(out) == ["nowheels"]


def test_pyproject_dependencies_win_over_other_requirements_names(capsys, fake_pypi, tmp_path):
    write(tmp_path, {"pyproject.toml": PROJECT, "requirements-dev.txt": "fastlib\n", "requirements/a.txt": ""})
    code, out, _ = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 1 and names(out) == ["nowheels", "purewheel"]


# Requirements files beside the pyproject.toml that was read: named in the note, never read in its place

UV_COMPILED = (
    "# This file was autogenerated by uv via the following command:\n"
    "#    uv pip compile --generate-hashes -o requirements-lock.txt pyproject.toml\n"
    "fastlib==2.0.0 \\\n    --hash=sha256:aa\n    # via app (pyproject.toml)\n"
)


def beside(tmp_path, err):
    """The notes after the first, which say what else is at the root."""
    first, *rest = notes(err)
    assert first == (
        f"no lockfile and no file named requirements.txt in {tmp_path}, so {tmp_path / 'pyproject.toml'} was checked"
    )
    return [line for line in rest if not line.startswith("pyproject.toml lists direct dependencies only")]


def test_compiled_requirements_files_beside_pyproject_are_named_not_read(capsys, fake_pypi, tmp_path):
    # HKUDS/Vibe-Trading: uv pip compile output at the root, with hashes.
    write(tmp_path, {"pyproject.toml": PROJECT, "requirements-lock.txt": UV_COMPILED})
    write(tmp_path, {"requirements-channels-lock.txt": UV_COMPILED.replace("fastlib==2.0.0", "purewheel==3.1")})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 1 and names(out) == ["nowheels", "purewheel"]  # pyproject.toml's, as before
    assert json.loads(out)["directory_inputs"][0]["other_requirements"] == [
        {"file": "requirements-channels-lock.txt", "compiled": True},
        {"file": "requirements-lock.txt", "compiled": True},
    ]
    assert beside(tmp_path, err) == [
        f"{name} looks compiled and pinned: `{command(tmp_path / name)}` checks what it pins"
        for name in ("requirements-channels-lock.txt", "requirements-lock.txt")
    ]
    # Each command answers for its file.
    code, out, _ = run(capsys, tmp_path / "requirements-lock.txt", *ARGS, "--json")
    assert code == 0 and names(out) == ["fastlib"]


def test_requirements_folder_beside_pyproject_compiled_files_first(capsys, fake_pypi, tmp_path):
    # apache/superset: requirements/*.txt compiled by uv from the .in beside each (the .in files aren't named).
    files = {"base": UV_COMPILED, "development": UV_COMPILED, "translations": UV_COMPILED, "extra": "fastlib>=2\n"}
    write(tmp_path, {"pyproject.toml": PROJECT, **{f"requirements/{k}.txt": v for k, v in files.items()}})
    write(tmp_path, {"requirements/base.in": "fastlib\n", "requirements/development.in": "fastlib\n"})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 1 and names(out) == ["nowheels", "purewheel"]
    assert [e["file"] for e in json.loads(out)["directory_inputs"][0]["other_requirements"]] == [
        "requirements/base.txt",
        "requirements/development.txt",
        "requirements/translations.txt",
        "requirements/extra.txt",
    ]
    assert beside(tmp_path, err) == [
        *(
            f"requirements/{name}.txt looks compiled and pinned: "
            f"`{command(tmp_path / 'requirements' / f'{name}.txt')}` checks what it pins"
            for name in ("base", "development", "translations")
        ),
        "other requirements files there: 1 more",
    ]


def test_build_tool_pins_beside_pyproject_stay_a_hint(capsys, fake_pypi, tmp_path):
    # pallets/flask 3.0.0: requirements/build.txt is pip-compile-multi output that pins the build tools. It looks
    # compiled and pinned, and the project's dependencies are still pyproject.toml's.
    build = (
        "# SHA1:80754af91bfb6d1073585b046fe0a474ce868509\n#\n"
        "# This file is autogenerated by pip-compile-multi\n# To update, run:\n#\n#    pip-compile-multi\n#\n"
        "fastlib==2.0.0\n    # via -r requirements/build.in\n"
    )
    write(tmp_path, {"pyproject.toml": PROJECT, "requirements/build.txt": build, "requirements/build.in": "fastlib\n"})
    write(tmp_path, {"requirements/docs.in": "fastlib\n", "requirements/tests.in": "fastlib\n"})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 1 and names(out) == ["nowheels", "purewheel"]
    assert json.loads(out)["directory_inputs"][0]["file"] == "pyproject.toml"
    assert beside(tmp_path, err) == [
        f"requirements/build.txt looks compiled and pinned: `{command(tmp_path / 'requirements' / 'build.txt')}` "
        "checks what it pins",
        f"other requirements files there: `{command(tmp_path / 'requirements' / 'docs.in')}` and "
        f"`{command(tmp_path / 'requirements' / 'tests.in')}`",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "fastlib>=2\n",  # not pinned
        "torch==2.13.0+xpu\n",  # pinned by hand, with no compiler's mark (LlamaFactory's requirements/xpu.txt)
        UV_COMPILED + "purewheel\n",  # marked, but a line isn't pinned
        UV_COMPILED + "-r missing.txt\n",  # can't be read in full
        '{"not": "requirements"}\n',
    ],
)
def test_requirements_files_that_dont_look_compiled_are_named_as_commands(capsys, fake_pypi, tmp_path, text):
    write(tmp_path, {"pyproject.toml": PROJECT, "requirements-foo.lock": text})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 1 and names(out) == ["nowheels", "purewheel"]
    assert json.loads(out)["directory_inputs"][0]["other_requirements"] == [
        {"file": "requirements-foo.lock", "compiled": False}
    ]
    assert beside(tmp_path, err) == [f"other requirements files there: `{command(tmp_path / 'requirements-foo.lock')}`"]


def test_the_hint_reads_only_the_file_itself(capsys, fake_pypi, tmp_path, monkeypatch):
    # An include may name any file (a FIFO, /dev/zero, a huge one), and an oversized file isn't opened: for a hint,
    # neither looks compiled, so wheelgap DIR never blocks or fills memory on what it doesn't read.
    write(tmp_path, {"pyproject.toml": PROJECT, "requirements/base.txt": UV_COMPILED})
    write(tmp_path, {"requirements-lock.txt": "-r requirements/base.txt\n", "requirements-big.txt": UV_COMPILED})
    monkeypatch.setattr(sources, "_HINT_MAX_BYTES", len(UV_COMPILED) - 1)
    code, out, _ = run(capsys, tmp_path, *ARGS, "--json")
    assert json.loads(out)["directory_inputs"][0]["other_requirements"] == [
        {"file": "requirements-big.txt", "compiled": False},
        {"file": "requirements-lock.txt", "compiled": False},
        {"file": "requirements/base.txt", "compiled": False},
    ]


def test_pyproject_with_no_dependencies_isnt_said_to_have_them(capsys, fake_pypi, tmp_path):
    write(tmp_path, {"pyproject.toml": '[project]\nname = "app"\n[project.optional-dependencies]\nx = ["fastlib"]\n'})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert notes(err)[0] == (
        f"no lockfile and no file named requirements.txt in {tmp_path}, so {tmp_path / 'pyproject.toml'} was checked"
    )


def test_many_requirements_files_beside_pyproject_are_counted(capsys, fake_pypi, tmp_path):
    names_ = ["requirements-a.txt", "requirements-b.txt", "requirements-c.txt", "requirements-d.txt"]
    write(tmp_path, {"pyproject.toml": PROJECT, **dict.fromkeys(names_, "fastlib\n")})
    code, _, err = run(capsys, tmp_path, *ARGS)
    assert beside(tmp_path, err) == [
        "other requirements files there: "
        + ", ".join(f"`{command(tmp_path / name)}`" for name in names_[:3])
        + " and 1 more"
    ]


def test_a_requirements_lock_under_another_name_is_never_read_by_directory_mode(capsys, fake_pypi, tmp_path):
    # Directory mode reads only the names it always has: a junk requirements-foo.lock changes nothing.
    write(tmp_path, {"requirements-foo.lock": '{"not": "requirements"}\n'})
    code, out, err = run(capsys, tmp_path, *ARGS)
    assert code == 2 and "no lockfile found" in err and "requirements-foo.lock" not in err
    write(tmp_path, {"backend/requirements.txt": "nowheels==0.9\n", "requirements-foo.in": "fastlib\n"})
    code, out, _ = run(capsys, tmp_path, *ARGS, "--json")
    assert json.loads(out)["directory_inputs"][0]["file"] == "requirements-foo.in"


# Inputs in subfolders


@pytest.mark.parametrize("name", ["backend/requirements.txt", "services/api/uv.lock", "a/b/c/pyproject.toml"])
def test_the_only_input_in_a_subfolder_is_checked(capsys, fake_pypi, tmp_path, fixtures, name):
    text = {"requirements.txt": "nowheels==0.9\n", "pyproject.toml": PROJECT}.get(name.rpartition("/")[2])
    write(tmp_path, {name: text or (fixtures / "uvproj" / "uv.lock").read_text(encoding="utf-8")})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    by_file = run(capsys, tmp_path / name, *ARGS, "--json")
    assert code == by_file[0] == 1
    assert json.loads(out)["packages"] == json.loads(by_file[1])["packages"]
    assert json.loads(out)["directory_inputs"] == [
        {"directory": str(tmp_path), "file": name, "reason": "subfolder", "empty": [], "other_requirements": []}
    ]
    assert notes(err)[0] == (
        f"no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}, so the only "
        f"input in its subfolders, {tmp_path / name}, was checked"
    )


def test_a_subfolder_input_is_named_by_its_path_in_the_directory(capsys, fake_pypi, tmp_path):
    # stderr is lost in a job summary, and a bare requirements.txt would read as the root's.
    write(tmp_path, {"scripts/tools/requirements.txt": "nowheels==0.9\npurewheel\n"})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 1 and json.loads(out)["sources"] == ["scripts/tools/requirements.txt"]
    assert "scripts/tools/requirements.txt has 1 unpinned package" in err
    _, out, _ = run(capsys, tmp_path, *ARGS, "--color", "never")
    assert " · scripts/tools/requirements.txt · 2 packages · " in out.splitlines()[0]
    _, out, _ = run(capsys, tmp_path, *ARGS, "--markdown")
    assert "scripts/tools/requirements.txt" in out
    # Named as a file, it is labelled as it always was.
    _, out, _ = run(capsys, tmp_path / "scripts" / "tools" / "requirements.txt", *ARGS, "--json")
    assert json.loads(out)["sources"] == ["requirements.txt"]


@pytest.mark.parametrize(
    ("rival", "listed"),
    [
        ("docker/main/requirements-wheels.txt", True),  # beside the subfolder's requirements.txt
        ("docker/main/requirements/gpu.txt", True),
        ("requirements-dev.txt", True),  # at the root: dev by its name, so not read on its own, but a rival
        ("docker/main/requirements-dev.txt", False),  # not the project's by its name
        ("docker/main/constraints.txt", False),
        ("docker/other/requirements-wheels.txt", False),  # another folder's
    ],
)
def test_a_subfolder_requirements_file_with_a_rival_is_listed_not_picked(capsys, fake_pypi, tmp_path, rival, listed):
    write(tmp_path, {"docker/main/requirements.txt": "purewheel==3.1\n", rival: "nowheels==0.9\n"})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    if not listed:
        assert code == 0 and json.loads(out)["sources"] == ["docker/main/requirements.txt"]
        return
    assert code == 2 and not fake_pypi
    assert err.splitlines() == [
        f"wheelgap: error: no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}, "
        "and more than one file that could hold the project's dependencies. Check one:",
        f"  {command(tmp_path / 'docker' / 'main' / 'requirements.txt')}",
        f"  {command(tmp_path / rival)}",
    ]
    assert json.loads(out)["error"]["candidates"] == [
        str(tmp_path / "docker" / "main" / "requirements.txt"),
        str(tmp_path / rival),
    ]


def test_a_subfolder_is_read_as_wheelgap_reads_that_folder(capsys, fake_pypi, tmp_path):
    # backend/requirements.txt has nothing to check, so `wheelgap backend` goes on to backend/pyproject.toml.
    write(tmp_path, {"backend/requirements.txt": "-e .\n", "backend/pyproject.toml": PROJECT})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    by_folder = run(capsys, tmp_path / "backend", *ARGS, "--json")
    report = json.loads(out)
    assert code == by_folder[0] == 1 and report["packages"] == json.loads(by_folder[1])["packages"]
    assert report["sources"] == ["backend/pyproject.toml"]
    assert report["directory_inputs"] == [
        {
            "directory": str(tmp_path),
            "file": "backend/pyproject.toml",
            "reason": "subfolder",
            "empty": [{"file": "backend/requirements.txt", "why": "only skipped entries (editable)"}],
            "other_requirements": [],
        }
    ]
    assert notes(err)[:2] == [
        f"no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}, so the only "
        f"input in its subfolders, {tmp_path / 'backend'}, was checked",
        f"{tmp_path / 'backend' / 'requirements.txt'} has only skipped entries (editable), so "
        f"{tmp_path / 'backend' / 'pyproject.toml'} was checked instead",
    ]


def test_a_subfolder_that_cant_be_read_means_nothing_is_picked(capsys, fake_pypi, tmp_path):
    write(tmp_path, {"backend/requirements.txt": "nowheels==0.9\n", "private/requirements.txt": "fastlib\n"})
    private = tmp_path / "private"
    private.chmod(0)
    try:
        if os.access(private, os.R_OK):
            pytest.skip("this user (or this OS) reads a folder whatever its mode")
        code, _, err = run(capsys, tmp_path, *ARGS)
    finally:
        private.chmod(0o755)
    assert code == 2 and not fake_pypi
    assert err.splitlines() == [
        f"wheelgap: error: no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}, "
        f"but inputs in its subfolders ({private} couldn't be read). Check one:",
        f"  {command(tmp_path / 'backend' / 'requirements.txt')}",
    ]


@pytest.mark.parametrize("name", ["requirements-gpu.txt", "backend/requirements.txt"])
def test_a_picked_file_that_isnt_utf8_is_named_in_the_error(capsys, fake_pypi, tmp_path, name):
    (tmp_path / name).parent.mkdir(exist_ok=True)
    (tmp_path / name).write_bytes("purewheel==3.1  # caf\u00e9\n".encode("latin-1"))
    code, _, err = run(capsys, tmp_path, *ARGS)
    assert code == 2
    assert err.startswith(f"wheelgap: error: {tmp_path / name}: not UTF-8 text (")


@pytest.mark.parametrize("name", ["README.txt", "readme-first.txt", "LICENSE.txt", "constraints-3.13.txt", "NOTES.in"])
def test_text_files_in_requirements_that_arent_requirements_files(capsys, fake_pypi, tmp_path, name):
    write(tmp_path, {"requirements/base.txt": "nowheels==0.9\n", f"requirements/{name}": "How to install\n"})
    code, out, _ = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 1 and json.loads(out)["directory_inputs"][0]["file"] == "requirements/base.txt"


def test_several_subfolder_inputs_are_listed_one_per_folder(capsys, fake_pypi, tmp_path, fixtures):
    write(
        tmp_path,
        {
            "web/requirements.txt": "nowheels==0.9\n",
            "api/uv.lock": (fixtures / "uvproj" / "uv.lock").read_text(encoding="utf-8"),
            "api/requirements.txt": "fastlib\n",  # api/uv.lock comes first, as in `wheelgap api`
            "api/pyproject.toml": PROJECT,
            "libs/core/pyproject.toml": PROJECT,
            "libs/tooling/pyproject.toml": "[tool.x]\n",  # nothing `wheelgap libs/tooling` could check
            "docs/requirements.txt": "fastlib\n",  # left out while the project has inputs of its own
        },
    )
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    found = ["api/uv.lock", "web/requirements.txt", "libs/core/pyproject.toml"]  # nearest first, then by name
    assert code == 2 and not fake_pypi
    assert err.splitlines() == [
        f"wheelgap: error: no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}, "
        "but inputs in its subfolders. Check one:",
        *(f"  {command(tmp_path / name)}" for name in found),
    ]
    assert json.loads(out)["error"]["candidates"] == [str(tmp_path / name) for name in found]


def test_a_long_list_of_candidates_is_cut_and_counted(capsys, fake_pypi, tmp_path):
    write(tmp_path, {f"svc{n:02}/requirements.txt": "fastlib\n" for n in range(13)})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    lines = err.splitlines()
    assert code == 2 and len(lines) == 12
    assert lines[10] == f"  {command(tmp_path / 'svc09' / 'requirements.txt')}" and lines[11] == "  (and 3 more)"
    assert len(json.loads(out)["error"]["candidates"]) == 13


@pytest.mark.parametrize("folder", ["tests", "docs", "examples/basic", "src/Tests", "common/e2e_tests", "dev"])
def test_an_input_in_a_tests_docs_or_examples_folder_is_offered_not_checked(capsys, fake_pypi, tmp_path, folder):
    write(tmp_path, {f"{folder}/requirements.txt": "nowheels==0.9\n"})
    code, _, err = run(capsys, tmp_path, *ARGS)
    assert code == 2 and not fake_pypi
    assert err.splitlines() == [
        f"wheelgap: error: no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}, "
        "and the only inputs it has are, by their names, for development, tests, docs or examples, which wheelgap "
        "doesn't check on its own. To check one:",
        f"  {command(tmp_path / folder / 'requirements.txt')}",
    ]
    # With an input of the project's own, those folders don't make it "one of several".
    write(tmp_path, {"backend/requirements.txt": "purewheel==3.1\n"})
    code, out, _ = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 0 and names(out) == ["purewheel"]


@pytest.mark.parametrize(
    "root",
    [
        {"setup.py": ""},
        {"setup.cfg": ""},
        {"pyproject.toml": '[project]\nname = "app"\ndynamic = ["dependencies"]\n'},
        {"pyproject.toml": '[build-system]\nrequires = ["setuptools"]\n'},
        {"pyproject.toml": '[tool.poetry.dependencies]\npython = "^3.11"\nfastlib = "*"\n'},
    ],
)
def test_one_subfolder_input_is_not_taken_for_a_project_whose_own_dependencies_cant_be_read(
    capsys, fake_pypi, tmp_path, root
):
    write(tmp_path, {"tools/requirements.txt": "nowheels==0.9\n", **root})
    code, _, err = run(capsys, tmp_path, *ARGS)
    assert code == 2 and not fake_pypi
    assert err.splitlines() == [
        f"wheelgap: error: no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}. "
        f"Its own dependencies are in {next(iter(root))}, which wheelgap can't read, so the one input in its "
        "subfolders isn't taken for the project's. To check that one:",
        f"  {command(tmp_path / 'tools' / 'requirements.txt')}",
    ]


def test_one_subfolder_input_is_not_taken_for_a_project_that_lists_no_dependencies(capsys, fake_pypi, tmp_path):
    write(tmp_path, {"tools/requirements.txt": "nowheels==0.9\n", "pyproject.toml": NO_DEPENDENCIES})
    code, _, err = run(capsys, tmp_path, *ARGS)
    assert code == 2 and not fake_pypi
    assert err.splitlines() == [
        f"wheelgap: error: no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}. "
        "Its pyproject.toml lists no dependencies, so the one input in its subfolders isn't taken for the "
        "project's. To check that one:",
        f"  {command(tmp_path / 'tools' / 'requirements.txt')}",
    ]


def test_folders_that_are_never_searched(capsys, fake_pypi, tmp_path):
    write(
        tmp_path,
        dict.fromkeys(
            [
                ".tox/requirements.txt",
                "node_modules/pkg/requirements.txt",
                "env/pyvenv.cfg",
                "env/requirements.txt",
                "env/lib/requirements.txt",
                "lib/site-packages/pkg/requirements.txt",
                "a/b/c/d/requirements.txt",  # deeper than NESTED_DEPTH
            ],
            "nowheels==0.9\n",
        ),
    )
    code, _, err = run(capsys, tmp_path, *ARGS)
    assert code == 2 and err.startswith(f"wheelgap: error: no lockfile found in {tmp_path} (looked for")
    assert "3 folders down" in err
    # So none of them makes the one real input "one of several".
    write(tmp_path, {"svc/requirements.txt": "purewheel==3.1\n"})
    code, out, _ = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 0 and json.loads(out)["directory_inputs"][0]["file"] == "svc/requirements.txt"


def test_symlinks_are_not_followed(capsys, fake_pypi, tmp_path):
    write(tmp_path, {"proj/real/requirements.txt": "nowheels==0.9\n", "elsewhere/requirements.txt": "fastlib\n"})
    try:
        (tmp_path / "proj" / "linked").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
        (tmp_path / "proj" / "loop").symlink_to(tmp_path / "proj", target_is_directory=True)
        (tmp_path / "proj" / "other").mkdir()
        (tmp_path / "proj" / "other" / "requirements.txt").symlink_to(tmp_path / "elsewhere" / "requirements.txt")
    except OSError:
        pytest.skip("symlinks need a privilege on this Windows")
    code, out, _ = run(capsys, tmp_path / "proj", *ARGS, "--json")
    assert code == 1 and json.loads(out)["directory_inputs"][0]["file"] == "real/requirements.txt"


def test_a_search_that_is_cut_short_never_picks(capsys, fake_pypi, tmp_path, monkeypatch):
    write(tmp_path, {"a/requirements.txt": "nowheels==0.9\n", "b/x.txt": "", "c/x.txt": ""})
    monkeypatch.setattr(sources, "NESTED_FOLDERS", 3)  # the root, a and b: c is never opened
    code, _, err = run(capsys, tmp_path, *ARGS)
    assert code == 2 and not fake_pypi
    assert err.splitlines() == [
        f"wheelgap: error: no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}, "
        "but inputs in its subfolders (the search stopped after 3 folders). Check one:",
        f"  {command(tmp_path / 'a' / 'requirements.txt')}",
    ]
    monkeypatch.setattr(sources, "NESTED_FOLDERS", 1)
    code, _, err = run(capsys, tmp_path, *ARGS)
    assert code == 2 and err.startswith(
        f"wheelgap: error: no lockfile found in {tmp_path} (the search stopped after 1 folders) (looked for"
    )


# A file with nothing to check


def test_requirements_txt_that_only_installs_the_project_gives_way_to_pyproject(capsys, fake_pypi, tmp_path):
    write(tmp_path, {"requirements.txt": ".\n", "pyproject.toml": PROJECT})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    report = json.loads(out)
    assert code == 1 and names(out) == ["nowheels", "purewheel"] and report["skipped"] == []
    assert report["directory_inputs"] == [
        {
            "directory": str(tmp_path),
            "file": "pyproject.toml",
            "reason": "pyproject",
            "empty": [{"file": "requirements.txt", "why": "only skipped entries (path)"}],
            "other_requirements": [],
        }
    ]
    assert notes(err)[0] == (
        f"{tmp_path / 'requirements.txt'} has only skipped entries (path), so {tmp_path / 'pyproject.toml'} "
        "was checked instead"
    )


def test_lockfile_with_nothing_to_check_gives_way_to_the_next_lockfile(capsys, fake_pypi, tmp_path):
    write(
        tmp_path,
        {
            "uv.lock": 'version = 1\nrequires-python = ">=3.11"\n',
            "requirements.txt": "",
            "requirements.lock": "nowheels==0.9\n",
            "pyproject.toml": PROJECT,
        },
    )
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 1 and names(out) == ["nowheels"]
    assert json.loads(out)["directory_inputs"][0] == {
        "directory": str(tmp_path),
        "file": "requirements.lock",
        "reason": "lockfile",
        "empty": [{"file": "uv.lock", "why": "no packages"}, {"file": "requirements.txt", "why": "no packages"}],
        "other_requirements": [],
    }
    assert notes(err)[0] == (
        f"{tmp_path / 'uv.lock'} has no packages; {tmp_path / 'requirements.txt'} has no packages, so "
        f"{tmp_path / 'requirements.lock'} was checked instead"
    )


def test_conda_only_pixi_lock_gives_way_to_pyproject(capsys, fake_pypi, tmp_path):
    lock = (
        "version: 6\nenvironments:\n  default:\n    channels:\n    - url: https://conda.anaconda.org/conda-forge/\n"
        "    packages:\n      linux-64:\n      - conda: https://conda.anaconda.org/conda-forge/linux-64/x-1-0.conda\n"
        "packages:\n- conda: https://conda.anaconda.org/conda-forge/linux-64/x-1-0.conda\n"
    )
    write(tmp_path, {"pixi.lock": lock, "pyproject.toml": PROJECT})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 1 and names(out) == ["nowheels", "purewheel"]
    assert notes(err)[0] == (
        f"{tmp_path / 'pixi.lock'} has only skipped entries (conda) in its default environment, so "
        f"{tmp_path / 'pyproject.toml'} was checked instead"
    )
    # --environment still means the pixi.lock, whatever it holds.
    code, out, _ = run(capsys, tmp_path, *ARGS, "--json", "--environment", "default")
    assert code == 0 and json.loads(out)["sources"] == ["pixi.lock"]


@pytest.mark.parametrize(
    "others",
    [
        {},
        {"pyproject.toml": "[tool.ruff]\nline-length = 120\n"},  # no [project] table
        {"pyproject.toml": NO_DEPENDENCIES},
        {"pyproject.toml": '[dependency-groups]\ndev = ["fastlib"]\n'},  # needs --group
        {"pyproject.toml": '[project]\nname = "app"\ndynamic = ["dependencies"]\n'},  # can't be read
        {"backend/requirements.txt": "nowheels==0.9\n", "requirements-dev.txt": "fastlib\n"},  # not root inputs
    ],
)
def test_file_with_nothing_to_check_and_nothing_better_is_read_as_before(capsys, fake_pypi, tmp_path, others):
    write(tmp_path, {"requirements.txt": ".\n", **others})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    report = json.loads(out)
    assert code == 0 and report["sources"] == ["requirements.txt"] and report["packages"] == []
    assert report["skipped"] == [{"name": ".", "reason": "path"}]
    assert report["directory_inputs"][0]["reason"] == "lockfile" and notes(err) == []


# Nothing found


def test_nothing_found_says_what_was_looked_for(capsys, tmp_path):
    write(tmp_path, {"setup.py": "", "README.md": ""})
    code, out, err = run(capsys, tmp_path, *ARGS, "--json")
    assert code == 2 and json.loads(out)["error"]["type"] == "SourceError"
    assert err == (
        f"wheelgap: error: no lockfile found in {tmp_path} (looked for uv.lock, pylock.toml, poetry.lock, pdm.lock, "
        "Pipfile.lock, requirements.txt, requirements.lock, pixi.lock, a pyproject.toml with [project] dependencies, "
        "requirements*.txt and requirements/*.txt there, and the lockfile names and pyproject.toml up to 3 folders "
        "down). Pass a file, or package names like `wheelgap numpy psycopg2`.\n"
    )


def test_nothing_found_says_why_pyproject_was_not_checked(capsys, tmp_path):
    write(tmp_path, {"pyproject.toml": '[project]\nname = "app"\ndynamic = ["dependencies"]\n'})
    code, _, err = run(capsys, tmp_path, *ARGS)
    assert code == 2 and "no lockfile found" in err
    assert (
        f"Its pyproject.toml can't be checked ({tmp_path / 'pyproject.toml'}: [project] dependencies is dynamic" in err
    )
    assert "run `wheelgap" not in err  # the command would only repeat that error


# The other ways in


def test_dockerfile_with_a_directory_uses_the_same_search(capsys, fake_pypi, tmp_path):
    write(tmp_path, {"Dockerfile": "FROM python:3.13-slim\n", "pyproject.toml": PROJECT})
    code, out, err = run(capsys, "--dockerfile", tmp_path / "Dockerfile", tmp_path, "--json")
    assert code == 1 and json.loads(out)["directory_inputs"][0]["file"] == "pyproject.toml"


def test_mcp_check_lockfile_uses_the_same_search(fake_pypi, tmp_path):
    write(tmp_path, {"backend/requirements.txt": "nowheels==0.9\n"})
    args = {"path": str(tmp_path), "python": "3.13", "targets": ["linux-x86_64"]}
    (_, reply) = run_mcp(init(), call("check_lockfile", **args))
    data = reply["result"]["structuredContent"]
    assert [gap["name"] for gap in data["gaps"]] == ["nowheels"]
    assert f"{tmp_path / 'backend' / 'requirements.txt'}, was checked" in data["notes"][0]
    write(tmp_path, {"api/requirements.txt": "fastlib\n"})
    (_, reply) = run_mcp(init(), call("check_lockfile", **args))
    assert reply["result"]["isError"] is True
    assert reply["result"]["content"][0]["text"].splitlines() == [
        f"no lockfile, requirements.txt or pyproject.toml dependencies at the root of {tmp_path}, but inputs in its "
        "subfolders. Check one:",
        f"  {command(tmp_path / 'api' / 'requirements.txt')}",
        f"  {command(tmp_path / 'backend' / 'requirements.txt')}",
    ]


def test_python_api_and_load_inputs(fake_pypi, tmp_path):
    write(tmp_path, {"backend/requirements.txt": "nowheels==0.9\n"})
    (source,) = load_inputs([str(tmp_path)])
    assert source.found is not None and source.found.path == tmp_path / "backend" / "requirements.txt"
    # check_lockfile() has no notes to say which file it read, so it reads a directory's lockfile by name only.
    with pytest.raises(SourceError, match="no lockfile found"):
        check_lockfile(tmp_path, make_targets(["3.13"], ["linux-x86_64"]))
