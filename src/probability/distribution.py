"""Forecast-only probability math. No current target input."""
import numpy as np
from scipy.special import ndtr,expit,logit
from .contracts import SUPPORT,FLOOR,require

K=np.asarray(SUPPORT)
EDGES=K+.5

def floor_mass(p):
 raw_sum=float(np.sum(p));require(raw_sum>0 and np.isfinite(p).all() and np.min(p)>=-1e-14,'Invalid pre-floor mass')
 p=np.maximum(p,0)/raw_sum
 out=(1-len(K)*FLOOR)*p+FLOOR
 return out,dict(pre_normalization_mass=raw_sum,floor_l1_adjustment=float(np.sum(np.abs(out-p))),floor=FLOOR)

def distribution(forecast,residuals,method):
 r=np.asarray(residuals,float);require(len(r)>1,'Residual history')
 if method.startswith('GAUSSIAN'):
  mu=float(r.mean());sigma=max(float(r.std(ddof=1)),.5)
  f=ndtr((EDGES-forecast-mu)/sigma);left=float(ndtr((K[0]-.5-forecast-mu)/sigma));right=float(ndtr((forecast+mu-K[-1]-.5)/sigma))
 elif method.startswith('KDE'):
  f=ndtr((EDGES[:,None]-forecast-r[None,:])/.75).mean(axis=1)
  left=float(ndtr((K[0]-.5-forecast-r)/.75).mean());right=float(ndtr((forecast+r-K[-1]-.5)/.75).mean());mu=float(r.mean());sigma=float(np.sqrt(r.var()+.75**2))
 else:
  latent=forecast+r;f=(latent[None,:]<EDGES[:,None]).mean(axis=1);left=float(np.mean(latent<K[0]-.5));right=float(np.mean(latent>=K[-1]+.5));mu=float(r.mean());sigma=float(r.std(ddof=1))
 p=np.diff(np.r_[0,f]);p[-1]+=1-f[-1]  # Both remote tails folded into edge bins.
 p,info=floor_mass(p)
 return p,dict(info,left_tail_mass=left,right_tail_mass=right,residual_mean=mu,residual_std=sigma)

def calibrate(p,alpha):
 f=np.cumsum(p);u=np.clip(f[:-1],1e-300,1-1e-16)
 transformed=np.r_[expit(alpha*logit(u)),1.0]
 return floor_mass(np.diff(np.r_[0,transformed]))

def score(p,actual):
 at=int(actual)-int(K[0]);require(0<=at<len(K),'Actual outside fixed support')
 f=np.cumsum(p);truth=(K>=actual).astype(float)
 ranked=np.argsort(-p,kind='stable');q=float(p[at]);mu=float(np.sum(p*K))
 out=dict(Brier=float(np.sum(p*p)+1-2*q),LogLoss=float(-np.log(q)),CRPS=float(np.sum((f[:-1]-truth[:-1])**2)),assigned_probability=q,top1_integer=int(K[ranked[0]]),top1_probability=float(p[ranked[0]]),top1_hit=int(at==ranked[0]),top2_hit=int(at in ranked[:2]),top3_hit=int(at in ranked[:3]),entropy=float(-np.sum(p*np.log(p))),effective_spread=float(np.sqrt(np.sum(p*(K-mu)**2))),within1_probability=float(p[np.abs(K-K[ranked[0]])<=1].sum()))
 for level in (.5,.8,.9):
  lo=int(K[min(np.searchsorted(f,(1-level)/2),len(K)-1)]);hi=int(K[min(np.searchsorted(f,1-(1-level)/2),len(K)-1)])
  out[f'interval{int(level*100)}_lower']=lo;out[f'interval{int(level*100)}_upper']=hi;out[f'interval{int(level*100)}_hit']=int(lo<=actual<=hi);out[f'interval{int(level*100)}_width']=hi-lo
 return out
