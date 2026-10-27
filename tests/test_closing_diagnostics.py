from pilot.closing_diagnostics import corrupt_final, natural_features
from pilot.day2_protocol import label_response


def test_final_answer_control_changes_only_box_contents_and_truth():
    r={'text':r'We find 7 after careful reasoning. Final answer: \boxed{7}.','gold_answer':'7'}
    out,reason=corrupt_final(r)
    assert reason is None and out['text']==r'We find 7 after careful reasoning. Final answer: \boxed{8}.'
    assert label_response(r['text'],False,'7','gsm8k')['correct'] is True
    assert label_response(out['text'],False,'7','gsm8k')['correct'] is False


def test_final_answer_control_rejects_multiple_explicit_assertions():
    for text in [r'\boxed{7} and \boxed{7}', '\\boxed{7}\n#### 7']:
        out,reason=corrupt_final({'text':text,'gold_answer':'7'})
        assert out is None and reason


def test_wrong_final_number_has_no_artificial_range_restriction():
    out,reason=corrupt_final({'text':r'\boxed{999}','gold_answer':'999'})
    assert reason is None and out['new_value']=='1000'
    out,reason=corrupt_final({'text':r'\boxed{2457}','gold_answer':'2457'})
    assert reason is None and out['new_value']=='2458'


def test_chain_patterns_respect_the_same_opening_window():
    assert natural_features('We need to find $x$.')['we need to']
    assert natural_features('We can solve it.')['we can']
    assert not natural_features('$x$, we need to solve it.')['we need to']
    assert not natural_features('Explanation:\nwe need to solve it.')['we need to']
