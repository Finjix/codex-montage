# FF 三件套（V20.2.5）

本仓库包含 FFmpeg 控制器、语义分析包和完整执行器。三个可安装的 Codex 技能位于 `components/`；维护脚本位于 `tools/`，中文使用文档位于 `docs/`。

## 使用入口

- 执行器：`components/montage-three-part-orchestrator-ff/scripts/three_suite_ff.py`
- 部署：双击 `start.cmd`，或运行 `install.ps1`
- 只做部署预检：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -PreflightOnly`
- 校验三件套：`python tools/build_ff_suite.py verify`
- 运行回归与发布检查：`python tools/validate_v20_release.py`

`suite_registry.json`、`runtime-lock.json` 和 `.manifests/` 是运行时清单。维护脚本生成的本机报告写入 `artifacts/`，不纳入版本控制。详细规则见 [使用说明](docs/使用说明.md) 和 [跨电脑部署说明](docs/跨电脑部署说明.md)。
