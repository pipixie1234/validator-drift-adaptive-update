"""One-epoch response-only LoRA: G1 from G0, G2 continues G1 weights, no truth input."""
import argparse
import gc
import json
import math
import random
import time
from .core import ROOT,read_jsonl,recover_jsonl,write_json,write_jsonl,append_jsonl,ensure_run_manifest,collate,prompt_ids
from .runtime import load,model_identity,seed_all
from .formal_common import RUN,REPORT,PLAN,PROTOCOL,protocol,plan,inputs,messages,verify_fixed,gpu_lock,WorkBudget,progress,sha,utc

def groups(order,size):
    return [order[i:i+size] for i in range(0,len(order),size)]

def nested_tuple(value):return tuple(nested_tuple(x) for x in value) if isinstance(value,list) else value

def save_rng(path):
    import torch,numpy as np
    ns=np.random.get_state()
    write_json(path/'rng.json',{'python':random.getstate(),'numpy':[ns[0],ns[1].tolist(),int(ns[2]),int(ns[3]),float(ns[4])]})
    torch.save({'cpu':torch.get_rng_state(),'cuda':torch.cuda.get_rng_state_all()},path/'rng.pt')

def restore_rng(path):
    import torch,numpy as np
    js=json.loads((path/'rng.json').read_text());random.setstate(nested_tuple(js['python']))
    ns=js['numpy'];np.random.set_state((ns[0],np.array(ns[1],dtype=np.uint32),ns[2],ns[3],ns[4]))
    state=torch.load(path/'rng.pt',map_location='cpu',weights_only=True)
    torch.set_rng_state(state['cpu']);torch.cuda.set_rng_state_all(state['cuda'])

def train(target):
    import torch
    from peft import LoraConfig,get_peft_model,PeftModel
    assert target in ['G1','G2']
    with gpu_lock():
        verify_fixed();p=protocol();settings=p['training']['G'];impl=plan()['training_implementation_choices']
        directory=RUN/f'train/{target}';source=directory/'tokenized.jsonl';data=read_jsonl(source)
        selected=read_jsonl(directory/'selected.jsonl');assert 1<=len(data)==len(selected)<=512
        assert len({r['question_id'] for r in selected})==len(selected)
        assert all(not any(k in r for k in ['gold_answer','correct','reference']) for r in selected)
        base,_=model_identity('G');parent=RUN/'checkpoints/G1_SFT' if target=='G2' else None
        parent_hash=model_identity('G',parent)[1] if parent else None
        if parent:assert (RUN/'train/G1/report.json').exists()
        descriptor={'target':target,'original_G0_fingerprint':base,'parent_adapter_fingerprint':parent_hash,
            'parent_adapter':str(parent) if parent else None,'tokenized_sha256':sha(source),
            'selected_sha256':sha(directory/'selected.jsonl'),'protocol_sha256':sha(PROTOCOL),
            'execution_sha256':sha(PLAN),'runner_sha256':sha(ROOT/'src/pilot/formal_train.py'),
            'settings':settings,'implementation_choices':impl,'truth_input':False}
        ensure_run_manifest(directory/'training.manifest.json',descriptor)
        final=RUN/f'checkpoints/{target}_SFT';reportpath=directory/'report.json'
        if reportpath.exists():
            old=json.loads(reportpath.read_text());assert model_identity('G',final)[1]==old['final_adapter_fingerprint']
            assert old['save_reload_passed'];print(f'Cached completed {target}; no repeated training.',flush=True);return
        steps_dir=directory/'checkpoints';steps_dir.mkdir(exist_ok=True)
        complete=sorted(d for d in steps_dir.glob('step_*') if (d/'complete.json').exists())
        resume=complete[-1] if complete else None
        budget=WorkBudget(f'train/{target}');overall=time.perf_counter();seed_all(settings['seed'])
        model,tok=load('G');original_model=model
        if resume or parent:
            model=PeftModel.from_pretrained(model,str(resume or parent),is_trainable=True,local_files_only=True)
        else:
            model=get_peft_model(model,LoraConfig(task_type='CAUSAL_LM',r=settings['lora_r'],lora_alpha=settings['lora_alpha'],
                lora_dropout=settings['lora_dropout'],target_modules=protocol()['training']['V_smoke']['target_modules']))
            assert all(not bool(param.detach().count_nonzero()) for name,param in model.named_parameters() if 'lora_B' in name)
        del original_model
        trainable={n:param for n,param in model.named_parameters() if param.requires_grad}
        frozen={n:param for n,param in model.named_parameters() if not param.requires_grad}
        assert trainable and all('lora_' in n for n in trainable)
        base_versions={n:param._version for n,param in frozen.items()}
        before={n:param.detach().cpu().clone() for n,param in trainable.items()}
        order=list(range(len(data)));random.Random(settings['seed']).shuffle(order)
        batches=groups(order,settings['gradient_accumulation'])
        optimizer=torch.optim.AdamW(list(trainable.values()),lr=settings['learning_rate'],betas=tuple(impl['betas']),eps=impl['epsilon'],weight_decay=impl['weight_decay'])
        next_step=0;oldlogs=recover_jsonl(directory/'steps.jsonl')
        if resume:
            checkpoint=json.loads((resume/'complete.json').read_text());next_step=checkpoint['optimizer_steps']
            assert checkpoint['training_manifest_sha256']==sha(directory/'training.manifest.json')
            optimizer.load_state_dict(torch.load(resume/'optimizer.pt',map_location='cpu',weights_only=True))
            discarded=[r for r in oldlogs if r['step']>next_step]
            if discarded:write_jsonl(directory/f'rolled_back_attempt_{time.time_ns()}.jsonl',discarded)
            oldlogs=[r for r in oldlogs if r['step']<=next_step];write_jsonl(directory/'steps.jsonl',oldlogs)
        elif oldlogs:
            write_jsonl(directory/f'pre_checkpoint_attempt_{time.time_ns()}.jsonl',oldlogs);write_jsonl(directory/'steps.jsonl',[]);oldlogs=[]
        probe_ids=prompt_ids(tok,messages('G',inputs('B')[0]['question']))[:128]
        probe=torch.tensor([probe_ids],device='cuda')
        model.eval()
        with torch.inference_mode():initial_probe=model(input_ids=probe,use_cache=False).logits[:,-1].float().cpu()
        parent_probe_difference=None
        if parent and not resume:
            expected_parent=torch.load(RUN/'train/G1/probe_logits.pt',map_location='cpu',weights_only=True)
            parent_probe_difference=float((expected_parent-initial_probe).abs().max())
            assert torch.allclose(expected_parent,initial_probe,atol=.02,rtol=.01),'G2 must continue actual G1 adapter'
            ensure_run_manifest(directory/'lineage_initialization.json',{'parent_adapter_fingerprint':parent_hash,
                'parent_probe_max_logit_difference_before_update':parent_probe_difference})
        elif parent and resume:
            lineage=json.loads((directory/'lineage_initialization.json').read_text())
            assert lineage['parent_adapter_fingerprint']==parent_hash
            parent_probe_difference=lineage['parent_probe_max_logit_difference_before_update']
        model.train();model.config.use_cache=False
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
        if resume:restore_rng(resume)
        else:seed_all(settings['seed'])
        optimizer.zero_grad(set_to_none=True);torch.cuda.reset_peak_memory_stats();torch.cuda.synchronize();start=time.perf_counter();b=loss=None
        for step_index in range(next_step,len(batches)):
            budget.check();indices=batches[step_index];losses=[];input_count=target_count=0;tick=time.perf_counter()
            for index in indices:
                ex=data[index];assert len(ex['input_ids'])<=settings['max_length']
                assert all(t==-100 for t in ex['labels'][:ex['prefix_length']])
                b={k:t.to('cuda') for k,t in collate([ex],tok.pad_token_id).items()}
                loss=model(**b).loss;assert torch.isfinite(loss),f'Nonfinite loss at {target}:{step_index+1}'
                (loss/len(indices)).backward();losses.append(float(loss.detach()))
                input_count+=len(ex['input_ids']);target_count+=sum(t!=-100 for t in ex['labels'])
            grad_sq=sum(float(param.grad.float().square().sum()) for param in trainable.values() if param.grad is not None)
            assert math.isfinite(grad_sq) and grad_sq>0 and all(param.grad is None for param in frozen.values())
            torch.nn.utils.clip_grad_norm_(list(trainable.values()),impl['max_gradient_norm']);optimizer.step();optimizer.zero_grad(set_to_none=True)
            torch.cuda.synchronize();step=step_index+1
            log={'step':step,'mean_loss':sum(losses)/len(losses),'gradient_l2_before_clip':math.sqrt(grad_sq),
                'examples':len(indices),'input_tokens':input_count,'supervised_tokens':target_count,
                'seconds':time.perf_counter()-tick,'candidate_ids':[data[i]['candidate_id'] for i in indices]}
            append_jsonl(directory/'steps.jsonl',log);budget.charge(f'optimizer_step_{step}')
            print(f'{target} step {step}/{len(batches)} loss={log["mean_loss"]:.5f} samples={len(indices)}',flush=True)
            progress(f'train_{target}',optimizer_steps_complete=step,optimizer_steps_expected=len(batches),selected_examples=len(data))
            if step%impl['checkpoint_interval_optimizer_steps']==0 or step==len(batches):
                dest=steps_dir/f'step_{step:04d}';dest.mkdir(exist_ok=True)
                model.save_pretrained(dest);torch.save(optimizer.state_dict(),dest/'optimizer.pt');save_rng(dest)
                write_json(dest/'complete.json',{'optimizer_steps':step,'training_manifest_sha256':sha(directory/'training.manifest.json'),
                    'consumed_examples':sum(len(x) for x in batches[:step]),'saved_at':utc()})
                budget.charge(f'checkpoint_{step}')
        logs=read_jsonl(directory/'steps.jsonl');assert len(logs)==len(batches)
        assert sum(r['examples'] for r in logs)==len(data)
        consumed=[cid for r in logs for cid in r['candidate_ids']]
        assert len(set(consumed))==len(data) and set(consumed)=={r['candidate_id'] for r in selected}
        torch.cuda.synchronize();seconds=time.perf_counter()-start;peak=torch.cuda.max_memory_allocated()
        assert all(param._version==base_versions[n] for n,param in frozen.items())
        changed=sum(not torch.equal(before[n],param.detach().cpu()) for n,param in trainable.items())
        if next_step<len(batches):assert changed>0
        model.eval();model.gradient_checkpointing_disable()
        with torch.inference_mode():expected=model(input_ids=probe,use_cache=False).logits[:,-1].float().cpu()
        final.parent.mkdir(exist_ok=True)
        if final.exists():final.rename(final.with_name(final.name+f'_incomplete_{time.time_ns()}'))
        model.save_pretrained(final);write_json(final/'training_provenance.json',descriptor)
        write_json(directory/'training_ids.json',selected);torch.save(expected,directory/'probe_logits.pt')
        del optimizer,b,loss,trainable,frozen,before,model;gc.collect();torch.cuda.empty_cache()
        reloaded,_=load('G',final)
        with torch.inference_mode():actual=reloaded(input_ids=probe,use_cache=False).logits[:,-1].float().cpu()
        difference=float((expected-actual).abs().max());assert torch.allclose(expected,actual,atol=.02,rtol=.01)
        final_fp,final_ah=model_identity('G',final);assert final_fp==base
        result={'checkpoint':target+'_SFT','source_generator':'G0' if target=='G1' else 'G1','base_model_fingerprint':base,
            'parent_adapter_fingerprint':parent_hash,'parent_probe_max_logit_difference_before_update':parent_probe_difference,
            'final_adapter_fingerprint':final_ah,'adapter_path':str(final),'objective':'response_only_SFT',
            'selected_examples':len(data),'epochs':1,'optimizer_steps':len(logs),'micro_batch':1,'gradient_accumulation':8,
            'consumed_examples_once':True,'input_tokens':sum(r['input_tokens'] for r in logs),
            'supervised_tokens':sum(r['supervised_tokens'] for r in logs),'training_examples_truncated':0,
            'max_training_context':settings['max_length'],'max_actual_input_tokens':max(len(e['input_ids']) for e in data),
            'finite_losses_and_gradients':True,'base_frozen':True,'only_LoRA_updated':True,'changed_adapter_tensors_this_invocation':changed,
            'resumed_from_checkpoint':str(resume) if resume else None,'resumed_optimizer_step':next_step,
            'training_and_checkpoint_seconds_this_invocation':seconds,'step_compute_seconds_all':sum(r['seconds'] for r in logs),
            'total_seconds_this_invocation':time.perf_counter()-overall,'peak_allocated_bytes_this_invocation':peak,
            'save_reload_passed':True,'reload_max_logit_difference':difference,'truth_used_by_trainer':False,
            'protocol_sha256':sha(PROTOCOL),'training_manifest_sha256':sha(directory/'training.manifest.json'),
            'note':'Generator update under imperfect fixed-verifier feedback; improvement is an evaluation question, not an assumption.'}
        write_json(reportpath,result);budget.charge('save_reload_finalization');print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--target',choices=['G1','G2'],required=True);a=p.parse_args();train(a.target)

if __name__=='__main__':main()
