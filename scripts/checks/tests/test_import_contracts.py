from pathlib import Path

import pytest
from import_contracts import ContractError, build_contracts


def make_workspace(root: Path, layers: str = '["shared", "stages"]') -> Path:
    (root / "pyproject.toml").write_text(f"[tool.chicago-lamp]\nlayers = {layers}\n")
    return root


def add_package(root: Path, layer: str, name: str, *, with_source: bool = True) -> None:
    folder = root / "packages" / layer / name
    folder.mkdir(parents=True)
    (folder / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n')
    if with_source:
        (folder / "src" / name.replace("-", "_")).mkdir(parents=True)


def test_layers_are_listed_highest_first_with_peers_independent(tmp_path: Path) -> None:
    root = make_workspace(tmp_path)
    add_package(root, "shared", "lamp-storage")
    add_package(root, "shared", "lamp-schemas")
    add_package(root, "stages", "lamp-collector")

    contracts = build_contracts(root)

    assert "root_packages =\n    lamp_collector\n    lamp_schemas\n    lamp_storage\n" in contracts
    assert contracts.endswith("layers =\n    lamp_collector\n    lamp_schemas | lamp_storage\n")


def test_layer_with_no_packages_is_left_out(tmp_path: Path) -> None:
    root = make_workspace(tmp_path, '["shared", "stages"]')
    add_package(root, "shared", "lamp-storage")

    assert build_contracts(root).endswith("layers =\n    lamp_storage\n")


def test_every_problem_is_reported_together(tmp_path: Path) -> None:
    root = make_workspace(tmp_path)
    add_package(root, "unknown", "lamp-stray")
    add_package(root, "stages", "lamp-collector", with_source=False)
    (root / "packages" / "lamp-flat").mkdir()
    (root / "packages" / "lamp-flat" / "pyproject.toml").write_text("")

    with pytest.raises(ContractError) as raised:
        build_contracts(root)

    problems = "\n".join(raised.value.problems)
    assert "lamp-flat/pyproject.toml is not at packages/<layer>/<name>" in problems
    assert "layer 'unknown' is not one of ['shared', 'stages']" in problems
    assert "has no src/lamp_collector folder" in problems


def test_missing_layer_list_is_refused(tmp_path: Path) -> None:
    root = make_workspace(tmp_path, "[]")

    with pytest.raises(ContractError, match="layers must be a non-empty list"):
        build_contracts(root)


def test_workspace_without_packages_is_refused(tmp_path: Path) -> None:
    root = make_workspace(tmp_path)
    (root / "packages").mkdir()

    with pytest.raises(ContractError, match="No packages found"):
        build_contracts(root)
