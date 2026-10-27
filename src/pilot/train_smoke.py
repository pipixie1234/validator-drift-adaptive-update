import argparse
import math
import time
from .core import *
from .runtime import *

def main():
    import torch
    from peft import LoraConfig,get_peft_model,PeftModel
    parser=argparse.ArgumentParser();parser.add_argument('--role',choices=['G','V'],required=True);args=parser.parse_args();role=args.role;settings=CFG['training'];seed_all(CFG['seed'])
    source='G0_A_smoke' if role=='G' else 'G0_B_smoke'
    rows=read_jsonl(ROOT/f'runs/day1/{source}.jsonl');labels={r['question_id']:r['gold_answer'] for r in read_jsonl(ROOT/f'data/labels/{"A" if role=="G" else "B"}.jsonl')}
    model,tok=load(role);model.train();model.config.use_cache=False
    lora=LoraConfig(task_type='CAUSAL_LM',r=settings['r'],lora_alpha=settings['alpha'],lora_dropout=settings['dropout'],target_modules=['q_proj','k_proj','v_proj','o_proj','gate_proj','up_proj','down_proj'])
    model=get_peft_model(model,lora);model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
    if role=='V':
        from itertools import zip_longest
        valid=[r for r in rows if r['verification_status']=='verified_numeric']
        positive=[r for r in valid if r['extracted_answer']==labels[r['question_id']]]
        negative=[r for r in valid if r['extracted_answer']!=labels[r['question_id']]]
        rows=[r for pair in zip_longest(positive,negative) for r in pair if r is not None]
    examples=[];excluded=0;selected=[]
    for row in rows:
        if row['verification_status']!='verified_numeric':excluded+=1;continue
        target=row['text'] if role=='G' else ('A' if row['extracted_answer']==labels[row['question_id']] else 'B')
        ex=training_example(tok,messages(role,row['question'],None if role=='G' else row['text']),target,settings['max_length'])
        if ex is None:excluded+=1;continue
        examples.append(ex);selected.append({'candidate_id':row['candidate_id'],'target':target if role=='V' else 'candidate_response','supervised_tokens':sum(x!=-100 for x in ex['labels'])})
        if len(examples)>=16:break
    if len(examples)<2:raise RuntimeError('Insufficient natural parsed training examples')
    # Long and short real examples check padding as well as prompt masks.
    batch=collate([min(examples,key=lambda e:len(e['input_ids'])),max(examples,key=lambda e:len(e['input_ids']))],tok.pad_token_id)
    assert bool((batch['labels'][batch['attention_mask']==0]==-100).all())
    for ex in examples:
        assert all(x==-100 for x in ex['labels'][:ex['prefix_length']]);assert any(x!=-100 for x in ex['labels'])
    trainable={n:p for n,p in model.named_parameters() if p.requires_grad};frozen={n:p for n,p in model.named_parameters() if not p.requires_grad}
    assert trainable and all('lora_' in n for n in trainable)
    before={n:p.detach().cpu().clone() for n,p in trainable.items()};versions={n:p._version for n,p in frozen.items()}
    optimizer=torch.optim.AdamW(list(trainable.values()),lr=settings['learning_rate']);optimizer.zero_grad(set_to_none=True)
    torch.cuda.reset_peak_memory_stats();start=time.perf_counter();logs=[];nonzero_grad=False
    for step in range(settings['smoke_steps']):
        losses=[]
        for micro in range(settings['gradient_accumulation']):
            ex=examples[(step*settings['gradient_accumulation']+micro)%len(examples)]
            b={k:v.to('cuda') for k,v in collate([ex],tok.pad_token_id).items()}
            loss=model(**b).loss
            assert torch.isfinite(loss),'Nonfinite loss'
            (loss/settings['gradient_accumulation']).backward();losses.append(float(loss.detach()))
        grad_sq=sum(float(p.grad.float().square().sum()) for p in trainable.values() if p.grad is not None)
        assert math.isfinite(grad_sq) and grad_sq>0;nonzero_grad=True
        assert all(p.grad is None for p in frozen.values())
        torch.nn.utils.clip_grad_norm_(list(trainable.values()),1.0);optimizer.step();optimizer.zero_grad(set_to_none=True)
        item={'step':step+1,'loss':sum(losses)/len(losses),'gradient_l2':math.sqrt(grad_sq)};logs.append(item);print(role,item,flush=True)
    torch.cuda.synchronize();training_seconds=time.perf_counter()-start;peak=torch.cuda.max_memory_allocated()
    changed=[n for n,p in trainable.items() if not torch.equal(before[n],p.detach().cpu())]
    assert changed and all(p._version==versions[n] for n,p in frozen.items()),'Unexpected base parameter update'
    model.eval();model.gradient_checkpointing_disable();probe=torch.tensor([examples[0]['input_ids'][:min(len(examples[0]['input_ids']),128)]],device='cuda')
    with torch.inference_mode():expected=model(input_ids=probe,use_cache=False).logits[:,-1].float().cpu()
    directory=ROOT/f'runs/day1/smoke_{role}_adapter'
    if directory.exists():raise RuntimeError('Refuse to overwrite smoke checkpoint')
    model.save_pretrained(directory);write_json(directory/'training_ids.json',selected)
    del optimizer,b,loss,trainable,frozen,before,model;gc.collect();torch.cuda.empty_cache()
    reloaded,_=load(role,directory)
    with torch.inference_mode():actual=reloaded(input_ids=probe,use_cache=False).logits[:,-1].float().cpu()
    max_diff=float((expected-actual).abs().max());assert torch.allclose(expected,actual,atol=0.02,rtol=0.01)
    report={'role':role,'purpose':'smoke_only_not_formal_G1_or_V_update','unique_examples':len(examples),'excluded_or_overlength':excluded,'optimizer_steps':settings['smoke_steps'],'micro_batch':1,'gradient_accumulation':settings['gradient_accumulation'],'max_length':settings['max_length'],'finite_loss':True,'nonzero_gradient':nonzero_grad,'only_lora_trainable':True,'base_parameter_versions_unchanged':True,'base_gradients_absent':True,'changed_adapter_tensors':len(changed),'prompt_and_padding_masks_checked':True,'inference_training_prefix_match':True,'save_reload_passed':True,'reload_max_logit_difference':max_diff,'training_seconds':training_seconds,'peak_allocated_bytes':peak,'logs':logs,'adapter_path':str(directory),'label_counts':{x:sum(s['target']==x for s in selected) for x in ['A','B']} if role=='V' else None,'note':'Only 16 micro-batches; examples may cycle for the plumbing check. No claim of formal training data volume or model improvement.'}
    write_json(ROOT/f'runs/day1/smoke_{role}_report.json',report);print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
