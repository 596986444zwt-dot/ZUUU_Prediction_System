# T0 Experimental V1 实施报告

身份：T0_EXPERIMENTAL / FORWARD_VALIDATION。生成时间：2026-10-05T05:36:36.940035+00:00。

## 输入选择

单个合法 ECMWF run 覆盖目标 BJT 日 24/24 小时，按最新合法 run/version 排序，不跨 run 拼接，不以后来完整 run 补造旧预测。对应观测时间以及非整点 cutoff 使用同 run 邻接小时线性插值。剩余轨迹包含 cutoff 插值及日内后续有效小时。

Tmax_so_far 是同一 BJT 日期所有截止时实际可用的有效观测温度最大值，保存峰值时间。保留已看到的原 METAR 和 COR 的最高温硬下限；最新观测用于 bias 时按 observation_time 后 first_seen 排序选择已知版本。不删除上午、SPECI 或晚到版本。

## 两个公式

LEVEL0 = max(Tmax_so_far, max(remaining ECMWF trajectory))。

L1_A current_bias = latest observed temperature − interpolated same-run ECMWF temperature at observation_time。

L1_A = max(Tmax_so_far, max(remaining ECMWF trajectory + current_bias))。

全量 bias、固定公式、无调参或 ML。两个连续中心均不低于 observed hard floor。整数映射 floor(value+0.5)，未按成绩搜索 round/floor/ceil。无概率。

## 缺失与身份

无实况、无合法 ECMWF、轨迹/对应时刻无法计算、陈旧输入（观测 3 小时、ECMWF 24 小时）、计划错过均保存明确失败状态与两个 null 输出，不用未来输入补齐。阈值属于 Experimental 配置，全部进入配置版本。快照保存 UTC/BJT 时间、输入 identities/links、first_seen、发布时刻、原始/剩余/修正 trajectory 和 hash、floor/bias、两个输出、代码/配置/方法版本、trigger、状态、来源及 created_at。

上午 L1_A shadow，12 点后 active candidate 仅为实验展示身份，不是永久正确的切换规则。18 点高命中可能主要是峰值已发生，未来评价须区分早期预测价值与当天峰值确认。
