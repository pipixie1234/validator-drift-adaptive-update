import pytest
from pilot.prefix_paraphrase import natural_span, edited, prefix_variants, natural_variants
from pilot.day2_protocol import label_response
from pilot.prefix_paraphrase_report import paired_stats


def test_natural_replacement_changes_only_target_span():
    text=r'To solve this problem, we need to find $x=3$. Finally, \\boxed{7}'
    a,b=natural_span(text);r={'text':text,'edit_span':[a,b]}
    v={'name':'we_have_to','replacement':'we have to'}
    out=edited(r,v,'natural')
    assert out==text[:a]+'we have to'+text[b:]
    assert out.endswith(text[b:]) and '$x=3$' in out


def test_natural_span_does_not_cross_punctuation_or_formula_boundary():
    for text in ['we, need to find x',r'$x=2$, we need to solve','123 we need to find','Reasoning:\nwe need to compute']:
        assert natural_span(text) is None


def test_original_is_byte_identical_even_with_capitals_and_extra_spaces():
    text='We  need to calculate. Final answer: 3.'
    r={'text':text,'edit_span':natural_span(text)}
    assert edited(r,{'name':'original','replacement':'we need to'},'natural')==text
    out=edited(r,{'name':'require','replacement':'require'},'natural')
    assert out.startswith('Require calculate')


def test_only_first_natural_occurrence_is_changed():
    text='we need to compute, and we need to check.'
    r={'text':text,'edit_span':natural_span(text)}
    assert edited(r,{'name':'we_should','replacement':'we should'},'natural')=='we should compute, and we need to check.'


def test_syntax_stress_keeps_final_answer_and_truth_label():
    text='We need to find the answer.\n\\boxed{9}'
    r={'text':text,'edit_span':natural_span(text)}
    old=label_response(text,False,'10','gsm8k')
    assert old['correct'] is False
    for v in natural_variants():
        lab=label_response(edited(r,v,'natural'),False,'10','gsm8k')
        assert lab['correct'] is False and lab['value']==old['value']


def test_prefix_inventory_is_unique_and_contains_requested_variants():
    vv=prefix_variants()
    assert len(vv)==len({v['name'] for v in vv})==len({v['prefix'] for v in vv})==46
    assert {'we_should','we_have_to','require','we_require_to'}<={v['name'] for v in vv}


def test_paired_question_uncertainty_does_not_shrink_from_copying_siblings():
    y=[1,1,1,0,0,0];b=[0,0,1,0,0,1];qs=['a','a','b','b','c','c']
    a=paired_stats(y,b,qs,draws=1000)
    copied=paired_stats([v for v in y for _ in range(4)],[v for v in b for _ in range(4)],
                        [q for q in qs for _ in range(4)],draws=1000)
    assert a['questions']==copied['questions']==3
    for k in ['delta','cluster_SE','low','high','p_cluster']:
        assert abs(a[k]-copied[k])<1e-12


def test_no_decision_changes_are_not_reported_as_significant():
    result=paired_stats([0,1,1,0],[0,1,1,0],['a','b','c','d'],draws=100)
    assert result['delta']==0 and result['gained']==result['lost']==0
    assert result['p_cluster']==result['p_McNemar']==1 and result['all_pairs_unchanged']
