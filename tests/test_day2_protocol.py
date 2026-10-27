import pytest
from pilot.day2_protocol import *

@pytest.mark.parametrize('text,value',[(r'Therefore \(\boxed{33}\) pieces of gum.','33'),(r'Answer \boxed{15}\%.','15'),(r'There are \boxed{3} new 5-ounce candles.','3'),(r'\boxed{2}\text{ seconds}.','2'),(r'\boxed{\frac{1}{3}}.','\\frac{1}{3}')])
def test_boxed_units_and_descriptive_suffix(text,value):
    a=extract(text);assert a['status']=='extracted' and a['value']==value

@pytest.mark.parametrize('text',[r'\boxed{2} but \boxed{3}',r'\boxed{2} then the answer is 7.',r'\boxed{2} x=3',r'\boxed{2} ```python',r'\boxed{2'])
def test_ambiguities_remain_unknown(text):assert extract(text)['value'] is None

def test_repeated_equivalent_boxes():assert extract(r'\boxed{1/2} and \boxed{0.5}')['value']=='0.5'

def test_truncated_with_complete_box_stays_unknown():assert extract(r'\boxed{2}',True)['value'] is None

@pytest.mark.parametrize('value,gold,answer',[(r'\frac{2}{6}','1/3',True),(r'\sqrt{8}',r'2\sqrt{2}',True),(r'\frac{1}{\sqrt{2}}',r'\frac{\sqrt{2}}{2}',True),('x^2+2x+1','(x+1)^2',True),('x+1','x+2',False),('(1,2)','(2,1)',False),(r'\{1,2\}',r'\{2,1\}',True),('3','4',False)])
def test_exact_symbolic_equivalence(value,gold,answer):assert compare(value,gold,'math')[0] is answer

@pytest.mark.parametrize('value',[r'__import__("os")',r'\unknown{2}',r'\begin{matrix}1&2\end{matrix}',r'10^{1000000}'])
def test_unsupported_or_unsafe_math_is_unknown(value):assert compare(value,'1','math')[0] is None

def test_percent_number_and_units():assert compare(r'15\%','15','gsm8k')[0] is True

def test_numerical_parse_failure_is_not_an_error():assert label_response(r'\boxed{not sure}',False,'2','gsm8k')['correct'] is None

def test_pi_constant_is_not_a_free_variable():
    assert compare(r'\frac{\pi}{4}',r'\pi/4','math')[0] is True
    assert compare(r'\pi',r'3','math')[0] is False

def test_requested_percentage_value_in_math():
    assert compare(r'80\%',r'80','math')[0] is True
    assert compare(r'80\%',r'0.8','math')[0] is False

def test_spelled_out_trailing_alternative_is_unknown():assert extract(r'\boxed{2} but the answer is three')['value'] is None

def test_known_answer_with_failed_verifier_score_remains_labelled():
    from pilot.day2_evaluate import stats
    rows=[{'question_id':'q1','correct':True,'accepted':True},{'question_id':'q1','correct':False,'accepted':None},{'question_id':'q2','correct':False,'accepted':False},{'question_id':'q2','correct':None,'accepted':True}]
    m=stats(rows)
    assert m['verified']==3 and m['natural_errors']==2 and m['error_questions']==2
    assert m['V_scored_verified']==2 and m['V_unscored_verified']==1
    assert m['G_accuracy_verified']==pytest.approx(1/3) and m['V_accuracy']==1
    assert m['false_accept_rate']==0 and m['G_correctness_upper_bound_all_outputs']==.5

def test_imaginary_unit_matches_radical():
    assert compare('i',r'\sqrt{-1}','math')[0] is True
    assert compare('i^2','-1','math')[0] is True

def test_finite_set_ignores_repeated_elements():assert compare(r'\{1,1,2\}',r'\{2,1\}','math')[0] is True

def test_markdown_step_headings_are_not_answer_conflicts():assert extract('#### Step 1: Calculate\nReasoning.\n'+r'\boxed{4}')['value']=='4'

def test_real_numeric_hash_conflicting_with_box_stays_unknown():assert extract('#### 3\n'+r'\boxed{4}')['value'] is None
