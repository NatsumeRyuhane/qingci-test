# 安全攻击合成样本（SYNTHETIC）

本目录包含 [`docs/security.md`](../../../docs/security.md) 中登记的全部攻击编号（`ATK-01` 至 `ATK-63`）的**合成攻击样本**，供 `#7` 离线评测端到端重放（`copilot eval --suite security`）。

- **全部为合成数据**（`synthetic: true`），不含任何真实客户或客服原文（D-002）。
- 每个文件以编号命名（例如 `ATK-20-refund-injection.json`），结构由 `copilot.eval.security.AttackFixture` 校验。
- `tests/eval/test_security.py` 检查 `docs/security.md` 中登记的每一个 `ATK-NN` 编号都在本目录有对应样本。
