# Local Coding Agent Instructions

- 修改前先使用 git_status 检查工作区。
- 搜索代码后再读取目标文件。
- 大文件优先使用 read_file_lines。
- 修改已有文件优先使用 apply_patch。
- 修改后必须使用 git_diff 检查结果。
- Python 修改后运行相关 pytest。
- 不允许修改 agent-runtime.toml。
- 不允许读取或输出任何 API Key。
- 不允许自动 commit 或 push。
- 功能变化必须同步更新文档。