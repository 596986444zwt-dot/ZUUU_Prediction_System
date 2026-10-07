"""Train-only X operations. Tree estimators use native missing values."""
import numpy as np
from .contracts import SEED,require

def fit_preprocessor(X,names,family):
 names=list(names);drop={};kept=[]
 alias=names.index('hist_horizon_bias_c') if 'hist_horizon_bias_c' in names else None
 original=names.index('hist_expanding_bias_c') if 'hist_expanding_bias_c' in names else None
 for j,n in enumerate(names):
  x=X[:,j];finite=x[np.isfinite(x)]
  if not len(finite):drop[n]='ALL_NULL_TRAINING_FEATURE';continue
  if j==alias and original is not None and np.array_equal(x,X[:,original],equal_nan=True):drop[n]='EXACT_DUPLICATE_OF_hist_expanding_bias_c';continue
  other=next((k for k in kept if np.array_equal(x,X[:,k],equal_nan=True)),None)
  if other is not None:drop[n]='EXACT_DUPLICATE_OF_'+names[other];continue
  if np.var(finite)<=1e-12:drop[n]='CONSTANT_TRAINING_VARIANCE';continue
  kept.append(j)
 require(kept,'No legal features')
 A=X[:,kept];state=dict(indices=kept,feature_names=[names[j] for j in kept],dropped=drop,family=family,
  training_rows=len(X),valid_counts=np.sum(np.isfinite(X),axis=0).tolist(),null_counts=np.sum(np.isnan(X),axis=0).tolist(),
  variance=[float(np.var(x[np.isfinite(x)])) if np.isfinite(x).any() else None for x in X.T],
  imputation_policy='TRAINING_MEDIAN_WITH_TRAINING_MISSING_INDICATORS' if family=='RIDGE' else 'NATIVE_NAN_NO_IMPUTATION',scaler_policy='TRAINING_POPULATION_MEAN_STD' if family=='RIDGE' else 'NONE')
 if family=='RIDGE':
  median=np.nanmedian(A,axis=0);indicator=np.flatnonzero(np.isnan(A).any(axis=0));B=np.where(np.isnan(A),median,A)
  if len(indicator):B=np.column_stack((B,np.isnan(A[:,indicator]).astype(float)))
  mean=B.mean(axis=0);scale=B.std(axis=0);scale[scale<=1e-12]=1
  state.update(medians=median.tolist(),indicator_indices=indicator.tolist(),means=mean.tolist(),scales=scale.tolist())
 else:state.update(medians=[],indicator_indices=[],means=[],scales=[])
 state['input_feature_count']=len(kept)+len(state['indicator_indices'])
 return state
def transform(X,state):
 A=X[:,state['indices']]
 if state['family']!='RIDGE':return A.copy()
 B=np.where(np.isnan(A),np.array(state['medians']),A)
 if state['indicator_indices']:B=np.column_stack((B,np.isnan(A[:,state['indicator_indices']]).astype(float)))
 return (B-np.array(state['means']))/np.array(state['scales'])
def estimator(family,params):
 if family=='RIDGE':
  from sklearn.linear_model import Ridge
  return Ridge(**params,solver='svd')
 if family=='RANDOM_FOREST':
  from sklearn.ensemble import RandomForestRegressor
  return RandomForestRegressor(**params,random_state=SEED,n_jobs=1)
 if family=='LIGHTGBM':
  from lightgbm import LGBMRegressor
  return LGBMRegressor(**params,random_state=SEED,n_jobs=1,deterministic=True,force_col_wise=True,verbosity=-1)
 if family=='XGBOOST':
  from xgboost import XGBRegressor
  return XGBRegressor(**params,random_state=SEED,n_jobs=1,tree_method='hist',objective='reg:squarederror')
 if family=='CATBOOST':
  from catboost import CatBoostRegressor
  return CatBoostRegressor(**params,random_seed=SEED,thread_count=1,verbose=False,allow_writing_files=False,nan_mode='Min',bootstrap_type='No')
 raise ValueError(family)
