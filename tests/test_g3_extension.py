import pytest
from pilot.g3_common import pool,adapter
from pilot.g3_engine import request_seed
from pilot.core import digest
import numpy as np
from pilot.g3_report import paired, contrast, metrics


def test_only_isolated_A_C_pools_are_accessible():
 for split in ['B','T']:
  with pytest.raises(ValueError):pool(split)


def test_request_seed_keeps_original_question_seed_rule():
 q='dapo:train:fixed-example'
 assert request_seed(q)==42+int(digest(q)[:8],16)%1000000


def test_G3_parent_is_original_G2_and_new_adapter_is_isolated():
 assert str(adapter('G2')).endswith('runs/formal/checkpoints/G2_SFT')
 assert 'g3_extension_20261024/checkpoints/G3_SFT' in str(adapter('G3'))


def test_bootstrap_pairs_whole_questions_with_all_four_candidates():
 rows=[{'question_id':q,'correct':False,'accepted':accept,'truncated':False,'output_tokens':10}
       for q,accept in [('q1',True),('q2',False)] for _ in range(4)]
 after=[{**r,'accepted':False} for r in rows]
 p,b=paired({'before':rows,'after':after},['q1','q2'],draws=200)
 assert p['before']['FPR']==.5 and p['after']['FPR']==0
 assert set(np.unique(b['before']['FPR']))=={0,.5,1}
 assert np.array_equal(b['after']['FPR'],np.zeros(200))
 diffs=contrast(p,b,'after','before','A','test')
 row=next(r for r in diffs if r['metric']=='FPR')
 assert row['estimate']==-.5 and row['low']==-1 and row['high']==0
 p2,b2=paired({'before':rows,'after':after},['q1','q2'],draws=200)
 assert np.array_equal(b['before']['FPR'],b2['before']['FPR'])


def test_paired_analysis_rejects_different_question_support():
 with pytest.raises(AssertionError):
  paired({'a':[{'question_id':'q1'}],'b':[{'question_id':'q2'}]},['q1'])


def test_unknown_bounds_keep_unknowns_out_of_observed_FPR_FNR():
 # TP=8,FN=2,FP=3,TN=7,unknown accepted=4,unknown rejected=6.
 m=metrics(np.array([8,2,3,7,4,6,0,100]))
 assert float(m['FPR'])==.3 and float(m['FNR'])==.2
 assert float(m['FPR_unknown_lower'])==3/16
 assert float(m['FPR_unknown_upper'])==7/14
 assert float(m['FNR_unknown_lower'])==2/14
 assert float(m['FNR_unknown_upper'])==8/16
