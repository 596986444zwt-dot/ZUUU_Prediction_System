"""Display-only translations. Never alter backend enums or model identities."""
import logging
from datetime import timedelta
from .adapters import now,utc,BJT

STATUS_TRANSLATIONS={
 'READ ONLY':'只读运行','RUNNING':'正常运行','STOPPED':'已停止','UNKNOWN':'状态未知',
 'EXPERIMENTAL':'实验模型','UNCALIBRATED':'概率尚未校准','CALIBRATED':'已应用概率校准',
 'DEGRADED':'降级运行','NO_FORECAST':'暂无预测','FALLBACK':'备用预测',
 'FRESH':'数据最新','STALE':'数据陈旧','DATA STALE':'数据严重陈旧','ERROR':'数据异常',
 'ONLINE':'在线','OFFLINE':'离线','DELAYED':'请求延迟','OK':'正常',
 'PENDING':'等待中','PASS':'通过','FAILED':'失败','WARNING':'警告','DATA WARNING':'数据警告',
 'PENDING_SOAK':'连续运行测试进行中','PENDING_REVIEW':'等待人工审核',
 'READY_FOR_FINAL_SOAK_ACCEPTANCE':'已达到最低观察时长，等待最终验收',
 'ENGINE_HEALTHY':'系统正常','ENGINE_DEGRADED':'系统降级','ENGINE_STARTING':'系统启动中',
 'T0 EXPERIMENTAL':'实验运行','EVALUATION READY':'达到首次评估数据门槛',
 'EXPERIMENTAL PROBABILITY':'实验概率研究','CALIBRATION / VALIDATION CANDIDATE':'校准验证候选',
 'T0 CALIBRATED PROBABILITY':'已授权校准概率','SCHEDULED':'定时预测','EVENT':'事件触发','STARTUP':'启动触发',
 'NO_LEGAL_ECMWF_RUN':'正在等待完整ECMWF数据','ECMWF_STALE':'ECMWF数据严重陈旧',
 'PROBABILITY_HISTORY_INSUFFICIENT':'概率历史样本不足','OFF_FIXED_ISSUE_REGIME':'非固定发布时间',
 'SOURCE_DEGRADED':'数据源降级','OLDER_COMPLETE_FALLBACK':'采用较早完整数据轮次',
 'MISSING_OBS':'缺少实况观测','STALE_OBS':'实况观测陈旧','MISSING_ECMWF':'缺少ECMWF数据',
 'STALE_ECMWF':'ECMWF数据陈旧','MISSING_REMAINING_TRAJECTORY':'剩余时段预报不足',
 'NOT AUTHORIZED / NONE':'尚未正式授权','NONE':'暂无','FORWARD_VALIDATION':'前向验证',
}
def zh(value):
    if not value:return '暂无数据'
    if any('\u4e00'<=c<='\u9fff' for c in str(value)):return str(value)
    return STATUS_TRANSLATIONS.get(str(value),'状态待核实')
def clock(value,full=False):
    if not value:return '--'
    try:
        t=utc(value).astimezone(BJT)
        return t.strftime('%Y年%m月%d日 %H:%M') if full else t.strftime('%m月%d日 %H:%M:%S')
    except (ValueError,TypeError):return '时间语义未知'
def duration(seconds):
    if seconds is None:return '--'
    minutes=max(0,int(seconds)//60)
    return f'{minutes//60}小时{minutes%60}分钟' if minutes>=60 else f'{minutes}分钟'
def targets(current=None):
    day=(current or now()).astimezone(BJT).date()
    return {h:(day+timedelta(days=n)).isoformat() for h,n in (('T0',0),('T1',1),('T2',2))}
def date_title(h,backend_date=None,current=None):
    expected=targets(current)[h];actual=backend_date or expected
    mismatch=actual!=expected
    try:
        from datetime import date
        d=date.fromisoformat(actual);short=f'{d.month}月{d.day}日'
    except (ValueError,TypeError):short='日期待核实';mismatch=True
    if mismatch:logging.getLogger('phase11').warning('GUI_TARGET_DATE_MISMATCH %s expected=%s actual=%s',h,expected,actual)
    return f"{dict(T0='今天 T0',T1='明天 T+1',T2='后天 T+2')[h]}｜{short}"+(' · 日期不一致' if mismatch else ''),mismatch
