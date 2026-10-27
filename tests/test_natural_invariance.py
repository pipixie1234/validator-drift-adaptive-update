from pilot.natural_invariance import intervention_span, edited, stats_for
from pilot.day2_protocol import label_response


def test_natural_edit_never_touches_formula_or_final_answer():
    text='We need to find $x=3$. Final answer: \\boxed{7}'
    r={'text':text,'edit_span':intervention_span(text)}
    assert r['edit_span']['target']=='we need to'
    assert edited(r,'synonym')=='We have to find $x=3$. Final answer: \\boxed{7}'
    for v in ['delete','synonym']:
        before=label_response(text,False,'8','gsm8k');after=label_response(edited(r,v),False,'8','gsm8k')
        assert before['value']==after['value']=='7' and after['correct'] is False


def test_earliest_span_and_opening_math_boundary():
    text='To solve for $x$, we need to find the answer.'
    r={'text':text,'edit_span':intervention_span(text)}
    assert edited(r,'synonym')=='To find $x$, we need to find the answer.'
    assert intervention_span('$x=2$, we need to find the answer.') is None
    assert intervention_span('Explanation:\nwe need to find the answer.') is None


def test_word_pair_replacement_only_edits_one_recorded_occurrence():
    text='To evaluate this we need to find the value.'
    r={'text':text,'edit_span':intervention_span(text)}
    assert edited(r,'synonym')=='To judge this we need to find the value.'
    assert edited(r,'original')==text


def test_uncertainty_keeps_same_question_siblings_together():
    rows=[{'candidate_id':'a1','question_id':'a'},{'candidate_id':'b1','question_id':'b'}]
    by={('a1','delete'):{'new_error':True},('b1','delete'):{'new_error':False}}
    s=stats_for(rows,by,'delete',draws=500)
    copied=[{'candidate_id':r['candidate_id']+str(i),'question_id':r['question_id']} for r in rows for i in range(3)]
    other={(r['candidate_id']+str(i),'delete'):by[r['candidate_id'],'delete'] for r in rows for i in range(3)}
    t=stats_for(copied,other,'delete',draws=500)
    assert s['questions']==t['questions']==2 and s['new_error_rate']==t['new_error_rate']==.5
    assert s['low']==t['low'] and s['high']==t['high']
