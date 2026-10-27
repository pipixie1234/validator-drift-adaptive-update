"""Separate rationale-conditioned protocol on fixed development examples only."""
import gc
import json
import re
import time
from pathlib import Path

from .core import read_jsonl,recover_jsonl,write_json,append_jsonl,digest,ensure_run_manifest
from .formal_common import sha,utc,gpu_lock,WorkBudget
from .runtime import load,model_identity
from .week_final_common import RUN,PLAN,VS,validate,vadapter,state

SYSTEM='Judge whether the FINAL ANSWER in the candidate response correctly answers the question. Independently verify the relevant mathematics. Judge final-answer correctness, not completeness of the explanation. First give a concise check in at most 180 words. Then end with exactly VERDICT: A if the final answer is correct, or VERDICT: B if it is incorrect. Do not merely repeat the candidate reasoning. You are not given a reference answer.'


def reason_messages(r):
    return [{'role':'system','content':SYSTEM},{'role':'user','content':f'Question:\n{r["question"]}\n\nCandidate response:\n{r["text"]}'}]


def parse_reason(text):
    matches=list(re.finditer(r'(?im)^\s*VERDICT\s*:\s*([AB])\s*[.!]?\s*$',text))
    if len(matches)==1 and not text[matches[0].end():].strip():
        return matches[0][1],text[:matches[0].start()].strip(),'explicit_verdict'
    bare=re.fullmatch(r'\s*([AB])[.!]?\s*',text)
    if bare:return bare[1],'','standalone_label_no_reason'
    return None,text.strip(),'missing_or_ambiguous_verdict'


def generate_reasons():
    import torch
    cfg=validate();rows=read_jsonl(RUN/'reason_inputs_C20.jsonl');assert len(rows)==20
    d=RUN/'rationales';d.mkdir(exist_ok=True)
    ensure_run_manifest(d/'protocol.json',{'plan_sha256':sha(PLAN),'system':SYSTEM,'code_sha256':sha(Path(__file__)),
        'sampling':cfg['rationale_review']['generation'],'inputs_sha256':sha(RUN/'reason_inputs_C20.jsonl'),
        'not_original_A_B_hidden_reason':True,'reference_answers_never_in_prompt':True})
    for v in VS:
        path=d/(v+'.jsonl');saved=recover_jsonl(path);done={r['candidate_id'] for r in saved};assert len(saved)==len(done)
        pending=[r for r in rows if r['candidate_id'] not in done]
        if not pending:continue
        with gpu_lock():
            budget=WorkBudget('week_final/rationales/'+v);tick=time.perf_counter();model,tok=load('V',vadapter(v));fp,ad=model_identity('V',vadapter(v))
            for offset in range(0,len(pending),4):
                budget.check();batch=pending[offset:offset+4]
                rendered=[tok.apply_chat_template(reason_messages(r),tokenize=False,add_generation_prompt=True) for r in batch]
                encoded=tok(rendered,return_tensors='pt',padding=True,add_special_tokens=False).to('cuda')
                assert encoded['input_ids'].shape[1]+768<=8192
                with torch.inference_mode():
                    output=model.generate(**encoded,max_new_tokens=768,do_sample=False,use_cache=True,pad_token_id=tok.pad_token_id,eos_token_id=tok.eos_token_id)
                for r,seq in zip(batch,output):
                    ids=seq[encoded['input_ids'].shape[1]:].tolist()
                    if tok.eos_token_id in ids:ids=ids[:ids.index(tok.eos_token_id)+1]
                    text=tok.decode(ids,skip_special_tokens=True)
                    verdict,reason,status=parse_reason(text)
                    append_jsonl(path,{'candidate_id':r['candidate_id'],'question_id':r['question_id'],'G_round':r['G_round'],'split':'C','V':v,
                        'rationale_full_text':text,'rationale_body':reason,'verdict':verdict,'parse_status':status,
                        'truncated':len(ids)==768 and tok.eos_token_id not in ids,'generated_token_ids':ids,
                        'output_tokens':len(ids),'prompt_sha256':digest(reason_messages(r)),
                        'model_fingerprint':fp,'adapter_fingerprint':ad,'protocol_sha256':sha(d/'protocol.json'),'at':utc()})
                    done.add(r['candidate_id'])
                budget.charge('batch_'+str(offset));state('C_rationale_generation',V=v,done=len(done),total=20)
                print(v,'C rationales',len(done),'/20',flush=True)
            assert model_identity('V',vadapter(v))==(fp,ad) and len(done)==20
            write_json(d/(v+'_compute.json'),{'seconds_this_invocation':time.perf_counter()-tick,'reasons':20})
            del model;gc.collect();torch.cuda.empty_cache()
