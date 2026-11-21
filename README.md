# 理想 P4 个人研究

产品实验改动放在 `product/`。在 `submission/` 提交 `candidate.md`、实际 PDF 和 Word，以及 `manifest.json`。manifest 写入 `author_kind`、`environment: frozen-stage02-market`、可信 `gate/task.json` 的 SHA-256、报告文件名到 SHA-256 的 `artifacts` 映射，以及以原文页/行锚定的 `claims` 和可选 `charts`。禁止夹带判定、评审和验收代码。

GitHub Actions 使用基线提交中的工作流和验证器，从 PR HEAD 的 Git 对象读取报告数据。作者从 fork 提交，维护者持有父仓库管理权。评审失败、缺失或校准不达标会留下失败状态。改动验收规则、工作流或最佳参考时，单独提交维护 PR。评审版 `best/` 在产品 PR 中冻结。通过内容检查不改变最佳版本。

`main` 要求通过 PR 合并，并在最新基线上通过来自 GitHub Actions 的 `p4/content`。分支保护适用于管理员，自动合并关闭。保持实验作者的 fork 权限与父仓库维护权限分离，由维护者决定合并和最佳版本更新。为受信工作流配置变量 `P4_JUDGE_MODEL` 和专用受限的 Secret `P4_JUDGE_API_KEY`。评审角色在独立上下文运行，超时、拒绝、上下文不一致和无依据判断均阻断。通过 `p4/content` 后，报告仍需单独提交维护 PR 才能更新最佳版本。

公开仓库配置 Judge 时，在 Actions Variables 添加 `P4_JUDGE_APPROVED_AUTHORS`，值为获准消耗评审预算的 fork 作者 GitHub 登录名组成的 JSON 数组，例如 `["research-user"]`。名单为空时，付费 Judge 保持关闭。将 `P4_JUDGE_BASE_URL` 显式设置为凭据对应的受信任端点。端点缺失或未获批准时，门禁会在请求发出前停止。受信任端点在 `gate/provider.py` 中固定。GPT-6.2 Sol 使用 `P4_JUDGE_MODEL=gpt-6.2-sol`、`P4_JUDGE_REASONING_EFFORT=medium`。密钥保留在 Actions Secret。
