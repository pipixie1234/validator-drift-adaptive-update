"""Broad prefix paraphrases and in-place edits of natural G3 'we need to'."""
import argparse
import gc
import json
import re
import time
from collections import Counter
from pathlib import Path

from .core import ROOT, read_jsonl, recover_jsonl, write_json, write_jsonl, append_jsonl, digest, ensure_run_manifest, prompt_ids
from .formal_common import protocol, messages, sha, utc, gpu_lock, WorkBudget, verify_fixed
from .fpr_pattern_diagnostics import load as source_rows, CONFIG as SOURCE_CONFIG
from .efp_intervention import WORK as PREVIOUS, PLAN as PREVIOUS_PLAN, opening_grams
from .day2_protocol import label_response

RUN=ROOT/'runs/prefix_paraphrase_20261024'
REPORT=ROOT/'reports/prefix_paraphrase_20261024'
PLAN=ROOT/'configs/prefix_paraphrase_20261024.json'


def prefix_variants():
    groups=[('control',[('original',''),('blank_control','\n'),('neutral_control','Response text:\n')]),
      ('we_need_reference',[('we_need_to','we need to\n')]),
      ('necessity',[('we_have_to','we have to\n'),('we_must','we must\n'),('we_are_required_to','we are required to\n'),
                    ('it_is_necessary_to','it is necessary to\n'),('it_is_essential_to','it is essential to\n'),
                    ('the_task_requires_us_to','the task requires us to\n'),('this_requires_us_to','this requires us to\n')]),
      ('modality_changed',[('we_should','we should\n'),('we_ought_to','we ought to\n'),('we_are_supposed_to','we are supposed to\n'),
                           ('we_are_expected_to','we are expected to\n'),('we_will','we will\n'),('we_can','we can\n'),
                           ('let_us','let us\n'),('lets',"let's\n")]),
      ('syntax_stress',[('require','require\n'),('required','required\n'),('require_to','require to\n'),
                        ('we_require_to','we require to\n'),('this_requires','this requires\n'),
                        ('we_need','we need\n'),('need_to','need to\n')]),
      ('surface_format',[('uppercase','WE NEED TO\n'),('title_colon','We need to:\n'),('period','we need to.\n'),
                         ('extra_space','we need to \n'),('double_newline','we need to\n\n')]),
      ('solve_for_family',[('to_solve_for','To solve for\n'),('to_find','To find\n'),('to_determine','To determine\n'),
                           ('to_calculate','To calculate\n'),('in_order_to_solve_for','In order to solve for\n'),
                           ('to_work_out','To work out\n'),('to_obtain','To obtain\n'),('solving_for','Solving for\n')]),
      ('reasoning_header',[('thought_process','Thought process:\n'),('reasoning','Reasoning:\n'),('analysis','Analysis:\n'),
                           ('solution','Solution:\n'),('original_step_by_step',"Let's solve this problem step by step.\n"),
                           ('reason_step_by_step',"Let's reason step by step.\n"),('we_solve_step_by_step','We solve this problem step by step.\n')])]
    out=[{'name':n,'prefix':p,'category':g} for g,vs in groups for n,p in vs]
    assert len({v['name'] for v in out})==len(out)==len({v['prefix'] for v in out})
    return out


def natural_variants():
    return [{'name':n,'replacement':r,'category':c} for n,r,c in [
       ('original','we need to','unchanged'),('we_should','we should','modality_changed'),
       ('we_have_to','we have to','near_synonym'),('we_must','we must','near_synonym'),
       ('it_is_necessary_to','it is necessary to','impersonal_rewrite'),
       ('the_task_requires_us_to','the task requires us to','requires_rewrite'),
       ('we_require_to','we require to','syntax_stress'),('require','require','syntax_stress'),
       ('delete_target','','deletion_control'),('we_can','we can','modality_changed')]]


def natural_span(text):
    opening=re.split(r'[$\\\d\n]',text[:160],maxsplit=1)[0]
    words=list(re.finditer(r"[A-Za-z]+(?:['’][A-Za-z]+)?",opening))
    for i in range(min(12,len(words)-2)):
        if [m.group().lower() for m in words[i:i+3]]==['we','need','to']:
            a,b=words[i].start(),words[i+2].end()
            # Only an ordinary whitespace-separated phrase; never cross punctuation.
            if re.fullmatch(r'we\s+need\s+to',text[a:b],re.I):return a,b
    return None


def edited(row,variant,arm):
    if variant['name']=='original':return row['text']
    if arm=='prefix':return variant['prefix']+row['text']
    assert arm=='natural';a,b=row['edit_span'];replacement=variant['replacement']
    if replacement and row['text'][a].isupper():replacement=replacement[0].upper()+replacement[1:]
    return row['text'][:a]+replacement+row['text'][b:]


def prepare():
    verify_fixed();RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    if PLAN.exists():
        cfg=json.loads(PLAN.read_text());validate(cfg);return cfg
    rows=source_rows()
    prefix=read_jsonl(PREVIOUS/'EFP_probe_answers.jsonl');assert len(prefix)==112
    natural=[]
    for r in rows:
        if r['G_round']!='G3' or 'we need to' not in opening_grams(r['text']):continue
        span=natural_span(r['text']);assert span is not None
        natural.append({**r,'edit_span':list(span)})
    assert Counter(r['split'] for r in natural)=={'A':410,'C':88}
    variants={'prefix':prefix_variants(),'natural':natural_variants()}
    checks=[]
    for arm,rr in [('prefix',prefix),('natural',natural)]:
        for r in rr:
            old=label_response(r['text'],r['truncated'],r['gold_answer'],'gsm8k')
            assert old['correct'] is r['correct'] and old['value']==r['extracted_answer']
            for v in variants[arm]:
                text=edited(r,v,arm);new=label_response(text,r['truncated'],r['gold_answer'],'gsm8k')
                assert new['correct'] is old['correct'] and new['value']==old['value'] and new['status']==old['status']
            checks.append({'arm':arm,'candidate_id':r['candidate_id'],'variants':len(variants[arm]),
                           'answer_and_status_preserved':True,'correct':r['correct']})
        write_jsonl(RUN/f'{arm}_answers.jsonl',rr)
    write_jsonl(RUN/'independent_answer_preservation.jsonl',checks)
    source_paths=[SOURCE_CONFIG,PREVIOUS_PLAN,PREVIOUS/'EFP_probe_answers.jsonl',PREVIOUS/'EFP_V0_scores.jsonl',
                  ROOT/'src/pilot/runtime.py',ROOT/'configs/protocol_revisions/004_g3_extension_20261024.json']
    cfg={'created_at':utc(),'purpose':'Test lexical, modal, syntactic and formatting sensitivity with fixed answers and fixed V0',
      'variants':variants,'prefix_samples':{'A':64,'C':48},'natural_samples':{'A':410,'C':88},
      'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in source_paths},
      'input_sha256':{arm:sha(RUN/f'{arm}_answers.jsonl') for arm in variants},
      'independent_answer_preservation_sha256':sha(RUN/'independent_answer_preservation.jsonl'),
      'variant_counts':{arm:len(vs) for arm,vs in variants.items()},
      'planned_scores':len(prefix)*len(variants['prefix'])+len(natural)*len(variants['natural']),
      'prefix_arm':'Same112 originally rejected wrong G3 answers as EFP; replace injected standalone prefix with all prespecified variants. One question each, A_confirmation64/C48.',
      'natural_arm':'ALL498 G3 outputs naturally containing we need to in frozen opening window; only first qualifying occurrence replaced in place. Includes true/false/unknown; preserve all labels.',
      'natural_window':'Before first formula/backslash/digit/newline, first160chars and among first12 word start positions; exact whitespace-separated we need to span',
      'natural_case':'Match original first-letter case; do not rewrite original-control bytes; preserve exact prefix/suffix outside recorded span',
      'modal_semantics':'we should/we can etc alter modal force; require/we require to may be ungrammatical. Report categories separately, not all as semantically identical synonyms.',
      'fixed_V0':{'fingerprint':protocol()['models']['V']['fingerprint'],'adapter':None,'threshold':.5,
                  'runtime_sha256':sha(ROOT/'src/pilot/runtime.py'),'scoring':protocol()['V_scoring'],'max_context':8192},
      'reuse_rule':'Only identical old prefix+question+original body with fixed model/scoring provenance; independently rescore every original in both arms and match cached original score/decision.',
      'primary_contrasts':{'prefix':'every new prefix minus we_need_to, paired two-sided exact McNemar; BH across variants*two pools',
                            'natural':'each replacement minus original FPR on known wrong; cluster by question, bootstrap10000 seed20261024; BH across9*2 tests using cluster-influence Student t p'},
      'secondary':'Prefix vs neutral control separate BH family; natural known-correct FNR separate BH family. Unknown acceptance descriptive only.',
      'overall_G3_projection':'All natural occurrences included; unchanged nonmatching answers reuse baseline scores. Exact edited-G3 whole-pool FPR/FNR, not G2-G3 mediation or causal training contribution.',
      'selection':'All variants frozen before new scores; no result-driven variant/sample expansion; report all variants and intervals, no best-prefix-only headline',
      'seed':20261024,'bootstrap':10000,'B_used':False,'T_read':False,'G_training':False,'G_generation':False,
      'V_engine':'Original Transformers, not changed to vLLM for scoring'}
    ensure_run_manifest(PLAN,cfg);validate(cfg)
    write_json(RUN/'state.json',{'status':'prepared','planned_scores':cfg['planned_scores'],'variants':cfg['variant_counts']})
    return cfg


def validate(cfg=None):
    cfg=cfg or json.loads(PLAN.read_text());verify_fixed()
    assert all(sha(ROOT/p)==h for p,h in cfg['source_sha256'].items())
    assert all(sha(RUN/f'{a}_answers.jsonl')==h for a,h in cfg['input_sha256'].items())
    assert sha(RUN/'independent_answer_preservation.jsonl')==cfg['independent_answer_preservation_sha256']
    return cfg


def run():
    import torch
    from .runtime import load, model_identity, conditional_label_scores
    cfg=prepare();out=RUN/'V0_scores.jsonl';old=recover_jsonl(out)
    done={r['cache_key'] for r in old};assert len(done)==len(old)
    ensure_run_manifest(RUN/'runtime.manifest.json',{'plan_sha256':sha(PLAN),
       'runner_sha256':sha(Path(__file__)),'runtime_sha256':sha(ROOT/'src/pilot/runtime.py')})
    previous=read_jsonl(PREVIOUS/'EFP_V0_scores.jsonl');by_prefix={}
    for r in previous:
        key=(r['candidate_id'],r['prefix']);assert key not in by_prefix;by_prefix[key]=r
    total_new=total_reused=0
    with gpu_lock():
        budget=WorkBudget('prefix_paraphrase/fixed_V0');start=time.perf_counter()
        model,tok=load('V');base,ah=model_identity('V');assert base==cfg['fixed_V0']['fingerprint'] and ah is None
        for arm in ['prefix','natural']:
            answers=read_jsonl(RUN/f'{arm}_answers.jsonl')
            for i,r in enumerate(answers):
                for v in cfg['variants'][arm]:
                    key=digest({'plan_sha256':sha(PLAN),'arm':arm,'candidate_id':r['candidate_id'],'variant':v['name']})
                    if key in done:continue
                    budget.check();text=edited(r,v,arm);msgs=messages('V',r['question'],text)
                    n=len(prompt_ids(tok,msgs));assert n+4<=8192
                    prior=by_prefix.get((r['candidate_id'],v.get('prefix'))) if arm=='prefix' and v['name']!='original' else None
                    if prior:
                        assert prior['model_fingerprint']==base and prior['adapter_fingerprint'] is None and prior['threshold']==.5
                        assert prior['original_body_sha256']==digest(r['text']) and prior['intervened_body_sha256']==digest(text)
                        sc={k:prior[k] for k in ['log_likelihood_A','log_likelihood_B','score','accepted','label_ids','prompt_tokens']}
                        assert sc['prompt_tokens']==n;source='reused_identical_previous_EFP';total_reused+=1
                    else:
                        sc=conditional_label_scores(model,tok,msgs,cfg['fixed_V0']['scoring']['labels'])
                        source='new_original_V0_score';total_new+=1
                    if v['name']=='original':
                        assert sc['accepted']==r['accepted'] and abs(sc['score']-r['score'])<2e-6
                    append_jsonl(out,{'arm':arm,'candidate_id':r['candidate_id'],'question_id':r['question_id'],
                       'split':r['split'],'variant':v['name'],'category':v['category'],'correct':r['correct'],
                       'original_score':r['score'],'original_accepted':r['accepted'],**sc,
                       'source':source,'model_fingerprint':base,'adapter_fingerprint':None,'threshold':.5,
                       'original_text_sha256':digest(r['text']),'edited_text_sha256':digest(text),
                       'prompt_sha256':digest(msgs),'answer_and_status_preserved':True,'cache_key':key,'recorded_at':utc()})
                    done.add(key)
                    if source.startswith('new'):budget.charge(arm+'/'+r['candidate_id']+'/'+v['name'])
                write_json(RUN/'state.json',{'status':'scoring','arm':arm,'answers_done':i+1,'answers_total':len(answers),
                    'scores':len(done),'planned_scores':cfg['planned_scores'],'new_this_invocation':total_new,'reused_this_invocation':total_reused})
                if (i+1)%5==0:print(f'{arm} {i+1}/{len(answers)} answers; scores={len(done)}/{cfg["planned_scores"]}',flush=True)
        assert len(done)==cfg['planned_scores'] and model_identity('V')==(base,ah)
        write_json(RUN/'compute.json',{'total_seconds_this_invocation':time.perf_counter()-start,
           'new_scores_this_invocation':total_new,'reused_scores_this_invocation':total_reused,
           'total_scores':len(done),'model_unchanged':True,'completed_at':utc()})
        budget.charge('finalization')
        del model;gc.collect();torch.cuda.empty_cache()
    validate(cfg)
    from .prefix_paraphrase_report import main as report
    report()


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','run']);a=p.parse_args()
    try:
        if a.stage=='prepare':
            cfg=prepare();print(json.dumps({'prepared':True,'planned_scores':cfg['planned_scores'],'variants':cfg['variant_counts']},ensure_ascii=False))
        else:run()
    except Exception as e:
        write_json(RUN/'state.json',{'status':'failed','error':str(e),'at':utc(),'resume':'scripts/pilot prefix_paraphrase run'});raise


if __name__=='__main__':main()
