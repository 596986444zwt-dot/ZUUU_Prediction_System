from .contracts import utc, require

def latest(*times):
    return max(times,key=utc)

def check(available,issue):
    require(utc(available)<=utc(issue),'Feature availability leakage')
