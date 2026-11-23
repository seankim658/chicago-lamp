"""Check that every shipped dependency carries an allowed license.

Read pinned requirements (`name==version`, one per line) from standard input, as
`uv export` writes them, and each package's license from its installed metadata.
Allowed licenses are listed in the root pyproject.toml under
[tool.chicago-lamp.licenses] allowed. Every license in an expression must be
allowed, so `MIT OR GPL-3.0-only` is refused, and an exception added with `WITH`
is always refused. A package whose metadata names no SPDX expression needs a
reviewed entry for its exact pinned version, so an upgrade forces another review.
"""

import re
import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_OPERATORS = frozenset({"AND", "OR", "WITH"})
_LICENSE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")


@dataclass(frozen=True)
class Requirement:
    name: str
    version: str


@dataclass(frozen=True)
class Review:
    version: str
    license: str


@dataclass(frozen=True)
class Policy:
    allowed: frozenset[str]
    reviewed: dict[str, Review]


# Reads a package's declared license expression from its installed metadata.
LicenseReader = Callable[[str], str]


def check(
    requirements: list[Requirement], policy: Policy, read_license: LicenseReader
) -> list[str]:
    """Return one message per problem found, or an empty list when all is allowed."""
    problems: list[str] = []
    for requirement in requirements:
        problems += check_requirement(requirement, policy, read_license)
    pinned = {requirement.name for requirement in requirements}
    for name in sorted(set(policy.reviewed) - pinned):
        problems.append(
            f"{name}: has a reviewed entry but is not a shipped dependency, so remove it"
        )
    return problems


def check_requirement(
    requirement: Requirement, policy: Policy, read_license: LicenseReader
) -> list[str]:
    name = requirement.name
    review = policy.reviewed.get(name)
    if review and review.version != requirement.version:
        return [
            f"{name}: reviewed at version {review.version} but {requirement.version} is pinned, "
            "so review its license again"
        ]
    expression = review.license if review else read_license(name)
    source = "reviewed entry" if review else "metadata"
    refused = refused_licenses(expression, policy.allowed)
    if not refused:
        return []
    return [
        f"{name} {requirement.version}: {source} license {expression!r} is refused, "
        f"{'; '.join(refused)}. Allowed are {', '.join(sorted(policy.allowed))}"
    ]


def refused_licenses(expression: str, allowed: frozenset[str]) -> list[str]:
    """Return why an SPDX expression is refused, or an empty list when it is allowed."""
    words = expression.replace("(", " ").replace(")", " ").split()
    if not words:
        return ["no license is stated, so add a reviewed entry"]
    if not is_spdx_shaped(words):
        return ["it is not an SPDX expression, so add a reviewed entry"]
    refused: list[str] = []
    for position, word in enumerate(words):
        if position % 2 == 1:
            if word == "WITH":
                refused.append("a WITH exception")
        elif word not in allowed:
            refused.append(word)
    return refused


def is_spdx_shaped(words: list[str]) -> bool:
    """Tell whether words alternate license IDs and operators, starting and ending on an ID."""
    if len(words) % 2 == 0:
        return False
    for position, word in enumerate(words):
        is_operator = word in _OPERATORS
        if position % 2 == 1:
            if not is_operator:
                return False
        elif is_operator or not _LICENSE_ID.fullmatch(word):
            return False
    return True


def parse_requirements(lines: Iterable[str]) -> list[Requirement]:
    requirements: list[Requirement] = []
    for line in lines:
        text = line.split(";")[0].strip()
        if not text or text.startswith("#"):
            continue
        name, separator, version = text.partition("==")
        if not separator:
            raise ValueError(f"requirement {text!r} is not pinned as name==version")
        requirements.append(Requirement(normalize(name), version.strip()))
    return requirements


def load_policy(pyproject: Path) -> Policy:
    config = tomllib.loads(pyproject.read_text())
    section = config.get("tool", {}).get("chicago-lamp", {}).get("licenses", {})
    allowed = section.get("allowed")
    if not isinstance(allowed, list) or not allowed:
        raise ValueError("[tool.chicago-lamp.licenses] allowed must be a non-empty list")
    reviewed: dict[str, Review] = {}
    for entry in section.get("reviewed", []):
        missing = {"package", "version", "license", "reason"} - set(entry)
        if missing:
            raise ValueError(f"reviewed entry {entry!r} lacks {', '.join(sorted(missing))}")
        reviewed[normalize(entry["package"])] = Review(entry["version"], entry["license"])
    return Policy(frozenset(allowed), reviewed)


def installed_license(name: str) -> str:
    """Return the License-Expression field, or the older License field without it."""
    fields = metadata.metadata(name)
    return fields.get("License-Expression") or fields.get("License") or ""


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower().strip()


def main() -> int:
    try:
        policy = load_policy(REPO_ROOT / "pyproject.toml")
        requirements = parse_requirements(sys.stdin)
    except ValueError as error:
        sys.stderr.write(f"{error}\n")
        return 2
    problems = check(requirements, policy, installed_license)
    for problem in problems:
        sys.stdout.write(f"{problem}\n")
    if problems:
        return 1
    sys.stdout.write(f"{len(requirements)} shipped dependencies, all licenses allowed\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
