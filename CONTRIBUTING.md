# 贡献指南

欢迎通过 Pull Request 提交可审查的改进。本项目采用 [MIT License](LICENSE)，贡献同样按该许可证提供。中文和英文文档应保持一致；请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

`main` 为稳定主分支。外部贡献通过 Fork / Branch / Pull Request，维护者审查后合并；不要求或授予直接写入 `main` 的权限。CI 通过不代表生产验收或模型晋升。

1. Fork 仓库，在独立开发目录中克隆自己的 Fork。
2. 从 `main` 创建分支，例如 `git switch -c fix/metar-parser`。建议使用 `feature/*`、`fix/*`、`docs/*`、`research/*`。
3. 修改代码或文档，保持改动范围清晰；不要直接修改主分支。
4. 按 [README](README.md) / [English README](README_EN.md) 创建 Python 3.13 虚拟环境。公开测试只需 `python -m pip install -r requirements-ci.txt`；GUI/模型依赖按 README 单独安装。运行与修改相关的测试及下面的公开 CI 子集；完整测试需要隔离的数据与模型夹具。说明未执行的检查及原因。
5. 检查 `git diff` 和暂存文件，不提交密码、Token、Cookie、私钥、数据库、原始观测、备份、日志、大型审计包或个人环境信息。
6. 提交并推送自己的分支，向本仓库 `main` 创建 Pull Request，描述问题、修改行为、验证结果与已知限制，等待维护者审查。

```powershell
python -m pytest -p no:cacheprovider tests/test_zuuu_metar_parser.py tests/test_zuuu_metar_temperature_parser.py tests/test_zuuu_time_normalizer.py tests/test_zuuu_observation_record.py tests/test_zuuu_raw_identity.py -q
git diff
git add <reviewed-files>
git commit -m "Describe the change"
git push origin <your-branch>
```

## Research Pull Requests

研究类 PR 必须说明以下内容，可以先用 Research Proposal 表单讨论：

- 数据窗口：来源、许可、样本日期、样本量、缺失/回退样本及标签定义。
- As-of rule：issue/cutoff、观测与 run 时间、真实接收时间和结算/标签可用时间。
- Leakage control：禁止未来标签、后来修订或全样本预处理进入过去预测；说明拟合/选择/校准窗口。
- Walk-forward method：训练、验证、OOS 划分与按时间推进方式，不能随机打散时间序列来声称前向成绩。
- Comparison baseline：冻结基线、共同可用样本与相同评估口径。
- Evaluation metrics：温度误差、概率 proper scores、校准与样本支持，报告失败、回退、无预测和不确定性。

先提供离线研究证据，不要修改正在运行的生产流程或自动晋升模型。对 T0 / T+1 / T+2 分别说明影响；文档改动也需准确描述 Experimental、候选、正式和校准状态。

## English contributor quick start

Fork the repository, create a `feature/`, `fix/`, `docs/`, or `research/` branch from `main`, install Python 3.13 development dependencies, run the public tests above, commit reviewed files, and open a PR. Full validation needs isolated local frozen assets. Research PRs must document data windows, as-of rules, leakage controls, walk-forward/OOS design, baselines, and metrics. Do not submit runtime data or change model authorization without independent review.

预测时间语义、信息可用性、数据泄漏防护、冻结模型与概率协议的改动应提供可重复的独立证据。Experimental 不得在缺少验收时被描述为 Production 或 Champion；未校准概率不得被称为已校准，不得省略失败/回退样本来报告准确率。

请不要在正式生产目录执行训练、重建、清库、自动启动/停止 worker 或集成测试。测试数据应为脱敏、合成或已获授权的小型夹具。不要以提交代码为由修改正在运行的 Soak 或 T0 实验数据。
