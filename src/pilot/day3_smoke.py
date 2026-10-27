"""Natural positive/negative LoRA plumbing checks; never formal G1/G2 or V updates."""
import argparse
import gc
import json
import math
import random
import time
from collections import Counter
from .core import ROOT,read_jsonl,write_json,training_example,collate,digest,ensure_run_manifest
from .runtime import load,seed_all,model_identity
from .dapo_common import RUN, messages,sha
from .day3_protocol import select_feedback,quarantined_ids

OUT=ROOT/'runs/day3'

def config():return json.loads((ROOT/'configs/day3_smoke.json').read_text())

def pick_examples(role,tok):
    cfg=config();g=read_jsonl(RUN/'G0_dapo100.jsonl');v={r['candidate_id']:r for r in read_jsonl(RUN/'V0_dapo100.jsonl')}
    assert len(g)==len(v)==400
    # G selection does not read independent labels; source/answer correctness cannot filter feedback.
    labels={r['candidate_id']:r for r in read_jsonl(OUT/'labels_C.jsonl')} if role=='V' else None
    bad=quarantined_ids()
    if role=='G':
        allowed={r['candidate_id'] for r in select_feedback(g,v,.5,cfg['seed'],bad_ids=bad)}
    rng=random.Random(cfg['seed']);eligible=[];excluded=Counter()
    for row in g:
        if row['question_id'] in bad:excluded['question_quarantined']+=1;continue
        if row['truncated']:excluded['generation_truncated']+=1;continue
        if role=='G':
            if row['candidate_id'] not in allowed:excluded['not_selected_by_frozen_feedback_rule']+=1;continue
            target=row['text']
        else:
            correct=labels[row['candidate_id']]['correct']
            if correct is None:excluded['unknown_independent_truth']+=1;continue
            target='A' if correct else 'B'
        ex=training_example(tok,messages(role,row['question'],row['text'] if role=='V' else None),target,cfg['max_length'][role])
        if ex is None:excluded['overlength_no_truncation']+=1;continue
        eligible.append((row,ex,target))
    def stress_and_random(rows,n):
        assert rows
        longest=max(rows,key=lambda t:len(t[1]['input_ids']))
        rest=[t for t in rows if t[0]['question_id']!=longest[0]['question_id']];rng.shuffle(rest)
        selected=[longest];seen={longest[0]['question_id']}
        for item in rest:
            if item[0]['question_id'] in seen:continue
            selected.append(item);seen.add(item[0]['question_id'])
            if len(selected)==n:break
        assert len(selected)==n
        return selected
    if role=='G':
        chosen=stress_and_random(eligible,16)
    else:
        positive=stress_and_random([t for t in eligible if t[2]=='A'],8)
        negative=stress_and_random([t for t in eligible if t[2]=='B'],8)
        chosen=[x for pair in zip(positive,negative) for x in pair]
    return chosen,dict(excluded),len(eligible)

def main():
    import torch
    from transformers import AutoTokenizer
    from peft import LoraConfig,get_peft_model
    p=argparse.ArgumentParser();p.add_argument('--role',required=True,choices=['G','V']);args=p.parse_args();role=args.role
    cfg=config();OUT.mkdir(exist_ok=True);base,_=model_identity(role)
    reportpath=OUT/f'smoke_{role}_report.json';directory=OUT/f'smoke_{role}_adapter'
    if reportpath.exists():
        old=json.loads(reportpath.read_text());assert old['save_reload_passed'] and old['config_sha256']==sha(ROOT/'configs/day3_smoke.json')
        assert old['model_fingerprint']==base and sha(directory/'adapter_model.safetensors')==old['adapter_sha256']
        print(f'{role} completed smoke cached; no repeated optimizer steps.');return
    assert not directory.exists(),'Incomplete adapter found: preserve and archive it before a fresh smoke run'
    from .core import CFG
    tok=AutoTokenizer.from_pretrained(CFG[role+'_MODEL_DIR'],local_files_only=True);tok.padding_side='left'
    if tok.pad_token_id is None:tok.pad_token=tok.eos_token
    chosen,excluded,eligible=pick_examples(role,tok);examples=[t[1] for t in chosen]
    selected=[{'question_id':r['question_id'],'candidate_id':r['candidate_id'],
        'target':target if role=='V' else 'response_only','input_tokens':len(ex['input_ids']),
        'supervised_tokens':sum(t!=-100 for t in ex['labels']),
        'V0_accepted':True if role=='G' else None,'source_text_sha256':digest(r['text'])} for r,ex,target in chosen]
    ensure_run_manifest(OUT/f'smoke_{role}.manifest.json',{'role':role,'base':base,'adapter_initialization':None,
        'config_sha256':sha(ROOT/'configs/day3_smoke.json'),'runner_sha256':sha(ROOT/'src/pilot/day3_smoke.py'),
        'raw_G_sha256':sha(RUN/'G0_dapo100.jsonl'),'V_scores_sha256':sha(RUN/'V0_dapo100.jsonl'),
        'independent_labels_sha256':sha(OUT/'labels_C.jsonl') if role=='V' else None,'selection':selected})
    write_json(OUT/f'smoke_{role}_selection.json',{'selected':selected,'excluded':excluded,'eligible':eligible})
    assert len({r['candidate_id'] for r in selected})==16
    for ex in examples:
        assert all(t==-100 for t in ex['labels'][:ex['prefix_length']]) and any(t!=-100 for t in ex['labels'])
    batch=collate([min(examples,key=lambda e:len(e['input_ids'])),max(examples,key=lambda e:len(e['input_ids']))],tok.pad_token_id)
    assert bool((batch['labels'][batch['attention_mask']==0]==-100).all())
    seed_all(cfg['seed']);overall=time.perf_counter();model,tok=load(role);model.train();model.config.use_cache=False
    assert max(len(e['input_ids']) for e in examples)<=model.config.max_position_embeddings
    model=get_peft_model(model,LoraConfig(task_type='CAUSAL_LM',r=cfg['r'],lora_alpha=cfg['alpha'],
        lora_dropout=cfg['dropout'],target_modules=cfg['target_modules']))
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
    trainable={n:p for n,p in model.named_parameters() if p.requires_grad};frozen={n:p for n,p in model.named_parameters() if not p.requires_grad}
    assert trainable and all('lora_' in name for name in trainable)
    before={n:p.detach().cpu().clone() for n,p in trainable.items()};versions={n:p._version for n,p in frozen.items()}
    optimizer=torch.optim.AdamW(list(trainable.values()),lr=cfg['learning_rate']);optimizer.zero_grad(set_to_none=True)
    torch.cuda.reset_peak_memory_stats();torch.cuda.synchronize();start=time.perf_counter();logs=[]
    # A separate backward-only length probe tests real longer completions, without selecting them for SFT.
    raw_supp=read_jsonl(OUT/'length_replay/G0_native_context.jsonl')
    gold={r['question_id']:r['gold_answer'] for r in read_jsonl(ROOT/'data/dapo/labels/dapo100.jsonl')}
    from .day2_protocol import label_response
    stress=[]
    for row in raw_supp:
        if not row['original_truncated'] or row['truncated'] or row['question_id'] in quarantined_ids():continue
        truth=label_response(row['text'],False,gold[row['question_id']],'gsm8k')['correct']
        if role=='V' and truth is None:continue
        target=row['text'] if role=='G' else 'A' if truth else 'B'
        ex=training_example(tok,messages(role,row['question'],row['text'] if role=='V' else None),target,cfg['max_length'][role])
        if ex is not None:stress.append((row,ex))
    padding_tokens=0;natural_stress_length=None
    if stress:
        stressrow,stressex=max(stress,key=lambda t:len(t[1]['input_ids']))
        stress_kind='real_completed_supplementary_response'
    else:
        assert role=='V','G should have a completed real supplementary response'
        # Unknown supplementary answers never become A/B supervision, even in a smoke test.
        longest=max(chosen,key=lambda t:len(t[1]['input_ids']))
        stressrow,stressex=longest[0],dict(longest[1]);natural_stress_length=len(stressex['input_ids'])
        from .core import prompt_ids
        target_length=max(len(prompt_ids(tok,messages('V',r['question'],r['text'])))+2
            for r in raw_supp if not r['truncated'] and r['question_id'] not in quarantined_ids())
        target_length=min(cfg['max_length']['V'],max(target_length,natural_stress_length))
        padding_tokens=target_length-natural_stress_length
        stressex={**stressex,'input_ids':stressex['input_ids']+[tok.pad_token_id]*padding_tokens,
            'attention_mask':stressex['attention_mask']+[0]*padding_tokens,
            'labels':stressex['labels']+[-100]*padding_tokens}
        stress_kind='real_natural_label_with_masked_padding_capacity_probe'
    assert all(t==-100 for t in stressex['labels'][:stressex['prefix_length']])
    b={k:v.to('cuda') for k,v in collate([stressex],tok.pad_token_id).items()}
    loss=model(**b).loss;assert torch.isfinite(loss);loss.backward()
    stressloss=float(loss.detach());assert all(p.grad is None for p in frozen.values())
    optimizer.zero_grad(set_to_none=True)
    length_probe={'candidate_id':stressrow['candidate_id'],'input_tokens':len(stressex['input_ids']),
        'finite_loss':True,'loss':stressloss,'optimizer_steps':0,'source':'separate native-context supplement; not part of16 selected SFT examples'}
    length_probe.update({'kind':stress_kind,'natural_input_tokens':natural_stress_length or len(stressex['input_ids']),
        'masked_padding_tokens':padding_tokens,'no_unknown_answer_used_as_V_label':True})
    for step in range(cfg['optimizer_steps']):
        losses=[];step_labels=Counter()
        for micro in range(cfg['gradient_accumulation']):
            idx=step*cfg['gradient_accumulation']+micro;ex=examples[idx]
            b={k:t.to('cuda') for k,t in collate([ex],tok.pad_token_id).items()}
            loss=model(**b).loss;assert torch.isfinite(loss)
            (loss/cfg['gradient_accumulation']).backward();losses.append(float(loss.detach()))
            step_labels[selected[idx]['target']]+=1
        grad_sq=sum(float(p.grad.float().square().sum()) for p in trainable.values() if p.grad is not None)
        assert math.isfinite(grad_sq) and grad_sq>0 and all(p.grad is None for p in frozen.values())
        torch.nn.utils.clip_grad_norm_(list(trainable.values()),1.0);optimizer.step();optimizer.zero_grad(set_to_none=True)
        item={'step':step+1,'loss':sum(losses)/len(losses),'gradient_l2':math.sqrt(grad_sq),'consumed_target_counts':dict(step_labels)}
        logs.append(item);print(role,item,flush=True)
    torch.cuda.synchronize();training_seconds=time.perf_counter()-start;peak=torch.cuda.max_memory_allocated()
    changed=[n for n,p in trainable.items() if not torch.equal(before[n],p.detach().cpu())]
    assert changed and all(p._version==versions[n] for n,p in frozen.items())
    model.eval();model.gradient_checkpointing_disable()
    probe=torch.tensor([examples[0]['input_ids'][:128]],device='cuda')
    with torch.inference_mode():expected=model(input_ids=probe,use_cache=False).logits[:,-1].float().cpu()
    model.save_pretrained(directory);write_json(directory/'training_ids.json',selected)
    del optimizer,b,loss,trainable,frozen,before,model;gc.collect();torch.cuda.empty_cache()
    reloaded,_=load(role,directory)
    with torch.inference_mode():actual=reloaded(input_ids=probe,use_cache=False).logits[:,-1].float().cpu()
    max_diff=float((expected-actual).abs().max());assert torch.allclose(expected,actual,atol=.02,rtol=.01)
    assert model_identity(role)[0]==base
    report={'role':role,'purpose':cfg['purpose'],'formal_training_started':False,'model_fingerprint':base,
        'config_sha256':sha(ROOT/'configs/day3_smoke.json'),'unique_examples':16,'unique_questions':len({r['question_id'] for r in selected}),
        'optimizer_steps':2,'micro_batch':1,'gradient_accumulation':8,'examples_cycled':False,
        'label_counts':dict(Counter(r['target'] for r in selected)),
        'configured_max_length':cfg['max_length'][role],'actual_max_length':max(r['input_tokens'] for r in selected),
        'actual_input_tokens':sum(r['input_tokens'] for r in selected),'actual_supervised_tokens':sum(r['supervised_tokens'] for r in selected),
        'truncated_training_examples':0,'excluded_examples':excluded,'eligible_examples':eligible,
        'length_backward_only_probe':length_probe,
        'finite_loss':True,'nonzero_gradient':True,'only_lora_trainable':True,'base_gradients_absent':True,
        'base_parameter_versions_unchanged':True,'changed_adapter_tensors':len(changed),
        'prompt_and_padding_masks_checked':True,'inference_training_prefix_match':True,
        'save_reload_passed':True,'reload_max_logit_difference':max_diff,'training_seconds':training_seconds,
        'total_seconds_including_load_save_reload':time.perf_counter()-overall,'peak_allocated_bytes':peak,
        'adapter_path':str(directory),'adapter_sha256':sha(directory/'adapter_model.safetensors'),'logs':logs,
        'limit':'Plumbing and natural-label-path check only; not evidence of accuracy improvement or stability at untested longer sequences. Development-trained adapters must never enter formal trajectories.'}
    write_json(reportpath,report);print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
