# 历史失败日志来源

`initial-ruff-error.log` 和 `initial-mypy-error.log` 是从同一会话首次开发时的
真实工具返回中逐字保存的完整诊断输出，不是当前版本重新执行失败所得，
也没有为了重现失败回退源码。两项首次检查均为 FAIL、退出码 1。
原工具调用 ` .venv/bin/ruff check src tests; .venv/bin/mypy src` 的工作目录为项目根目录。
首次 ruff 输出与首次 mypy 输出由异步工具两次返回，现分别存档。

首次环境创建和依赖下载的工具输出此前未存为独立完整原始日志；本次不补造。
已保留实际 `install.log`、原版本锁和新执行的依赖版本/一致性检查日志。
新执行记录均在 `delivery-*.log`，包含命令、工作目录、时间、退出码和完整 stdout/stderr。
