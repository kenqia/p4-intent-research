# 理想 P4 个人研究

产品实验改动放在 `product/`。在 `submission/` 提交 `candidate.md`、实际 PDF 和 Word，以及 `manifest.json`。manifest 写入 `author_kind`、`environment: frozen-stage02-market`、可信 `gate/task.json` 的 SHA-256、报告文件名到 SHA-256 的 `artifacts` 映射，以及以原文页/行锚定的 `claims` 和可选 `charts`。禁止夹带判定、评审和验收代码。

GitHub Actions 使用基线提交中的工作流和验证器，从 PR HEAD 的 Git 对象读取报告数据。作者从 fork 提交，维护者持有父仓库管理权。评审失败、缺失或校准不达标会留下失败状态。改动验收规则、工作流或最佳参考时，单独提交维护 PR。评审版 `best/` 在产品 PR 中冻结。通过内容检查不改变最佳版本。

为 `main` 指定源自 GitHub Actions 的必需状态 `p4/content`，要求 PR 基于最新基线。要求一位有写权限的协作者审批，审批人应与最后推送者不同。启用失效审批撤销和管理员遵守规则，关闭自动合并与直推通道。为受信工作流配置变量 `P4_JUDGE_MODEL` 和专用受限的 Secret `P4_JUDGE_API_KEY`，可使用受保护环境限制读取。角色调用无工具、独立上下文，超时、拒绝、上下文不一致及无依据判断均阻断。在仓库仍未激活分支保护时，维护者需手动核对 GitHub Actions 真正可信工作流的 HEAD 状态，产品 PR 的绿色标记本身不更新最佳版本。

公开仓库配置 Judge 时，在 Actions Variables 添加 `P4_JUDGE_APPROVED_AUTHORS`，值为获准消耗评审预算的 fork 作者 GitHub 登录名组成的 JSON 数组，例如 `["research-user"]`。名单为空时，付费 Judge 保持关闭。将 `P4_JUDGE_BASE_URL` 显式设置为凭据对应的受信任端点。端点缺失或未获批准时，门禁会在请求发出前停止。受信任端点在 `gate/provider.py` 中固定。GPT-6.2 Sol 使用 `P4_JUDGE_MODEL=gpt-6.2-sol`、`P4_JUDGE_REASONING_EFFORT=medium`。密钥保留在 Actions Secret。
