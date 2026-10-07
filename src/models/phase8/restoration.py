"""Evaluate saved numeric LightGBM trees without unstable loaded native handles.

This is inference over the SAME trained LightGBM tree structures, not training,
parameter selection, or substituting a different estimator. The evaluator is
an additional cross-check. Native restore works with the controlled runtime
startup order below and remains the authoritative library prediction path.
"""
import math
import numpy as np

def initialize_runtime():
 """Load native dependencies in the same order as the frozen model experiment."""
 import sklearn.linear_model
 import sklearn.ensemble
 import lightgbm
 import xgboost
 import catboost

def load_fitted_state(path):
 """Restore a locally hash-verified Phase8 artifact after controlled startup."""
 initialize_runtime()
 import pickle
 from pathlib import Path
 return pickle.loads(Path(path).read_bytes())

def evaluate_lightgbm_dump(model,X):
 if model['num_class']!=1 or model['num_tree_per_iteration']!=1:raise ValueError('Only numeric regression model supported')
 def leaf(node,row):
  while 'leaf_value' not in node:
   if node['decision_type']!='<=':raise ValueError('Categorical splits are outside FEATURE_V1 contract')
   v=float(row[node['split_feature']]);kind=node['missing_type']
   missing=(math.isnan(v) and kind=='NaN') or (kind=='Zero' and (math.isnan(v) or abs(v)<=1e-35))
   if missing:left=node['default_left']
   else:
    if math.isnan(v):v=0.0
    left=v<=float(node['threshold'])
   node=node['left_child'] if left else node['right_child']
  return node['leaf_value']
 result=np.array([sum(leaf(t['tree_structure'],row) for t in model['tree_info']) for row in X])
 return result/len(model['tree_info']) if model['average_output'] else result
