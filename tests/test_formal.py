import json
import random
import numpy as np
import pytest
import torch
from pilot.formal_common import inputs,messages
from pilot.dapo_common import messages as frozen_messages
from pilot.formal_pipeline import steps
from pilot.formal_report import paired_deltas,transition0
from pilot.formal_train import groups,save_rng,restore_rng

def test_no_test_split_or_old_prompt_protocol():
    with pytest.raises(ValueError):inputs('T')
    assert len(inputs('C'))==96
    assert len(inputs('A'))==600 and len(inputs('B'))==200
    assert messages('G','q')==frozen_messages('G','q')
    assert messages('V','q','response')==frozen_messages('V','q','response')

def test_required_trajectory_order_and_fixed_verifier():
    schedule=steps();names=[n for n,_ in schedule]
    assert names.index('evaluate_G0_C')<names.index('generate_G0_A')<names.index('select_G1')<names.index('train_G1_SFT')
    assert names.index('train_G1_SFT')<names.index('generate_G1_A')<names.index('select_G2')<names.index('train_G2_SFT')
    assert names.index('train_G2_SFT')<names.index('evaluate_G2_C')<names.index('final_report')
    assert all('T' not in args for _,args in schedule)
    assert [args for _,args in schedule if args[0]=='formal_train']==[['formal_train','--target','G1'],['formal_train','--target','G2']]

def test_last_partial_accumulation_matches_batch_mean():
    order=list(range(11));batches=groups(order,8)
    assert [len(x) for x in batches]==[8,3]
    assert [i for batch in batches for i in batch]==order
    model=torch.nn.Linear(2,1,bias=False);x=torch.arange(22,dtype=torch.float32).view(11,2)/30;y=torch.ones(11,1)
    for indices in batches:
        model.zero_grad();loss=torch.nn.functional.mse_loss(model(x[indices]),y[indices]);loss.backward();expected=model.weight.grad.clone()
        model.zero_grad()
        for index in indices:(torch.nn.functional.mse_loss(model(x[index:index+1]),y[index:index+1])/len(indices)).backward()
        assert torch.allclose(expected,model.weight.grad,atol=1e-6)

def test_checkpoint_rng_restores_next_draws_without_cuda(tmp_path,monkeypatch):
    monkeypatch.setattr(torch.cuda,'get_rng_state_all',lambda:[])
    monkeypatch.setattr(torch.cuda,'set_rng_state_all',lambda _:None)
    random.seed(42);np.random.seed(42);torch.manual_seed(42);save_rng(tmp_path)
    expected=(random.random(),np.random.random(),torch.rand(4))
    random.random();np.random.random();torch.rand(10);restore_rng(tmp_path)
    actual=(random.random(),np.random.random(),torch.rand(4))
    assert actual[0]==expected[0] and actual[1]==expected[1] and torch.equal(actual[2],expected[2])

def test_paired_deltas_and_unknown_transition_denominators():
    old=[{'question_id':'q1','candidate_index':0,'correct':True,'accepted':True},
         {'question_id':'q2','candidate_index':0,'correct':False,'accepted':True},
         {'question_id':'q3','candidate_index':0,'correct':None,'accepted':True}]
    new=[dict(r) for r in old];new[0]['accepted']=False;new[1]['accepted']=False
    values=paired_deltas(old,new,reps=20)
    assert values['FNR']['delta_vs_G0']==1 and values['FPR']['delta_vs_G0']==-1
    transition=transition0(old,new)
    assert transition['b_old_correct_new_wrong']==1 and transition['c_old_wrong_new_correct']==1 and transition['unknown_pairs_excluded']==1
