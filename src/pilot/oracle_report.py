"""Independent final-answer evaluation and matched oracle-control reporting."""
import copy
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

from .core import ROOT, read_jsonl, write_json, write_jsonl
from .day2_protocol import label_response, numeric, VERSION
from .formal_common import RUN as FORMAL, REPORT as FORMAL_REPORT, inputs, sha, utc
from .explanation_probes import SUSPECT
from .v0_drift_analysis import analyze, clean, csv_write, interval
from .oracle_control import RUN, REPORT, CONFIG, ARMS, validate_plan
from .oracle_evaluate import generated, scored

MODELS=['G0',*ARMS]
SCENARIOS=['C95_exclude_reference_suspect','C96_frozen_original','C96_reference1_overlay']
METRICS=['G_accuracy_verified','G_accuracy_lower_all','unknown_fraction','FPR','FNR',
         'retention_all','accepted_error_FDR','AUROC','mean_output_tokens']


def freeze_analysis_plan():
    from .core import ensure_run_manifest
    REPORT.mkdir(exist_ok=True)
    ensure_run_manifest(REPORT/'analysis_plan.json',{
        'study_sha256':sha(CONFIG),'analysis_code_sha256':sha(Path(__file__)),
        'primary_contrast':'Oracle_SFT minus Vmatched_SFT; both fresh G0 at matched training budget',
        'primary_G_metric':'G_accuracy_verified; always report correct/all and unknown bounds alongside',
        'scenarios':SCENARIOS,'primary_scenario':SCENARIOS[0],
        'secondary_baseline':'Each new arm minus original cached G0 under the same sampler, same question seeds and complete contexts',
        'old_formal_G1_is_not_matched_comparator':True,
        'bootstrap':{'unit':'whole question, four candidates together','replicates':2000,'seed':20261022,
                     'paired':True,'CI':'Exploratory pointwise percentile95; no multiplicity adjustment, no training-seed uncertainty'},
        'report_unknown_as_wrong':False,'T_read':False,'B_read':False,
        'source_reference_issue':SUSPECT,'reference1_is_separate_overlay_not_formal_relabelling':True})


def load_control(arm):
    g=read_jsonl(generated(arm));v={r['candidate_id']:r for r in read_jsonl(scored(arm))}
    pool={r['question_id']:r for r in inputs('C')}
    assert len(g)==len(v)==384
    assert len({(r['question_id'],r['candidate_index']) for r in g})==384
    gold={r['question_id']:r['gold_answer'] for r in read_jsonl(ROOT/'data/dapo/labels/dapo100.jsonl')}
    rows=[];labels=[]
    for r in g:
        assert r['question']==pool[r['question_id']]['question']
        vr=v[r['candidate_id']];assert vr['threshold']==.5 and vr['adapter_fingerprint'] is None
        lab=label_response(r['text'],r['truncated'],gold[r['question_id']],'gsm8k')
        label={'question_id':r['question_id'],'candidate_id':r['candidate_id'],'G_round':arm,
               'correct':lab['correct'],'extracted_answer':lab['value'],'gold_answer':gold[r['question_id']],
               'verification_status':lab['status'],'answer_format':lab.get('format'),
               'unknown_reason':lab['status'] if lab['correct'] is None else None,
               'grader_version':VERSION+':dapo_exact_rational_v3','label_source':'independent_frozen_source_reference_comparison'}
        labels.append(label)
        rows.append({**r,**label,'score':vr['score'],'accepted':vr['accepted'],'V_prompt_tokens':vr['prompt_tokens']})
    write_jsonl(RUN/arm/'eval/labels_C.jsonl',labels)
    return rows


def main():
    cfg=validate_plan();freeze_analysis_plan()
    from .v0_drift_analysis import load_rows
    data={'G0':load_rows('G0','C')}
    for arm in ARMS:data[arm]=load_control(arm)
    original_hashes=cfg['original_sources_sha256']
    points=[];cis=[];deltas=[];full={};curves={};scenarios={}
    for scenario in SCENARIOS:
        view={g:copy.deepcopy(rows) for g,rows in data.items()}
        if scenario.startswith('C95'):
            view={g:[r for r in rr if r['question_id']!=SUSPECT] for g,rr in view.items()}
        elif scenario=='C96_reference1_overlay':
            for rr in view.values():
                for r in rr:
                    if r['question_id']==SUSPECT and r['correct'] is not None:r['correct']=numeric(r['extracted_answer'])=='1'
        qs=sorted({r['question_id'] for r in view['G0']})
        rng=np.random.default_rng(20261022);samples=rng.integers(len(qs),size=(2000,len(qs)))
        boot={};scenarios[scenario]=view
        for g in MODELS:
            point,ci,draws,curve=analyze(view[g],qs,samples)
            # Add intervals for correct/all and output length, absent in the earlier PRIMARY list.
            for key in METRICS:
                if key not in ci:ci[key]=interval(draws[key])
            full[scenario+'::'+g]={'point':point,'ci95':ci};boot[g]=draws;curves[scenario+'::'+g]=curve
            points.append({'scenario':scenario,'model':g,**{k:v for k,v in point.items() if not isinstance(v,dict)}})
            for k in METRICS:cis.append({'scenario':scenario,'model':g,'metric':k,'estimate':point[k],**ci[k]})
        for old,new in [('G0','Vmatched_SFT'),('G0','Oracle_SFT'),('Vmatched_SFT','Oracle_SFT')]:
            for k in METRICS:
                deltas.append({'scenario':scenario,'contrast':new+'-'+old,'metric':k,
                               'estimate':full[scenario+'::'+new]['point'][k]-full[scenario+'::'+old]['point'][k],
                               **interval(boot[new][k]-boot[old][k])})
    training_audit=audit_training(cfg)
    result={'study':cfg,'analysis_plan':json.loads((REPORT/'analysis_plan.json').read_text()),'metrics':full,
            'paired_deltas':clean(deltas),'training_audit':training_audit,'completed_at':utc(),
            'original_sources_unchanged':all(sha(ROOT/n)==h for n,h in original_hashes.items()),'T_read':False}
    assert result['original_sources_unchanged']
    for name,rs in [('metrics',points),('metrics_ci95',cis),('paired_deltas',deltas)]:csv_write(REPORT/f'{name}.csv',rs)
    write_json(REPORT/'results.json',clean(result));write_json(REPORT/'curves.json',curves)
    write_jsonl(REPORT/'candidates_full.jsonl',[r for g in MODELS for r in data[g]])
    from .oracle_visuals import render
    render(result,points,cis,deltas)
    write_json(RUN/'state.json',{'status':'complete','completed_at':utc(),'training_arms':2,'C_generated':768,'V0_scored':768,
                               'original_G0_baseline_reused':384,'T_read':False,'report':str(REPORT/'oracle_control_report.md')})
    print(json.dumps({'status':'complete','report':str(REPORT/'oracle_control_report.md')},ensure_ascii=False),flush=True)


def audit_training(cfg):
    import random
    from transformers import AutoTokenizer
    from .core import training_example
    from .formal_common import messages, protocol
    from .runtime import model_identity
    tok=AutoTokenizer.from_pretrained(protocol()['models']['G']['path'],local_files_only=True)
    raw={r['candidate_id']:r for r in read_jsonl(FORMAL/'G0_A.jsonl')}
    lab={r['candidate_id']:r for r in read_jsonl(FORMAL/'labels_G0_A.jsonl')}
    picked_orig={r['candidate_id'] for r in read_jsonl(FORMAL/'train/G1/selected.jsonl')}
    qorders={};records=[]
    for arm in ARMS:
        d=RUN/arm/'train/G1';selected=read_jsonl(d/'selected.jsonl');tokens=read_jsonl(d/'tokenized.jsonl');steps=read_jsonl(d/'steps.jsonl')
        assert len(selected)==len(tokens)==289
        for r,t in zip(selected,tokens):
            source=raw[r['candidate_id']]
            assert r['question_id']==t['question_id']==source['question_id'] and r['text']==source['text']
            assert r['question']==source['question']
            if arm=='Oracle_SFT':assert lab[r['candidate_id']]['correct'] is True
            else:assert r['candidate_id'] in picked_orig
            ex=training_example(tok,messages('G',r['question']),r['text'],4096)
            assert all(t[k]==ex[k] for k in ['input_ids','labels','attention_mask','prefix_length'])
            assert not any(k in t for k in ['gold_answer','reference','correct'])
        order=list(range(len(tokens)));random.Random(42).shuffle(order)
        consumed=[cid for step in steps for cid in step['candidate_ids']]
        assert consumed==[tokens[i]['candidate_id'] for i in order]
        assert len(set(consumed))==289 and len(steps)==37
        qorders[arm]=[tokens[i]['question_id'] for i in order]
        report=json.loads((d/'report.json').read_text());assert report['parent_adapter_fingerprint'] is None and report['source_generator']=='G0'
        from .oracle_control import checkpoint
        base,adapter=model_identity('G',checkpoint(arm));assert adapter==report['final_adapter_fingerprint']
        assert report['base_frozen'] and report['save_reload_passed']
        assert report['supervised_tokens']==sum(s['supervised_tokens'] for s in steps)==225565
        assert report['input_tokens']==sum(s['input_tokens'] for s in steps)==264654
        records.append({'arm':arm,'exact_training_rows_rebuilt':289,'epochs':1,'optimizer_steps':37,
                        'supervised_tokens':225565,'input_tokens':264654,'selected_examples_unique_once':True,
                        'fresh_G0':True,'base_fingerprint':base,'adapter_fingerprint':adapter,
                        'total_seconds':report['total_seconds_this_invocation'],
                        'peak_allocated_bytes':report['peak_allocated_bytes_this_invocation'],
                        'engineering_probe_uses_independent_text':True})
    assert qorders[ARMS[0]]==qorders[ARMS[1]]
    result={'passed':True,'both_same_shuffled_question_order':True,'both_same_accumulation_question_groups':True,
            'arms':records,'B_C_T_supervised_samples':0,'final_answer_oracle_does_not_certify_process':True,
            'V_arm_question_set_conditioned_on_oracle_availability':True}
    write_json(REPORT/'training_audit.json',result);return result


if __name__=='__main__':main()
