import numpy as np
from pilot.prefix_length_analysis import within, slope, bootstrap_slopes, lengths


def test_category_adjustment_removes_between_category_length_confounding():
    x=np.array([1,2,10,11],float);y=np.array([.2,.2,0,0]);cats=['a','a','b','b']
    assert slope(x,y)<0
    assert abs(float(slope(x,y,cats)))<1e-12


def test_question_resampling_keeps_all_variant_outcomes_together():
    Y=np.array([[0,1,1],[0,0,1],[1,0,1]],float);x=[1,2,3];cats=['a','a','a']
    result=bootstrap_slopes(x,Y,cats,draws=100)
    assert result[0]['questions']==3 and result[0]['prefixes']==3
    assert abs(result[0]['slope']-float(slope(x,Y.mean(0))))<1e-12
    zero=bootstrap_slopes(x,np.ones((3,3)),cats,draws=100)
    assert zero[0]['question_low']==zero[0]['crossed_high']==0


def test_length_definition_distinguishes_words_characters_and_format_tokens():
    class FakeTokenizer:
        def encode(self,text,add_special_tokens=False):return list(text)
    v=lengths("Let's reason step by step.\n",FakeTokenizer())
    assert v['words']==5 and v['characters']==26 and v['raw_characters']==27
    assert v['prefix_tokens']==27 and v['tokens_without_edge_whitespace']==26


def test_within_demeaning_preserves_batch_rows():
    y=np.array([[1,3,9,11],[2,4,10,12]],float)
    out=within(y,['a','a','b','b'])
    np.testing.assert_array_equal(out,[[-1,1,-1,1],[-1,1,-1,1]])
