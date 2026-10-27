import numpy as np
import pytest
from pilot.cache_followup import answer_spans,mask_features,independent_bootstrap,counts


def test_nested_box_span_includes_complete_fraction():
 text=r'Proof. \boxed{\frac{1}{2}}'
 spans=answer_spans(text)
 assert len(spans)==1 and text[slice(*spans[0])]==r'\boxed{\frac{1}{2}}'


def test_final_answer_mask_hole_is_rejected():
 ex={'input_ids':[1,2,3,4],'labels':[-100,-100,3,4],'prefix_length':2,'attention_mask':[1]*4}
 offsets=[(0,1),(1,2),(2,3),(3,4)]
 assert mask_features(ex,offsets,2,'xy')['loss_tokens']==2
 ex['labels'][3]=-100
 with pytest.raises(AssertionError):mask_features(ex,offsets,2,'xy')


def test_question_pairing_identical_models_have_zero_error_differences():
 rows=[{'question_id':'q1','correct':False,'accepted':True},
       {'question_id':'q1','correct':True,'accepted':False},
       {'question_id':'q2','correct':False,'accepted':False},
       {'question_id':'q2','correct':True,'accepted':True}]
 result=independent_bootstrap({'a':rows,'b':list(reversed(rows))},['q1','q2'],draws=50)
 for key in ['FPR','FNR']:assert np.array_equal(result['a'][key],result['b'][key])


def test_prompt_leak_is_rejected_even_when_answer_tokens_are_present():
 ex={'input_ids':[1,2,3,4],'labels':[-100,2,3,4],'prefix_length':2,'attention_mask':[1]*4}
 with pytest.raises(AssertionError):mask_features(ex,[(0,1),(1,2),(2,3),(3,4)],2,'xy')


def test_unknown_accepted_answer_does_not_become_a_false_positive():
 rows=[{'question_id':'q','correct':False,'accepted':True},
       {'question_id':'q','correct':False,'accepted':False},
       {'question_id':'q','correct':None,'accepted':True},
       {'question_id':'q','correct':True,'accepted':False}]
 m=counts(rows)
 assert m['FP']==1 and m['unknown']==1 and m['FPR']==.5 and m['FNR']==1
