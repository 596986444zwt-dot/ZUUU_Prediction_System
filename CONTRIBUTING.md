# 贡献指南

欢迎通过 Pull Request 提交可审查的改进。许可证尚待所有者决定（`LICENSE_PENDING_USER_DECISION`），请在许可证确定前与维护者确认贡献授权安排。

1. Fork 仓库，在独立开发目录中克隆自己的 Fork。
2. 从 `main` 创建分支，例如 `git switch -c fix/metar-parser`。
3. 修改代码或文档，保持改动范围清晰；不要直接修改主分支。
4. 按 README 安装依赖，运行与修改相关的测试。解析器测试可用 `python -m pytest tests/test_zuuu_metar_parser.py tests/test_zuuu_metar_temperature_parser.py -q`；完整测试需要隔离的数据与模型夹具。说明未执行的检查及原因。
5. 检查 `git diff` 和暂存文件，不提交密码、Token、Cookie、私钥、数据库、原始观测、备份、日志、大型审计包或个人环境信息。
6. 提交并推送自己的分支，向本仓库 `main` 创建 Pull Request，描述问题、修改行为、验证结果与已知限制，等待维护者审查。

预测时间语义、信息可用性、数据泄漏防护、冻结模型与概率协议的改动应提供可重复的独立证据。Experimental 不得在缺少验收时被描述为 Production 或 Champion；未校准概率不得被称为已校准，不得省略失败/回退样本来报告准确率。

请不要在正式生产目录执行训练、重建、清库、自动启动/停止 worker 或集成测试。测试数据应为脱敏、合成或已获授权的小型夹具。不要以提交代码为由修改正在运行的 Soak 或 T0 实验数据。
