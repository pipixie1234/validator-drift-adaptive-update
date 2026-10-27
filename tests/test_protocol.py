import json
from pathlib import Path
import pytest
from pilot.core import *

@pytest.mark.parametrize('text,expected',[('#### 1,234','1234'),('Reason 99\n#### -2.50','-5/2'),('#### 1 / 3','1/3'),('#### -.5','-1/2'),('#### 0','0'),('#### 1.25','5/4')])
def test_exact_answers(text,expected):assert extract_answer(text)=={'status':'verified_numeric','answer':expected}

@pytest.mark.parametrize('text',['The result is 42.','#### 1,23','#### 1/0','#### 1\n#### 2','#### 2 apples','#### 2\nmore words','#### __import__("os").system("true")','#### 2 or 3'])
def test_reject_ambiguous_or_unsafe(text):assert extract_answer(text)['answer'] is None

def test_truncated_is_not_natural_error():assert extract_answer('#### 7',True)['status']=='truncated'

def test_gold_extraction():assert extract_gold('work <<1+1=2>>\n#### 2')=='2'

def test_normalization():assert question_hash('Ａ  B\nC')==question_hash('a b c')

def test_saved_split_and_label_isolation():
    sets=[]
    for split,count in [('A',600),('B',200),('C',100),('T',300)]:
        inputs=read_jsonl(ROOT/f'data/inputs/{split}.jsonl');labels=read_jsonl(ROOT/f'data/labels/{split}.jsonl')
        assert len(inputs)==len(labels)==count
        assert {x['question_id'] for x in inputs}=={x['question_id'] for x in labels}
        assert all('gold_answer' not in x and 'reference' not in x and 'answer' not in x for x in inputs)
        sets.append({x['question_hash'] for x in inputs})
    for i,s in enumerate(sets):
        for other in sets[i+1:]:assert s.isdisjoint(other)

@pytest.mark.parametrize('role',['G','V'])
def test_real_template_and_padding(role):
    from transformers import AutoTokenizer
    t=AutoTokenizer.from_pretrained(CFG[f'{role}_MODEL_DIR'],local_files_only=True)
    m=messages(role,'What is 1+1?','#### 2');target='#### 2' if role=='G' else 'A'
    a=training_example(t,m,target,1536);b=training_example(t,m,target+' '+target,1536)
    assert a and b
    batch=collate([a,b],t.pad_token_id)
    assert bool((batch['labels'][batch['attention_mask']==0]==-100).all())
    assert all(v==-100 for v in a['labels'][:a['prefix_length']])
    assert a['input_ids'][a['prefix_length']:]==a['labels'][a['prefix_length']:]
    assert training_example(t,m,target,2) is None

def test_cache_model_adapter_prompt_and_settings_isolation():
    key=cache_identity('V','base','V0','id','prompt',{'temperature':.7})
    assert key!=cache_identity('V','base','Vrand','id','prompt',{'temperature':.7})
    assert key!=cache_identity('V','base','Vtarget','id','prompt',{'temperature':.7})
    assert key!=cache_identity('V','base','V0','other','prompt',{'temperature':.7})
    assert key!=cache_identity('V','base','V0','id','new prompt',{'temperature':.7})
    assert key!=cache_identity('V','base','V0','id','prompt',{'temperature':.9})

def test_multitoken_label_likelihood():
    import torch
    from types import SimpleNamespace
    from pilot.runtime import conditional_label_scores
    class Tokenizer:
        pad_token_id=0
        def apply_chat_template(self,msgs,tokenize,add_generation_prompt):return [1,2] if tokenize else 'prefix'
        def encode(self,text,add_special_tokens):return {'prefixA':[1,2,3], 'prefixB':[1,2,4,5]}[text]
    fixed=torch.arange(2*4*7,dtype=torch.float32).reshape(2,4,7)/10
    class Model:
        device='cpu'
        def __call__(self,**kwargs):return SimpleNamespace(logits=fixed)
    actual=conditional_label_scores(Model(),Tokenizer(),[])
    a=float(fixed[0,1].log_softmax(-1)[3]);b=float(fixed[1,1].log_softmax(-1)[4]+fixed[1,2].log_softmax(-1)[5])
    assert actual['log_likelihood_A']==pytest.approx(a)
    assert actual['log_likelihood_B']==pytest.approx(b)
    assert actual['score']==pytest.approx(float(torch.tensor([a,b]).softmax(0)[0]))

def test_torn_jsonl_write_preserves_valid_prefix(tmp_path):
    p=tmp_path/'run.jsonl';p.write_bytes(b'{"id":1}\n{"id":')
    assert recover_jsonl(p)==[{'id':1}]
    assert p.read_text()=='{"id":1}\n'
    assert len(list(tmp_path.glob('*.partial_*')))==1
    append_jsonl(p,{'id':2});assert len(read_jsonl(p))==2

def test_changed_run_manifest_is_rejected(tmp_path):
    p=tmp_path/'manifest.json';ensure_run_manifest(p,{'adapter':'V0'})
    ensure_run_manifest(p,{'adapter':'V0'})
    with pytest.raises(RuntimeError):ensure_run_manifest(p,{'adapter':'Vtarget'})

def test_unicode_separator_inside_json_string(tmp_path):
    p=tmp_path/'unicode.jsonl'
    write_jsonl(p,[{'question':'A\u2028B\u0085C\u2029D'}])
    assert read_jsonl(p)==[{'question':'A\u2028B\u0085C\u2029D'}]

@pytest.mark.parametrize('text,expected',[(r'Thus \[\boxed{1,234}\]','1234'),(r'Answer: \(\boxed{-2.5}\).','-5/2'),(r'\boxed{\frac{1}{3}}','1/3'),(r'\boxed{2/3}','2/3')])
def test_independently_verifiable_terminal_box(text,expected):
    out=extract_candidate_answer(text);assert out['answer']==expected
    assert out['status']=='verified_numeric' and not out['format_compliant']
    assert out['answer_format']=='boxed'

@pytest.mark.parametrize('text',[r'\boxed{2} then \boxed{3}',r'\boxed{2} but 3 might be correct',r'\boxed{2 apples}',r'\boxed{\frac{1}{0}}',r'\boxed{2',r'#### 7 then \boxed{2}',r'\boxed{2+3}',r'\boxed{2} dollars for the camera.'])
def test_ambiguous_box_is_unverified(text):assert extract_candidate_answer(text)['answer'] is None

def test_terminal_box_truncated_is_not_verified():assert extract_candidate_answer(r'\boxed{2}',True)['answer'] is None

def test_inline_final_marker_conflicting_with_box_is_unverified():
    assert extract_candidate_answer(r'Final answer #### 7, then \boxed{2}')['answer'] is None
