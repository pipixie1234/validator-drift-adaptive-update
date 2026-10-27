"""Independent labels and descriptive metrics, outside G/V inference and feedback selection."""
import argparse
import json
from collections import Counter
import numpy as np
from .core import ROOT,read_jsonl,write_json,write_jsonl,ensure_run_manifest
from .day2_protocol import label_response,extract,numeric,VERSION
from .day3_protocol import risk_summary,quarantined_ids
from .day2_evaluate import confidence
from .formal_common import RUN,REPORT,PROTOCOL,inputs,output_path,verify_fixed,sha,utc

def reference_path(split):
    if split=='C':return ROOT/'data/dapo/labels/dapo100.jsonl'
    if split not in ['A','B']:raise ValueError('T labels sealed')
    return ROOT/f'data/dapo/formal/labels/{"A_G_update" if split=="A" else "B_V_update"}.jsonl'

def label_split(round_name,split):
    verify_fixed();path=output_path(round_name,split);rows=read_jsonl(path)
    assert len(rows)==len(inputs(split))*4
    target=RUN/f'labels_{round_name}_{split}.jsonl';ref=reference_path(split)
    descriptor={'raw_G_sha256':sha(path),'reference_sha256':sha(ref),'protocol_sha256':sha(PROTOCOL),
        'grader_sha256':sha(ROOT/'src/pilot/day2_protocol.py'),'label_code_sha256':sha(ROOT/'src/pilot/formal_labels.py')}
    ensure_run_manifest(target.with_suffix('.manifest.json'),descriptor)
    if target.exists():
        existing=read_jsonl(target);assert len(existing)==len(rows)
        return existing
    gold={r['question_id']:r['gold_answer'] for r in read_jsonl(ref)};bad=quarantined_ids();out=[]
    for r in rows:
        assert r['question_id'] not in bad,'Whole invalid questions must not enter formal generation'
        lab=label_response(r['text'],r['truncated'],gold[r['question_id']],'gsm8k')
        e=extract(r['text'],r['truncated'],'gsm8k')
        try:
            n=numeric(lab['value']) if lab['value'] is not None else None
            typ='integer' if n is not None and '/' not in n else 'rational' if n is not None else 'unresolved'
        except (ValueError,ZeroDivisionError):typ='unsupported_expression'
        out.append({'question_id':r['question_id'],'candidate_id':r['candidate_id'],'split':split,'G_round':round_name,
            'correct':lab['correct'],'gold_answer':gold[r['question_id']],'verification_status':lab['status'],
            'parse_status':e['status'],'answer_type':typ,'extracted_answer':lab['value'],'comparison':lab['comparison'],
            'answer_format':lab.get('format'),'unknown_reason':lab['status'] if lab['correct'] is None else None,
            'grader_version':VERSION+':dapo_exact_rational_v3','label_source':'independent_frozen_source_reference_comparison',
            'question_status':'eligible_by_frozen_manifest_not_human_certified','recorded_at':utc()})
    write_jsonl(target,out);return out

def joined(round_name,split):
    g=read_jsonl(output_path(round_name,split));v=read_jsonl(output_path(round_name,split,'V'))
    assert len(g)==len(v)==len(inputs(split))*4
    byv={r['candidate_id']:r for r in v};labs={r['candidate_id']:r for r in label_split(round_name,split)}
    assert {r['candidate_id'] for r in g}==set(byv)==set(labs)
    return [{**r,**byv[r['candidate_id']],**labs[r['candidate_id']],
        'G_model_fingerprint':r['model_fingerprint'],'G_adapter_fingerprint':r['adapter_fingerprint'],
        'V_model_fingerprint':byv[r['candidate_id']]['model_fingerprint'],
        'prompt_tokens':r['prompt_tokens'],'V_prompt_tokens':byv[r['candidate_id']]['prompt_tokens']} for r in g]

def evaluate(round_name,split):
    rs=joined(round_name,split);m=risk_summary(rs)
    m.update({'G_round':round_name,'split':split,'truncated':sum(r['truncated'] for r in rs),
        'unknown_fraction':sum(r['correct'] is None for r in rs)/len(rs),
        'unknown_reasons':dict(Counter(r['unknown_reason'] for r in rs if r['correct'] is None)),
        'answer_formats':dict(Counter(r['answer_format'] or 'none' for r in rs)),
        'length_tokens':{name:float(func([r['output_tokens'] for r in rs])) for name,func in [
            ('mean',np.mean),('median',np.median),('p95',lambda x:np.quantile(x,.95)),('max',np.max)]},
        'G_prompt_plus_output_max':max(r['prompt_tokens']+r['output_tokens'] for r in rs),
        'V_prompt_max':max(r['V_prompt_tokens'] for r in rs),
        'G_generation_seconds':sum({r['question_id']:r['generation_seconds_for_question'] for r in rs}.values()),
        'V_score_seconds':sum(r['seconds'] for r in rs),
        'by_length':{name:risk_summary([r for r in rs if lo<=r['output_tokens']<=hi]) for name,lo,hi in [
            ('le512',0,512),('513_to1024',513,1024),('1025_to2048',1025,2048),('gt2048',2049,100000)]},
        'uncertainty':confidence(rs,'dapo'),
        'reference_limit':'Source-answer comparison is independent ofV0, not proof all public references are correct. No source answer used for filtering.'})
    write_json(REPORT/f'{round_name}_{split}_metrics.json',m)
    print(json.dumps({'round':round_name,'split':split,'correct':m['correct'],'wrong':m['natural_errors'],
        'unknown':m['unknown'],'FPR':m['false_accept_rate'],'FNR':m['false_reject_rate']},ensure_ascii=False),flush=True)
    return m

def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['label','evaluate']);p.add_argument('--round',dest='round_name',choices=['G0','G1','G2'],required=True);p.add_argument('--split',choices=['A','B','C'],required=True);a=p.parse_args()
    label_split(a.round_name,a.split) if a.stage=='label' else evaluate(a.round_name,a.split)

if __name__=='__main__':main()
