from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from scripts.verify_distribution import build_release_candidate_report


def _write_release_sources(root: Path, version: str) -> None:
    (root / "src/agent_runtime").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "src/agent_runtime/version.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "agent-runtime"\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "README.md").write_text(
        f"当前版本是 **v{version} Release Candidate**\n", encoding="utf-8"
    )
    (root / "docs/CURRENT.md").write_text(
        f"- **当前版本**：`{version}`\n", encoding="utf-8"
    )
    (root / "docs/ROADMAP.md").write_text(
        f"- **当前版本**：v{version}\n", encoding="utf-8"
    )
    (root / "src/agent_runtime/lab").mkdir()
    (root / "src/agent_runtime/lab/static").mkdir()
    (root / "src/agent_runtime/lab/static/index.html").write_text(
        f"Learning Console · v{version}\n", encoding="utf-8"
    )


def _write_wheel(path: Path, version: str) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr(
            f"agent_runtime-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: agent-runtime\nVersion: {version}\n",
        )


def test_release_candidate_report_checks_source_docs_and_wheel(tmp_path: Path) -> None:
    version = "0.8.30"
    _write_release_sources(tmp_path, version)
    wheel = tmp_path / f"agent_runtime-{version}-py3-none-any.whl"
    _write_wheel(wheel, version)

    report = build_release_candidate_report(tmp_path, wheel)

    assert report["status"] == "passed"
    assert report["version"] == version
    assert all(check["status"] == "passed" for check in report["checks"])


def test_release_candidate_report_detects_wheel_metadata_drift(tmp_path: Path) -> None:
    _write_release_sources(tmp_path, "0.8.30")
    wheel = tmp_path / "agent_runtime-0.8.30-py3-none-any.whl"
    _write_wheel(wheel, "0.8.29")

    report = build_release_candidate_report(tmp_path, wheel)

    assert report["status"] == "failed"
    failed = {check["name"] for check in report["checks"] if check["status"] == "failed"}
    assert "wheel.metadata_version" in failed
