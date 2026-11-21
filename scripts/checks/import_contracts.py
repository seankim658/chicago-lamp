"""Write the import-linter config that enforces the workspace's architecture layers.

Layers are listed lowest first in the root pyproject.toml under
[tool.chicago-lamp] layers. A package belongs to the layer named by its folder,
packages/<layer>/<name>, and its import name is its project name with hyphens
replaced by underscores. A layer may import only the layers below it, and packages
in the same layer never import each other.
"""

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class ContractError(Exception):
    """The workspace layout breaks a layering rule. Holds every problem found."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Package:
    layer: str
    module: str


def build_contracts(root: Path) -> str:
    """Return the import-linter config for the workspace at root."""
    layers = read_layers(root)
    packages, problems = find_packages(root, layers)
    if not packages:
        problems.append("No packages found under packages/<layer>/<name>.")
    if problems:
        raise ContractError(problems)
    return render(layers, packages)


def read_layers(root: Path) -> list[str]:
    config = tomllib.loads((root / "pyproject.toml").read_text())
    layers = config.get("tool", {}).get("chicago-lamp", {}).get("layers")
    if not isinstance(layers, list) or not layers or not all(isinstance(x, str) for x in layers):
        raise ContractError(
            [f"[tool.chicago-lamp] layers must be a non-empty list of names, got {layers!r}."]
        )
    return layers


def find_packages(root: Path, layers: list[str]) -> tuple[list[Package], list[str]]:
    packages: list[Package] = []
    problems: list[str] = []
    for manifest in sorted((root / "packages").rglob("pyproject.toml")):
        relative = manifest.relative_to(root)
        if len(relative.parts) != 4:
            problems.append(f"{relative} is not at packages/<layer>/<name>/pyproject.toml.")
            continue
        package, problem = read_package(manifest, layers)
        if problem:
            problems.append(f"{relative}: {problem}")
        elif package:
            packages.append(package)
    return packages, problems


def read_package(manifest: Path, layers: list[str]) -> tuple[Package | None, str]:
    layer = manifest.parent.parent.name
    if layer not in layers:
        return None, f"layer {layer!r} is not one of {layers}."
    name = tomllib.loads(manifest.read_text()).get("project", {}).get("name")
    if not isinstance(name, str):
        return None, "has no [project] name."
    module = name.replace("-", "_")
    if not (manifest.parent / "src" / module).is_dir():
        return None, f"has no src/{module} folder for project {name!r}."
    return Package(layer, module), ""


def render(layers: list[str], packages: list[Package]) -> str:
    modules = sorted(package.module for package in packages)
    lines = ["[importlinter]", "root_packages =", *(f"    {m}" for m in modules), ""]
    lines += [
        "[importlinter:contract:layers]",
        "name = Layers import only lower layers, and packages in a layer never import each other",
        "type = layers",
        "layers =",
    ]
    for layer in reversed(layers):
        members = sorted(package.module for package in packages if package.layer == layer)
        if members:
            lines.append("    " + " | ".join(members))
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: import_contracts.py OUTPUT\n")
        return 2
    try:
        contracts = build_contracts(REPO_ROOT)
    except ContractError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    Path(argv[1]).write_text(contracts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
