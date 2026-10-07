# T0 Experimental V1 实施报告

身份：T0_EXPERIMENTAL / FORWARD_VALIDATION。生成时间：2026-10-05T05:36:36.940035+00:00。

固定计划时点：08:00、10:00、12:00、14:00、16:00、18:00 BJT。允许 120 秒执行宽限，cutoff 保持计划时刻；实际 created_at 和 execution delay 另外记录。超过宽限追加 NO_FORECAST_SCHEDULE_MISSED，不用后续信息补造计划预测。新观测/新 ECMWF 的事件预测在 08～18 时段运行，trigger 类型单列，不与固定时点混算。

每个合法上下文生成 Level0、L1_A 两个独立输出。上午 L1_A=EXPERIMENTAL_SHADOW；12 点后为 EXPERIMENTAL_ACTIVE_CANDIDATE。没有唯一 winner，没有正式概率。

日目标沿用冻结 DAILY_TMAX_RULE_V1 的纯计算逻辑，在实验库产生独立证据；至少 24 个 hourly slots、24 个 distinct observation times、无同观测时刻温度冲突，D+2 后才确认。缺失或版本冲突为待确认，禁止偷偷删除 COR 或选有利版本。确认后出现新版本会暂停对应评价，保留旧结算，禁止覆盖原预测/评分。实验确认规则仍须独立验收，不能直接视为正式模型训练标签。

结算为追加记录：actual_integer_tmax、连续 error/absolute error、integer_prediction、integer_exact_hit、within_1c。整数中心预先固定 floor(prediction+0.5)，±1 命中用整数中心；MAE/RMSE/Bias 用连续中心。按 method、cutoff、date、month、season 汇总，scheduled 与 event 分开。未结算 N=0 时准确率/误差为 null，所有六个计划时点仍展示，并报告 attempted、missing、unsettled。评估状态追加，CSV/latest pointer 仅为可再生导出。

未来正式训练必须先固定时间分割，检查真实可用证据、标签、缺失与版本，独立比较 Level0/L1_A；不允许随机划分或自动晋升。当前没有 forward validated accuracy。
