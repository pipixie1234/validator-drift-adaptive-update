import pytest
from pilot.random_control import draw_length_conditioned


def test_first_admissible_uniform_draw_is_reproducible_and_length_only():
    lengths=[[10,30],[20,40],[10,20]]
    choice,trace=draw_length_conditioned(lengths,60,seed=20261024,tolerance=0,max_draws=100)
    assert (choice,trace)==draw_length_conditioned(lengths,60,seed=20261024,tolerance=0,max_draws=100)
    assert trace['length_matched'] and sum(lengths[i][j] for i,j in enumerate(choice))==60
    assert all(t!=60 for t in trace['rejected_length_totals'])


def test_no_admissible_tuple_retains_first_draw_instead_of_closest():
    lengths=[[10,30],[20,40]]
    choice,trace=draw_length_conditioned(lengths,999,seed=42,tolerance=0,max_draws=20)
    first,_=draw_length_conditioned(lengths,999,seed=42,tolerance=0,max_draws=1)
    assert choice==first and not trace['length_matched']
    assert trace['selected_total']==trace['first_draw_total']


def test_missing_structural_candidate_is_an_error_not_silent_question_drop():
    with pytest.raises(ValueError):draw_length_conditioned([[1],[]],1)
