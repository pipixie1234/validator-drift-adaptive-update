"""Independent Vrand/Vtarget updates from V0, B-only supervision, cached C eval."""
import argparse
import gc
import json
import math
import random
import shutil
import time
from collections import Counter
from pathlib import Path

from .core import ROOT,read_jsonl,recover_jsonl,write_json,write_jsonl,append_jsonl,digest,ensure_run_manifest,training_example,collate
from .formal_common import protocol,messages,verify_fixed,sha,utc,gpu_lock,WorkBudget
from .runtime import load,model_identity,conditional_label_scores,seed_all
from .formal_train import groups,save_rng,restore_rng
from .g3_common import RUN as LATE,pool
from .day2_protocol import label_response

RUN=ROOT/'runs/verifier_update_20261025'
REPORT=ROOT/'reports/verifier_update_20261025'
PLAN=ROOT/'configs/verifier_update_20261025.json'
ARMS=['Vrand','Vtarget']
QUOTAS={('G0',True):32,('G0',False):32,('G1',True):32,('G1',False):32,('G2',True):64,('G2',False):64}


def length_bin(n):
    return 'le512' if n<=512 else '513_1024' if n<=1024 else '1025_2048' if n<=2048 else 'gt2048'


def protect_existing():
    path=RUN/'preserved_existing_files.json'
    if path.exists():return json.loads(path.read_text())
    files={}
    for directory in ['runs','reports','configs']:
        for p in sorted((ROOT/directory).rglob('*')):
            if not p.is_file() or p.is_symlink():continue
            if p==PLAN or RUN in p.parents or REPORT in p.parents or p.name.startswith('verifier_update_20261025'):continue
            if p==ROOT/'runs/formal/work_ledger.jsonl' or p.name=='.gpu.lock':continue
            files[str(p.relative_to(ROOT))]={'sha256':sha(p),'bytes':p.stat().st_size}
    write_json(path,files)
    shutil.copyfile(ROOT.parent/'一周前置实验方案_20261020.md',RUN/'prior_research_plan.md')
    return files


def join_source(directory,g,split,allowed=None,view=None):
    paths=[directory/f'{prefix}{g}_{split}.jsonl' for prefix in ['','labels_','V0_']]
    raw,labels,scores=[read_jsonl(p) for p in paths]
    ll={r['candidate_id']:r for r in labels};vv={r['candidate_id']:r for r in scores}
    assert len(raw)==len(ll)==len(vv) and {r['candidate_id'] for r in raw}==set(ll)==set(vv)
    out=[]
    for r in raw:
        if allowed is not None and r['question_id'] not in allowed:continue
        l=ll[r['candidate_id']];v=vv[r['candidate_id']]
        assert r['question_id']==l['question_id']==v['question_id']
        assert v['model_fingerprint']==protocol()['models']['V']['fingerprint'] and v['adapter_fingerprint'] is None and v['threshold']==.5
        assert v['accepted']==(v['score']>=.5) and v['prompt_sha256']==digest(messages('V',r['question'],r['text']))
        out.append({**r,**l,'split':split,'G_round':g,'view':view or g,'V0_accepted':v['accepted'],
                    'V0_score':v['score'],'V0_log_likelihood_A':v['log_likelihood_A'],'V0_log_likelihood_B':v['log_likelihood_B']})
    return out,paths


def targeted_selection(rows,quotas=QUOTAS):
    selected=[];seen_prompts=set()
    for (g,y),n in quotas.items():
        candidates=[r for r in rows if r['G_round']==g and r['correct'] is y]
        seen_q=set();chosen=[]
        def take(candidates,limit):
            for r in sorted(candidates,key=lambda r:digest('Vtarget-20261025:'+r['candidate_id'])):
                if len(chosen)>=limit:break
                if r['question_id'] in seen_q or r['prompt_hash'] in seen_prompts:continue
                chosen.append(r);seen_q.add(r['question_id']);seen_prompts.add(r['prompt_hash'])
        take([r for r in candidates if r['V0_accepted']!=y],math.floor(.75*n))
        take([r for r in candidates if r['V0_accepted']==y],n)
        take(candidates,n)
        assert len(chosen)==n,(g,y,len(chosen),n)
        selected.extend(chosen)
    return selected


def matched_random(rows,target,max_attempts=10000):
    """No V scores or correctness-of-V enter this length-conditional random arm."""
    rng=random.Random(20261025)
    keys=[(r['G_round'],r['correct'],r['length_bin']) for r in target]
    counts=Counter(keys);pools={k:[r for r in rows if (r['G_round'],r['correct'],r['length_bin'])==k] for k in counts}
    target_tokens=sum(r['input_tokens'] for r in target)
    for attempt in range(1,max_attempts+1):
        sampled={};seen_prompts=set();feasible=True
        for key,n in counts.items():
            candidates=pools[key].copy();rng.shuffle(candidates);chosen=[];seen_q=set()
            # Enforce one answer/question within each G,label across all length bins.
            for oldkey,oldrows in sampled.items():
                if oldkey[:2]==key[:2]:seen_q.update(r['question_id'] for r in oldrows)
            for r in candidates:
                if r['question_id'] in seen_q or r['prompt_hash'] in seen_prompts:continue
                chosen.append(r);seen_q.add(r['question_id']);seen_prompts.add(r['prompt_hash'])
                if len(chosen)==n:break
            if len(chosen)!=n:feasible=False;break
            sampled[key]=chosen
        if not feasible:continue
        chosen=[sampled[k].pop() for k in keys]
        ratio=sum(r['input_tokens'] for r in chosen)/target_tokens
        if abs(ratio-1)<=.01:return chosen,{'attempt':attempt,'input_token_ratio_vs_target':ratio,'tolerance':.01}
    raise RuntimeError('No length-matched random selection; preserve outputs and revise protocol before training, never silently widen tolerance')


def validate():
    cfg=json.loads(PLAN.read_text());verify_fixed()
    assert all(sha(ROOT/n)==h for n,h in cfg['sources_sha256'].items())
    assert all(sha(RUN/n)==h for n,h in cfg['inputs_sha256'].items())
    return cfg


def prepare():
    RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    if PLAN.exists():return validate()
    verify_fixed();protect_existing()
    from transformers import AutoTokenizer
    tok=AutoTokenizer.from_pretrained(protocol()['models']['V']['path'],local_files_only=True)
    if tok.pad_token_id is None:tok.pad_token=tok.eos_token
    rows=[];paths=[];exclusions=Counter();all_labelled=0
    for g in ['G0','G1','G2']:
        rr,pp=join_source(ROOT/'runs/formal',g,'B');paths+=pp;all_labelled+=len(rr)
        for r in rr:
            if r['correct'] is None:exclusions[g+':unknown']+=1;continue
            if r['truncated']:exclusions[g+':truncated']+=1;continue
            old=label_response(r['text'],False,r['gold_answer'],'gsm8k')
            assert old['correct'] is r['correct'] and old['value']==r['extracted_answer']
            ex=training_example(tok,messages('V',r['question'],r['text']),'A' if r['correct'] else 'B',8192)
            if ex is None:exclusions[g+':over_context']+=1;continue
            n=len(ex['input_ids']);supervised=sum(x!=-100 for x in ex['labels'])
            assert supervised==3 and tok.decode([x for x in ex['labels'] if x!=-100])==('A' if r['correct'] else 'B')+'<|im_end|>\n'
            rows.append({**r,'input_tokens':n,'supervised_tokens':supervised,'length_bin':length_bin(n),
                         'prompt_hash':digest(messages('V',r['question'],r['text']))})
    target=targeted_selection(rows);rand,matching=matched_random(rows,target)
    train_q={r['question_id'] for r in rows};evalrows=[]
    cids={r['question_id'] for r in pool('C')}
    assert len(cids)==95 and not cids&train_q
    for view,g,directory in [('G0_TF','G0',ROOT/'runs/formal'),('G1_TF','G1',ROOT/'runs/formal'),
                             ('G2_TF','G2',ROOT/'runs/formal'),('G2_vLLM','G2',LATE),('G3_vLLM','G3',LATE)]:
        rr,pp=join_source(directory,g,'C',cids,view);assert len(rr)==380;evalrows+=rr;paths+=pp
    assert len(evalrows)==1900 and len({r['candidate_id'] for r in evalrows})==1900
    write_jsonl(RUN/'B_eligible.jsonl',rows);write_jsonl(RUN/'C_evaluation.jsonl',evalrows)
    summaries={}
    for arm,chosen in [('Vrand',rand),('Vtarget',target)]:
        d=RUN/arm;d.mkdir(exist_ok=True);tokenized=[]
        for r in chosen:
            ex=training_example(tok,messages('V',r['question'],r['text']),'A' if r['correct'] else 'B',8192)
            assert ex is not None and len(ex['input_ids'])==r['input_tokens']
            assert all(x==-100 for x in ex['labels'][:ex['prefix_length']])
            assert ex['input_ids'][ex['prefix_length']:]==[x for x in ex['labels'] if x!=-100]
            tokenized.append({'candidate_id':r['candidate_id'],**ex})
        assert len(chosen)==len({r['candidate_id'] for r in chosen})==len({r['prompt_hash'] for r in chosen})==256
        assert len({(r['G_round'],r['correct'],r['question_id']) for r in chosen})==256
        write_jsonl(d/'selected.jsonl',chosen);write_jsonl(d/'tokenized.jsonl',tokenized)
        summaries[arm]={'responses':256,'questions':len({r['question_id'] for r in chosen}),
            'input_tokens':sum(r['input_tokens'] for r in chosen),'supervised_tokens':sum(r['supervised_tokens'] for r in chosen),
            'decision_label_tokens':256,'V0_mistakes':sum(r['V0_accepted']!=r['correct'] for r in chosen),
            'by_round_label':{g+':'+str(y):{'count':sum(r['G_round']==g and r['correct'] is y for r in chosen),
                'V0_errors':sum(r['G_round']==g and r['correct'] is y and r['V0_accepted']!=y for r in chosen)} for g,y in QUOTAS},
            'max_input_tokens':max(r['input_tokens'] for r in chosen)}
    assert summaries['Vrand']['supervised_tokens']==summaries['Vtarget']['supervised_tokens']==768
    paths += [ROOT/'src/pilot/runtime.py',Path(__file__),ROOT/'src/pilot/core.py',ROOT/'src/pilot/formal_train.py']
    src={str(p.relative_to(ROOT)):sha(p) for p in paths}
    cfg={'created_at':utc(),'purpose':'V0 vs independently initialized Vrand/Vtarget; B-only natural-label SFT, adaptation and historical retention on frozen C outputs',
        'sources_sha256':src,'inputs_sha256':{str(p.relative_to(RUN)):sha(p) for p in RUN.rglob('*.jsonl')},
        'models':protocol()['models'],'fixed_scoring':protocol()['V_scoring'],'prompts':protocol()['prompts'],
        'training':{'epochs':1,'examples_each':256,'optimizer_steps':32,'micro_batch':1,'gradient_accumulation':8,
            'learning_rate':1e-4,'optimizer':'AdamW','betas':[.9,.999],'epsilon':1e-8,'weight_decay':.01,'max_gradient_norm':1.,
            'r':16,'alpha':32,'dropout':.05,'target_modules':protocol()['training']['V_smoke']['target_modules'],
            'max_context':8192,'gradient_checkpointing':True,'seed':42,'checkpoint_every_steps':16,
            'target':'A/B + template im_end and newline, prompt/padding masked;3 supervised tokens/answer, no truncation',
            'initialization':'Each arm fresh V0 + same-seed new LoRA, no development/smoke/G adapter or other-arm inheritance'},
        'selection':{'training_split':'B only; all2400 candidate independent labels counted in acquisition cost',
            'round_counts':{'G0':64,'G1':64,'G2':128},'truth_counts':{'correct':128,'wrong':128},
            'target':'Within each G,label request75% V0 errors and25% correct V0 decisions, fixed hash order. FN scarcity filled with valid controls. One answer per G,label,question and no repeated full prompt within arm.',
            'random':'Random within exactly matched G,label,V-input-length bins, no V scores/decisions used; same one-per-G,label,question and prompt dedup. First overall input-token match within1%. Conditional random, not unstratified.',
            'pair_order':'Match G,label,length-bin at every row; common seeded shuffle gives same strata order per optimizer step',
            'random_matching':matching,'summaries':summaries,'excluded':dict(exclusions),'label_acquisition_candidates':all_labelled,
            'eligible_known_candidates':len(rows),'same_B_question_pool':True,'overall_question_coverage_not_exactly_matched':True},
        'evaluation':{'split':'C95 repeated-use development; no C training or hyperparameter search',
            'views':['G0_TF','G1_TF','G2_TF','G2_vLLM','G3_vLLM'],'answers_each':380,
            'primary':'Vtarget-Vrand on G2_TF FPR and FNR, paired whole-question bootstrap10000; G0_TF retention/history',
            'secondary':'G1_TF, G2_vLLM and held-out-generation-stage G3_vLLM; all V evaluated on identical cached answers',
            'engine_limit':'B G0/G1/G2 and primary C G0/G1/G2 from original Transformers. Later G2/G3 vLLM transfer separate; do not join pure training curve across engines.',
            'metrics':['FPR','FNR','balanced_accuracy','V_accuracy','accepted_error_FDR','retention_all','AUROC','Brier_proxy'],
            'score_limit':'Original conditional full A/B likelihood softmax proxy, uncalibrated; threshold0.5 unchanged',
            'T':'Still sealed, not evaluated or used for selection; final-test experiment requires a separate frozen execution'},
        'seed':20261025,'bootstrap':10000,'G_training':False,'G_generation':False,'T_decoded':False,
        'preservation':'Existing runs/reports/configs not overwritten or deleted; complete hash inventory saved. Shared GPU work ledger append is intentionally excluded.',
        'limits':['One training seed, one update size, no test-set claim','B reference labels not fully human certified; unknowns never negatives',
                  'Selection changes question coverage and context within matched strata; not a proof of a unique learning mechanism']}
    ensure_run_manifest(PLAN,cfg);validate();write_json(RUN/'state.json',{'status':'prepared','selection':summaries,'at':utc()})
    print(json.dumps(summaries,ensure_ascii=False,indent=2),flush=True);return cfg


def train(arm):
    import torch
    from peft import LoraConfig,get_peft_model,PeftModel
    cfg=validate();s=cfg['training'];d=RUN/arm;final=d/'adapter';report=d/'training_report.json'
    if report.exists():
        old=json.loads(report.read_text());assert model_identity('V',final)[1]==old['adapter_fingerprint'];return
    with gpu_lock():
        budget=WorkBudget('verifier_update/train/'+arm);tick=time.perf_counter();seed_all(s['seed'])
        data=read_jsonl(d/'tokenized.jsonl');selected=read_jsonl(d/'selected.jsonl');base,_=model_identity('V')
        ensure_run_manifest(d/'training.manifest.json',{'plan_sha256':sha(PLAN),'tokenized_sha256':sha(d/'tokenized.jsonl'),
             'selected_sha256':sha(d/'selected.jsonl'),'base_V0_fingerprint':base,'runner_sha256':sha(Path(__file__))})
        checkpoints=d/'checkpoints';checkpoints.mkdir(exist_ok=True)
        complete=sorted(p for p in checkpoints.glob('step_*') if (p/'complete.json').exists());resume=complete[-1] if complete else None
        model,tok=load('V');seed_all(s['seed'])
        if resume:model=PeftModel.from_pretrained(model,str(resume),is_trainable=True,local_files_only=True)
        else:model=get_peft_model(model,LoraConfig(task_type='CAUSAL_LM',r=s['r'],lora_alpha=s['alpha'],lora_dropout=s['dropout'],target_modules=s['target_modules']))
        params={n:p for n,p in model.named_parameters() if p.requires_grad};frozen={n:p for n,p in model.named_parameters() if not p.requires_grad}
        assert params and all('lora_' in n for n in params)
        before={n:p.detach().cpu().clone() for n,p in params.items()};versions={n:p._version for n,p in frozen.items()}
        if not resume:
            initial_hash=digest({n:__import__('hashlib').sha256(p.detach().cpu().numpy().tobytes()).hexdigest() for n,p in params.items()})
            ensure_run_manifest(d/'initial_adapter.json',{'hash':initial_hash,'fresh_V0':True,'seed':s['seed']})
            other=RUN/('Vtarget' if arm=='Vrand' else 'Vrand')/'initial_adapter.json'
            if other.exists():assert json.loads(other.read_text())['hash']==initial_hash
            model.eval()
            for r in selected[:2]:
                point=conditional_label_scores(model,tok,messages('V',r['question'],r['text']))
                assert point['accepted']==r['V0_accepted'] and abs(point['score']-r['V0_score'])<2e-6
        order=list(range(len(data)));random.Random(s['seed']).shuffle(order);batches=groups(order,s['gradient_accumulation'])
        optimizer=torch.optim.AdamW(list(params.values()),lr=s['learning_rate'],betas=tuple(s['betas']),eps=s['epsilon'],weight_decay=s['weight_decay'])
        logs=recover_jsonl(d/'steps.jsonl');start_step=0
        if resume:
            saved=json.loads((resume/'complete.json').read_text());assert saved['manifest']==sha(d/'training.manifest.json');start_step=saved['step']
            optimizer.load_state_dict(torch.load(resume/'optimizer.pt',map_location='cpu',weights_only=True))
        if any(l['step']>start_step for l in logs):
            write_jsonl(d/f'rolled_back_logs_{time.time_ns()}.jsonl',logs);logs=[l for l in logs if l['step']<=start_step];write_jsonl(d/'steps.jsonl',logs)
        model.train();model.config.use_cache=False;model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant':False})
        optimizer.zero_grad(set_to_none=True);torch.cuda.reset_peak_memory_stats()
        if not resume:
            longest=max(data,key=lambda r:len(r['input_ids']));b={k:v.to('cuda') for k,v in collate([longest],tok.pad_token_id).items()}
            loss=model(**b).loss;assert torch.isfinite(loss);loss.backward();optimizer.zero_grad(set_to_none=True)
            write_json(d/'longest_backward_probe.json',{'candidate_id':longest['candidate_id'],'input_tokens':len(longest['input_ids']),
                'loss':float(loss.detach()),'optimizer_steps':0,'no_truncation':True});del b,loss
            seed_all(s['seed'])
        else:restore_rng(resume)
        for step_index in range(start_step,len(batches)):
            budget.check();indices=batches[step_index];losses=[];start=time.perf_counter()
            for ix in indices:
                ex=data[ix];b={k:v.to('cuda') for k,v in collate([ex],tok.pad_token_id).items()}
                loss=model(**b).loss;assert torch.isfinite(loss);(loss/len(indices)).backward();losses.append(float(loss.detach()))
            norm=math.sqrt(sum(float(p.grad.float().square().sum()) for p in params.values() if p.grad is not None))
            assert norm>0 and math.isfinite(norm) and all(p.grad is None for p in frozen.values())
            torch.nn.utils.clip_grad_norm_(list(params.values()),s['max_gradient_norm']);optimizer.step();optimizer.zero_grad(set_to_none=True)
            step=step_index+1;log={'step':step,'mean_loss':sum(losses)/len(losses),'gradient_L2':norm,'examples':len(indices),
                 'input_tokens':sum(len(data[ix]['input_ids']) for ix in indices),'supervised_tokens':sum(sum(x!=-100 for x in data[ix]['labels']) for ix in indices),
                 'candidate_ids':[data[ix]['candidate_id'] for ix in indices],'seconds':time.perf_counter()-start}
            append_jsonl(d/'steps.jsonl',log);budget.charge('step_'+str(step))
            write_json(RUN/'state.json',{'status':'training','arm':arm,'optimizer_step':step,'total_steps':len(batches),'at':utc()})
            print(f'{arm} train {step}/{len(batches)} loss={log["mean_loss"]:.5f}',flush=True)
            if step%s['checkpoint_every_steps']==0 or step==len(batches):
                dest=checkpoints/f'step_{step:04d}';dest.mkdir(exist_ok=True);model.save_pretrained(dest)
                torch.save(optimizer.state_dict(),dest/'optimizer.pt');save_rng(dest)
                write_json(dest/'complete.json',{'step':step,'manifest':sha(d/'training.manifest.json'),'at':utc()});budget.charge('checkpoint')
        logs=read_jsonl(d/'steps.jsonl');assert len(logs)==32 and sum(l['examples'] for l in logs)==256 and sum(l['supervised_tokens'] for l in logs)==768
        consumed=[cid for l in logs for cid in l['candidate_ids']];assert len(set(consumed))==256 and set(consumed)=={r['candidate_id'] for r in selected}
        assert all(p._version==versions[n] for n,p in frozen.items())
        changed=sum(not torch.equal(before[n],p.detach().cpu()) for n,p in params.items())
        if start_step<32:assert changed>0
        model.eval();model.gradient_checkpointing_disable()
        probe=selected[:2];expected=[conditional_label_scores(model,tok,messages('V',r['question'],r['text'])) for r in probe]
        if final.exists():final.rename(d/f'adapter_incomplete_{time.time_ns()}')
        model.save_pretrained(final);peak=torch.cuda.max_memory_allocated()
        del optimizer,b,loss,params,frozen,before,model;gc.collect();torch.cuda.empty_cache()
        reloaded,tok=load('V',final);actual=[conditional_label_scores(reloaded,tok,messages('V',r['question'],r['text'])) for r in probe]
        assert all(abs(a['score']-e['score'])<2e-6 and abs(a['log_likelihood_A']-e['log_likelihood_A'])<2e-4 and a['accepted']==e['accepted'] for a,e in zip(actual,expected))
        fp,ad=model_identity('V',final);assert fp==base
        write_json(report,{'arm':arm,'base_V0_fingerprint':base,'adapter_fingerprint':ad,'adapter_path':str(final),
            'fresh_independent_V0_initialization':True,'examples':256,'epochs':1,'steps':32,'supervised_tokens':768,
            'decision_label_tokens':256,'input_tokens':sum(l['input_tokens'] for l in logs),'base_parameters_unchanged':True,
            'only_lora_updated':True,'save_reload_passed':True,'changed_adapter_tensors_this_invocation':changed,'resume':str(resume) if resume else None,
            'max_context':8192,'max_actual_context':max(len(e['input_ids']) for e in data),'truncated_training_examples':0,
            'peak_allocated_bytes':peak,'seconds_this_invocation':time.perf_counter()-tick,'at':utc()})
        budget.charge('save_reload');del reloaded;gc.collect();torch.cuda.empty_cache()


def evaluate(arm):
    import torch
    cfg=validate();d=RUN/arm;adapter=d/'adapter';training=json.loads((d/'training_report.json').read_text())
    fp,ad=model_identity('V',adapter);assert ad==training['adapter_fingerprint']
    rows=read_jsonl(RUN/'C_evaluation.jsonl');path=d/'C_scores.jsonl';saved=recover_jsonl(path)
    done={r['cache_key'] for r in saved};assert len(done)==len(saved)
    ensure_run_manifest(d/'evaluation.manifest.json',{'plan_sha256':sha(PLAN),'evaluation_input_sha256':sha(RUN/'C_evaluation.jsonl'),
        'model_fingerprint':fp,'adapter_fingerprint':ad,'runtime_sha256':sha(ROOT/'src/pilot/runtime.py')})
    cache={s['prompt_sha256']:s for s in saved};fresh=reused=0
    with gpu_lock():
        budget=WorkBudget('verifier_update/evaluate/'+arm);tick=time.perf_counter();model,tok=load('V',adapter)
        for i,r in enumerate(rows):
            key=digest({'plan':sha(PLAN),'arm':arm,'candidate_id':r['candidate_id']})
            if key in done:continue
            budget.check();msgs=messages('V',r['question'],r['text']);ph=digest(msgs);old=cache.get(ph)
            if old:
                sc={k:old[k] for k in ['score','accepted','log_likelihood_A','log_likelihood_B','label_ids','prompt_tokens']};reused+=1
            else:sc=conditional_label_scores(model,tok,msgs);fresh+=1
            assert sc['prompt_tokens']+4<=8192 and sc['accepted']==(sc['score']>=.5)
            s={'arm':arm,'view':r['view'],'G_round':r['G_round'],'candidate_id':r['candidate_id'],'question_id':r['question_id'],
               'split':'C','correct':r['correct'],**sc,'cache_key':key,'prompt_sha256':ph,'text_sha256':digest(r['text']),
               'model_fingerprint':fp,'adapter_fingerprint':ad,'threshold':.5,'source':'same_adapter_exact_full_prompt' if old else 'new_score','at':utc()}
            append_jsonl(path,s);done.add(key);cache[ph]=s
            if not old:budget.charge(r['candidate_id'])
            if (i+1)%25==0:
                print(f'{arm} evaluate {i+1}/{len(rows)}',flush=True)
                write_json(RUN/'state.json',{'status':'evaluating','arm':arm,'done':len(done),'total':len(rows),'at':utc()})
        assert len(done)==len(rows) and model_identity('V',adapter)==(fp,ad)
        write_json(d/'evaluation_compute.json',{'logical_scores':len(done),'new_this_invocation':fresh,'reused_this_invocation':reused,'seconds_this_invocation':time.perf_counter()-tick})
        budget.charge('finalize');del model;gc.collect();torch.cuda.empty_cache()


def run():
    prepare()
    for arm in ARMS:train(arm)
    for arm in ARMS:evaluate(arm)
    from .verifier_update_report import main as report
    report()


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run']);a=p.parse_args()
    try:prepare() if a.stage=='prepare' else run()
    except Exception as e:
        RUN.mkdir(exist_ok=True);write_json(RUN/'state.json',{'status':'failed','error':str(e),'at':utc(),'resume':'scripts/pilot verifier_update run'});raise


if __name__=='__main__':main()
