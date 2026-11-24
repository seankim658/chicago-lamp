from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

ROOT_CONFTEST = Path(__file__).parents[1] / "conftest.py"

USES_FIXTURES = """
def test_reads_fixtures(cta_fixtures):
    assert (cta_fixtures / "sample.json").read_text() == "{}"
"""


@pytest.fixture
def suite(pytester: pytest.Pytester) -> pytest.Pytester:
    """A test suite with the repository's conftest and one test that needs fixtures."""
    pytester.makeconftest(ROOT_CONFTEST.read_text())
    pytester.makepyfile(USES_FIXTURES)
    return pytester


def test_missing_fixture_folder_skips_with_a_reason(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LAMP_FIXTURES_DIR", str(tmp_path / "absent"))

    result = suite.runpytest("-rs")

    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*no CTA fixtures, set LAMP_FIXTURES_DIR*"])


def test_configured_fixture_folder_is_used(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "sample.json").write_text("{}")
    monkeypatch.setenv("LAMP_FIXTURES_DIR", str(tmp_path))

    suite.runpytest().assert_outcomes(passed=1)


def test_empty_variable_falls_back_to_the_default_folder(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAMP_FIXTURES_DIR", "")
    default = suite.path / "fixtures"
    default.mkdir()
    (default / "sample.json").write_text("{}")

    suite.runpytest().assert_outcomes(passed=1)
