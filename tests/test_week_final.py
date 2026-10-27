from pilot.week_final_reasons import parse_reason,reason_messages
from pilot.week_initial_review import paragraph_spans,exact_checks


def test_reason_verdict_ambiguity_keeps_unknown_and_bare_label_has_no_reason():
    assert parse_reason('Check: 53 is prime.\nVERDICT: B')==('B','Check: 53 is prime.','explicit_verdict')
    assert parse_reason('A.\n')==('A','','standalone_label_no_reason')
    for text in ['VERDICT: A\nVERDICT: B','VERDICT: A\nMore text','Probably correct','']:
        assert parse_reason(text)[0] is None


def test_rationale_input_never_uses_reference_or_prior_V_decision():
    base={'question':'What is 2+2?','text':'The answer is 5.'}
    expected=reason_messages(base)
    poisoned={**base,'gold_answer':'SECRET_REFERENCE','correct':False,'V0_score':.999,'V0_accepted':True}
    assert reason_messages(poisoned)==expected
    assert all('SECRET_REFERENCE' not in message['content'] for message in expected)


def test_steps_keep_exact_unicode_spans_and_do_not_rewrite_original_text():
    raw='  原始首段：x²=2。\n\n\tSecond trial: 53?\n \n  No, 53 is prime.  '
    spans=paragraph_spans(raw)
    assert [raw[a:b] for a,b in spans]==['原始首段：x²=2。','Second trial: 53?','No, 53 is prime.']
    assert all(a<b and (i==0 or spans[i-1][1]<a) for i,(a,b) in enumerate(spans))


def test_exact_case_proofs_separate_final_answer_and_process_correctness():
    checks=exact_checks()
    assert checks['smallest_composite_nonsquare']['first_valid']==3127
    assert checks['smallest_composite_nonsquare']['53_fourth_power']!=885849
    assert checks['three_squares']['ordered_non_decreasing_roots']==[(1,4,8),(3,6,6),(4,4,7)]
    assert checks['real_cube_roots']['product_of_cube_roots']==-2
