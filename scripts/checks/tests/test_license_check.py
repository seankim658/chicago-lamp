from pathlib import Path

import pytest
from license_check import (
    Policy,
    Requirement,
    Review,
    check,
    load_policy,
    parse_requirements,
)

ALLOWED = frozenset({"MIT", "Apache-2.0", "BSD-3-Clause"})


def run(declared: dict[str, str], reviewed: dict[str, Review] | None = None) -> list[str]:
    requirements = [Requirement(name, "1.0") for name in declared]
    policy = Policy(ALLOWED, reviewed or {})
    return check(requirements, policy, lambda name: declared[name])


def test_allowed_licenses_pass() -> None:
    assert run({"a": "MIT", "b": "Apache-2.0", "c": "(MIT OR Apache-2.0) AND BSD-3-Clause"}) == []


def test_a_license_outside_the_list_is_refused() -> None:
    [problem] = run({"a": "GPL-3.0-only"})

    assert problem.startswith("a 1.0: metadata license 'GPL-3.0-only' is refused, GPL-3.0-only.")


def test_every_option_in_an_or_must_be_allowed() -> None:
    [problem] = run({"a": "MIT OR GPL-3.0-only"})

    assert "is refused, GPL-3.0-only." in problem


def test_a_with_exception_is_refused() -> None:
    [problem] = run({"a": "Apache-2.0 WITH LLVM-exception"})

    assert "a WITH exception" in problem


def test_a_license_that_is_not_spdx_asks_for_a_review() -> None:
    [problem] = run({"a": "Apache License 2.0"})

    assert "it is not an SPDX expression, so add a reviewed entry" in problem


def test_no_license_asks_for_a_review() -> None:
    [problem] = run({"a": ""})

    assert "no license is stated, so add a reviewed entry" in problem


@pytest.mark.parametrize(
    "expression", ["MIT AND", "OR MIT", "MIT MIT", "MIT AND OR MIT", "MIT/X11"]
)
def test_a_malformed_expression_asks_for_a_review(expression: str) -> None:
    [problem] = run({"a": expression})

    assert "it is not an SPDX expression" in problem


def test_a_reviewed_entry_replaces_the_metadata() -> None:
    assert run({"a": "Dual License"}, {"a": Review("1.0", "Apache-2.0 AND BSD-3-Clause")}) == []


def test_a_reviewed_entry_for_another_version_forces_a_new_review() -> None:
    [problem] = run({"a": "Dual License"}, {"a": Review("0.9", "MIT")})

    assert "reviewed at version 0.9 but 1.0 is pinned" in problem


def test_a_reviewed_entry_must_name_an_allowed_license() -> None:
    [problem] = run({"a": "Dual License"}, {"a": Review("1.0", "GPL-2.0-only")})

    assert "reviewed entry license 'GPL-2.0-only' is refused" in problem


def test_a_reviewed_entry_for_a_package_not_shipped_is_reported() -> None:
    [problem] = run({"a": "MIT"}, {"gone": Review("1.0", "MIT")})

    assert "gone: has a reviewed entry but is not a shipped dependency" in problem


def test_requirements_are_parsed_and_names_normalized() -> None:
    lines = ["Python_Dateutil==2.9.0.post0\n", "six==1.17.0 ; python_version >= '3.12'\n", "\n"]

    assert parse_requirements(lines) == [
        Requirement("python-dateutil", "2.9.0.post0"),
        Requirement("six", "1.17.0"),
    ]


def test_an_unpinned_requirement_is_refused() -> None:
    with pytest.raises(ValueError, match="not pinned as name==version"):
        parse_requirements(["boto3>=1.0\n"])


def test_policy_needs_an_allowed_list(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.chicago-lamp.licenses]\nallowed = []\n")

    with pytest.raises(ValueError, match="allowed must be a non-empty list"):
        load_policy(pyproject)


def test_policy_reviewed_entries_need_a_reason(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.chicago-lamp.licenses]\nallowed = ["MIT"]\n\n'
        '[[tool.chicago-lamp.licenses.reviewed]]\npackage = "a"\nversion = "1"\nlicense = "MIT"\n'
    )

    with pytest.raises(ValueError, match="lacks reason"):
        load_policy(pyproject)
