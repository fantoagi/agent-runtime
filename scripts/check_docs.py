#!/usr/bin/env python3
"""Validate Agent Runtime evolution documentation and optional Git change gates."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
CURRENT = ROOT / "docs" / "CURRENT.md"
ROADMAP = ROOT / "docs" / "ROADMAP.md"

REQUIRED_FILES = (
    "docs/README.md",
    "docs/CURRENT.md",
    "docs/ARCHITECTURE.md",
    "docs/CHANGELOG.md",
    "docs/ROADMAP.md",
    "docs/LEARNING.md",
    "docs/MULTI_AGENT.md",
    "docs/CONTEXT_MEMORY.md",
    "docs/adr/README.md",
    "docs/templates/change-entry.md",
    "docs/templates/adr.md",
)

CORE_PATHS = {
    "src/agent_runtime/domain.py",
    "src/agent_runtime/runtime.py",
    "src/agent_runtime/providers.py",
    "src/agent_runtime/tools.py",
    "src/agent_runtime/storage.py",
    "src/agent_runtime/orchestration.py",
    "src/agent_runtime/context.py",
    "src/agent_runtime/memory.py",
    "src/agent_runtime/observability.py",
    "src/agent_runtime/evals.py",
}

REQUIRED_FIELDS = (
    "完成时间",
    "状态",
    "类型",
    "影响范围",
    "关联 commit",
    "关联 ADR",
)

REQUIRED_SECTIONS = (
    "变更摘要",
    "系统架构",
    "实现方式",
    "当前功能",
    "已知限制",
    "测试与验收",
    "后续计划",
)

CHANGE_HEADING = re.compile(
    r"^## (?P<id>E(?P<date>\d{4}-\d{2}-\d{2})-(?P<seq>\d{3}))：(?P<title>.+)$",
    re.MULTILINE,
)
FIELD_PATTERN = r"^- \*\*{label}\*\*：(?P<value>.*)$"
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\((?P<target>[^)]+\.md)(?:#[^)]+)?\)")
ADR_LINK = re.compile(r"\]\((?P<target>\./adr/[^)#]+\.md)(?:#[^)]+)?\)")
CHANGE_ID = re.compile(r"E\d{4}-\d{2}-\d{2}-\d{3}")
COMMIT_HASH = re.compile(r"[0-9a-fA-F]{7,40}")
STATUS_MARKERS = ("✅ stable", "🧪 experimental", "🚧 partial", "📋 planned", "⛔ unsupported")
ROADMAP_STATUSES = {
    "✅ completed",
    "🚧 in-progress",
    "📋 planned",
    "💡 candidate",
    "⛔ out-of-scope",
}
ROADMAP_REQUIRED_SECTIONS = (
    "状态定义",
    "版本总览",
    "演进原则",
    "v0.6",
    "v0.7",
    "v0.8",
    "v0.9",
    "v0.10",
    "v1.0",
    "明确暂不优先事项",
    "路线图维护规则",
)
ROADMAP_ROW = re.compile(
    r"^\|\s*(?P<version>v\d+\.\d+(?:\.\d+)?)\s*"
    r"\|\s*(?P<status>[^|]+?)\s*"
    r"\|\s*(?P<goal>[^|]+?)\s*"
    r"\|\s*(?P<record>[^|]+?)\s*\|$",
    re.MULTILINE,
)
VERSION = re.compile(r"v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?")


@dataclass(frozen=True)
class ChangeEntry:
    change_id: str
    completed: date
    sequence: int
    title: str
    body: str


@dataclass(frozen=True)
class RoadmapEntry:
    version_text: str
    version: tuple[int, int, int]
    status: str
    record: str


class Validation:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def field_value(body: str, label: str) -> str | None:
    match = re.search(FIELD_PATTERN.format(label=re.escape(label)), body, re.MULTILINE)
    return match.group("value").strip() if match else None


def parse_entries(validation: Validation) -> list[ChangeEntry]:
    if not CHANGELOG.exists():
        return []
    text = read_text(CHANGELOG)
    matches = list(CHANGE_HEADING.finditer(text))
    if not matches:
        validation.error("docs/CHANGELOG.md 中没有合法的 Change ID 标题。")
        return []

    entries: list[ChangeEntry] = []
    seen: set[str] = set()
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        change_id = match.group("id")
        id_date_text = match.group("date")
        sequence = int(match.group("seq"))

        if change_id in seen:
            validation.error(f"Change ID 重复：{change_id}")
        seen.add(change_id)

        try:
            id_date = date.fromisoformat(id_date_text)
        except ValueError:
            validation.error(f"{change_id} 的日期非法：{id_date_text}")
            continue

        for label in REQUIRED_FIELDS:
            if field_value(body, label) is None:
                validation.error(f"{change_id} 缺少字段：{label}")

        for section in REQUIRED_SECTIONS:
            if not re.search(rf"^### {re.escape(section)}\s*$", body, re.MULTILINE):
                validation.error(f"{change_id} 缺少章节：{section}")

        completed_text = field_value(body, "完成时间")
        if completed_text == "pending":
            status = field_value(body, "状态") or ""
            if "partial" not in status:
                validation.error(f"{change_id} 只有 partial 条目可以使用 pending 完成时间。")
            completed = id_date
        else:
            try:
                completed = date.fromisoformat(completed_text or "")
            except ValueError:
                validation.error(f"{change_id} 的完成时间必须是 YYYY-MM-DD：{completed_text!r}")
                completed = id_date
            if completed != id_date:
                validation.error(f"{change_id} 的编号日期与完成时间不一致。")
            if completed > today:
                validation.error(f"{change_id} 的完成时间 {completed} 晚于 Asia/Shanghai 当前日期 {today}。")

        entries.append(ChangeEntry(change_id, completed, sequence, match.group("title").strip(), body))

    expected = sorted(entries, key=lambda item: (item.completed, item.sequence), reverse=True)
    if [entry.change_id for entry in entries] != [entry.change_id for entry in expected]:
        validation.error("docs/CHANGELOG.md 未按完成日期和当天序号倒序排列。")

    return entries


def validate_entry_details(
    validation: Validation, entries: list[ChangeEntry], require_commit_hash: bool
) -> None:
    for entry in entries:
        impact_match = re.search(
            r"^- \*\*影响范围\*\*：\s*\n(?P<paths>(?:\s{2}- `[^`]+`\s*\n?)+)",
            entry.body,
            re.MULTILINE,
        )
        if not impact_match:
            validation.error(f"{entry.change_id} 的影响范围必须至少包含一个反引号路径。")
        else:
            paths = re.findall(r"^\s{2}- `([^`]+)`", impact_match.group("paths"), re.MULTILINE)
            for relative in paths:
                if any(character in relative for character in "*?[]"):
                    validation.error(f"{entry.change_id} 的影响路径不能使用通配符：{relative}")
                    continue
                if not (ROOT / relative).exists():
                    validation.error(f"{entry.change_id} 引用了不存在的路径：{relative}")

        commit_value = (field_value(entry.body, "关联 commit") or "").strip("` ")
        if commit_value == "pending":
            if require_commit_hash:
                validation.error(f"{entry.change_id} 的关联 commit 仍为 pending。")
        elif not COMMIT_HASH.fullmatch(commit_value):
            validation.error(f"{entry.change_id} 的关联 commit 不是 7-40 位 Git hash：{commit_value!r}")

        for match in ADR_LINK.finditer(entry.body):
            target = (CHANGELOG.parent / match.group("target")).resolve()
            if not target.is_file():
                validation.error(f"{entry.change_id} 引用了不存在的 ADR：{match.group('target')}")


def validate_current(validation: Validation, known_ids: set[str], require_commit_hash: bool) -> None:
    if not CURRENT.exists():
        return
    text = read_text(CURRENT)
    baseline = re.search(r"^- \*\*当前代码基线 commit\*\*：`([^`]+)`", text, re.MULTILINE)
    if baseline is None:
        validation.error("docs/CURRENT.md 缺少当前代码基线 commit。")
    elif baseline.group(1) == "pending":
        if require_commit_hash:
            validation.error("docs/CURRENT.md 的当前代码基线 commit 仍为 pending。")
    elif not COMMIT_HASH.fullmatch(baseline.group(1)):
        validation.error("docs/CURRENT.md 的当前代码基线 commit 不是合法 Git hash。")

    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.startswith("|") and line.count("|") >= 4 and any(marker in line for marker in STATUS_MARKERS):
            ids = CHANGE_ID.findall(line)
            if not ids:
                validation.error(f"docs/CURRENT.md 第 {line_number} 行的状态项没有关联 Change ID。")
            for change_id in ids:
                if change_id not in known_ids:
                    validation.error(
                        f"docs/CURRENT.md 第 {line_number} 行引用未知 Change ID：{change_id}"
                    )


def parse_version(value: str) -> tuple[int, int, int] | None:
    match = VERSION.fullmatch(value.strip())
    if match is None:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch") or 0),
    )


def validate_roadmap(validation: Validation, known_ids: set[str]) -> None:
    if not ROADMAP.exists():
        return

    text = read_text(ROADMAP)
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()

    headings = re.findall(r"^## (?P<title>.+?)\s*$", text, re.MULTILINE)
    for required in ROADMAP_REQUIRED_SECTIONS:
        if not any(title == required or title.startswith(f"{required}：") for title in headings):
            validation.error(f"docs/ROADMAP.md 缺少章节：{required}")

    updated_match = re.search(
        r"^- \*\*最近更新\*\*：(?P<value>\d{4}-\d{2}-\d{2})\s*$",
        text,
        re.MULTILINE,
    )
    if updated_match is None:
        validation.error("docs/ROADMAP.md 缺少合法的最近更新日期。")
    else:
        try:
            updated = date.fromisoformat(updated_match.group("value"))
        except ValueError:
            validation.error("docs/ROADMAP.md 的最近更新日期必须是 YYYY-MM-DD。")
        else:
            if updated > today:
                validation.error(
                    f"docs/ROADMAP.md 的最近更新日期 {updated} 晚于 Asia/Shanghai 当前日期 {today}。"
                )

    roadmap_current_match = re.search(
        r"^- \*\*当前版本\*\*：`?(?P<value>v?\d+\.\d+(?:\.\d+)?)`?\s*$",
        text,
        re.MULTILINE,
    )
    roadmap_current = None
    if roadmap_current_match is None:
        validation.error("docs/ROADMAP.md 缺少合法的当前版本。")
    else:
        roadmap_current = parse_version(roadmap_current_match.group("value"))

    current_text = read_text(CURRENT) if CURRENT.exists() else ""
    current_match = re.search(
        r"^- \*\*当前版本\*\*：`?(?P<value>v?\d+\.\d+(?:\.\d+)?)`?\s*$",
        current_text,
        re.MULTILINE,
    )
    current_version = None
    if current_match is None:
        validation.error("docs/CURRENT.md 缺少可与 ROADMAP 对齐的当前版本。")
    else:
        current_version = parse_version(current_match.group("value"))

    if roadmap_current is not None and current_version is not None and roadmap_current != current_version:
        validation.error("docs/ROADMAP.md 的当前版本与 docs/CURRENT.md 不一致。")

    entries: list[RoadmapEntry] = []
    seen_versions: set[tuple[int, int, int]] = set()
    for match in ROADMAP_ROW.finditer(text):
        version_text = match.group("version").strip()
        version = parse_version(version_text)
        if version is None:
            validation.error(f"docs/ROADMAP.md 包含非法版本：{version_text}")
            continue
        if version in seen_versions:
            validation.error(f"docs/ROADMAP.md 版本重复：{version_text}")
        seen_versions.add(version)

        status = match.group("status").strip()
        record = match.group("record").strip()
        if status not in ROADMAP_STATUSES:
            validation.error(f"docs/ROADMAP.md 的 {version_text} 状态非法：{status}")

        change_ids = CHANGE_ID.findall(record)
        if status == "✅ completed":
            if len(change_ids) != 1:
                validation.error(f"docs/ROADMAP.md 的 completed 版本 {version_text} 必须关联一个 Change ID。")
            elif change_ids[0] not in known_ids:
                validation.error(
                    f"docs/ROADMAP.md 的 {version_text} 引用未知 Change ID：{change_ids[0]}"
                )
        entries.append(RoadmapEntry(version_text, version, status, record))

    if not entries:
        validation.error("docs/ROADMAP.md 的版本总览中没有合法版本记录。")
        return

    versions = [entry.version for entry in entries]
    if versions != sorted(versions) or len(versions) != len(set(versions)):
        validation.error("docs/ROADMAP.md 的版本总览必须按版本从低到高排列且不能重复。")

    in_progress = [entry.version_text for entry in entries if entry.status == "🚧 in-progress"]
    if len(in_progress) > 1:
        validation.error(
            "docs/ROADMAP.md 同一时间最多只能有一个 in-progress 主版本："
            + ", ".join(in_progress)
        )

    completed = [entry for entry in entries if entry.status == "✅ completed"]
    if not completed:
        validation.error("docs/ROADMAP.md 至少需要一个 completed 版本。")
    else:
        latest_completed = max(completed, key=lambda entry: entry.version)
        if roadmap_current is not None and latest_completed.version != roadmap_current:
            validation.error(
                "docs/ROADMAP.md 的最新 completed 版本必须与当前版本一致："
                f"{latest_completed.version_text} != v{roadmap_current[0]}.{roadmap_current[1]}.{roadmap_current[2]}"
            )


def validate_adrs(validation: Validation, known_ids: set[str]) -> None:
    adr_dir = ROOT / "docs" / "adr"
    for path in sorted(adr_dir.glob("[0-9][0-9][0-9][0-9]-*.md")):
        text = read_text(path)
        if not re.search(r"^# ADR-\d{4}：.+$", text, re.MULTILINE):
            validation.error(f"{path.relative_to(ROOT)} 缺少合法 ADR 标题。")
        for label in ("状态", "日期", "决策人", "关联变更"):
            if not re.search(FIELD_PATTERN.format(label=re.escape(label)), text, re.MULTILINE):
                validation.error(f"{path.relative_to(ROOT)} 缺少字段：{label}")
        for section in ("背景", "决策", "影响", "被放弃的方案", "后续约束"):
            if not re.search(rf"^## {re.escape(section)}\s*$", text, re.MULTILINE):
                validation.error(f"{path.relative_to(ROOT)} 缺少章节：{section}")
        for change_id in CHANGE_ID.findall(text):
            if change_id not in known_ids:
                validation.error(f"{path.relative_to(ROOT)} 引用未知 Change ID：{change_id}")


def validate_markdown_links(validation: Validation) -> None:
    docs_root = ROOT / "docs"
    for path in docs_root.rglob("*.md"):
        text = read_text(path)
        for match in MARKDOWN_LINK.finditer(text):
            raw_target = match.group("target")
            if "://" in raw_target or any(token in raw_target for token in ("NNNN", "YYYY", "eyyyy")):
                continue
            target = (path.parent / raw_target).resolve()
            if not target.is_file():
                validation.error(
                    f"{path.relative_to(ROOT)} 包含失效 Markdown 链接：{raw_target}"
                )


def git_output(*arguments: str) -> tuple[int, str]:
    process = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return process.returncode, process.stdout.strip()


def validate_git_gate(validation: Validation, base_ref: str | None) -> None:
    if not base_ref or set(base_ref) == {"0"}:
        validation.warn("未提供有效 base ref，跳过 Git diff 文档同步门禁。")
        return
    code, _ = git_output("rev-parse", "--verify", f"{base_ref}^{{commit}}")
    if code != 0:
        validation.warn(f"无法解析 base ref {base_ref!r}，跳过 Git diff 文档同步门禁。")
        return
    code, output = git_output("diff", "--name-only", f"{base_ref}...HEAD")
    if code != 0:
        validation.error(f"无法计算 {base_ref}...HEAD 的 Git diff。")
        return

    changed = {line.replace("\\", "/") for line in output.splitlines() if line.strip()}
    if changed & CORE_PATHS and "docs/CHANGELOG.md" not in changed:
        validation.error("核心 Runtime 代码已变化，但 docs/CHANGELOG.md 未同步更新。")
    if any(path.startswith("tests/") for path in changed) and "docs/CHANGELOG.md" not in changed:
        validation.error("测试代码已变化，但 docs/CHANGELOG.md 未记录测试与验收变化。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        default=None,
        help="可选 Git 基线；用于检查核心代码变更是否同步修改 CHANGELOG。",
    )
    parser.add_argument(
        "--require-commit-hash",
        action="store_true",
        help="不允许 CHANGELOG 和 CURRENT 中出现 pending commit。",
    )
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    validation = Validation()

    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            validation.error(f"缺少必需文档：{relative}")

    entries = parse_entries(validation)
    known_ids = {entry.change_id for entry in entries}
    validate_entry_details(validation, entries, args.require_commit_hash)
    validate_current(validation, known_ids, args.require_commit_hash)
    validate_roadmap(validation, known_ids)
    validate_adrs(validation, known_ids)
    validate_markdown_links(validation)
    validate_git_gate(validation, args.base_ref)

    for warning in validation.warnings:
        print(f"WARNING: {warning}")
    if validation.errors:
        for error in validation.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"Documentation validation failed with {len(validation.errors)} error(s).", file=sys.stderr)
        return 1

    print(f"Documentation validation passed: {len(entries)} change entries checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
