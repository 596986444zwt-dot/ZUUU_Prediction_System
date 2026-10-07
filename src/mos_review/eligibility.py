"""Calendar eligibility, explicitly distinct from observation receipt evidence."""
from datetime import date, datetime, timedelta
from src.data_v1.contracts import BJT, utc
from .contracts import RULE, LAG_HOURS

def eligibility_time(day):
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=BJT)
    return start + timedelta(days=1, hours=LAG_HOURS)

def check_label(label, prediction):
    issue = utc(prediction['issue_time_utc'])
    day = label['business_date_bjt']
    eligible = (eligibility_time(day) <= issue
                and day < issue.astimezone(BJT).date().isoformat()
                and day < prediction['business_date_bjt']
                and utc(label['day_end_utc']) < issue
                and utc(label['issue_time_utc']) < issue)
    return dict(label_business_date=day, label_eligibility_time_bjt=eligibility_time(day).isoformat(),
        prediction_issue_time=prediction['issue_time_utc'], eligibility_rule=RULE,
        eligibility_lag=LAG_HOURS, is_label_eligible=bool(eligible))

def latest_eligible_date(issue):
    return utc(issue).astimezone(BJT).date()-timedelta(days=2)
