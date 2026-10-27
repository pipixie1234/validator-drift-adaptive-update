"""Commit feedback-only SFT data before reading independent A labels for quality reporting."""
import argparse
import json
from collections import Counter
from .core import ROOT,read_jsonl,write_json,write_jsonl,ensure_run_manifest,training_example
from .day3_protocol import select_feedback,quarantined_ids,quality
from .formal_common import RUN,REPORT,PROTOCOL,protocol,inputs,output_path,messages,verify_fixed,sha,utc

def select(source_round,target_round):
    from transformers import AutoTokenizer
    verify_fixed();p=protocol();assert (source_round,target_round) in [('G0','G1'),('G1','G2')]
    source=output_path(source_round,'A');scorepath=output_path(source_round,'A','V')
    rows=read_jsonl(source);scores={r['candidate_id']:r for r in read_jsonl(scorepath)}
    assert len(rows)==len(scores)==2400
    train=p['training']['G'];picked=select_feedback(rows,scores,.5,train['seed'],train['max_examples_per_round'],quarantined_ids(),train['max_length'])
    tok=AutoTokenizer.from_pretrained(p['models']['G']['path'],local_files_only=True)
    if tok.pad_token_id is None:tok.pad_token=tok.eos_token
    data=[];tokens=[];excluded=[]
    for r in picked:
        ex=training_example(tok,messages('G',r['question']),r['text'],train['max_length'])
        if ex is None:excluded.append({'candidate_id':r['candidate_id'],'reason':'exact_training_template_over_context_no_replacement'});continue
        assert all(x==-100 for x in ex['labels'][:ex['prefix_length']])
        data.append({k:r[k] for k in ['question_id','candidate_id','question','text','G_round','prompt_tokens','output_tokens']}|
            {'V0_score':scores[r['candidate_id']]['score'],'selection_used_gold':False,'input_tokens':len(ex['input_ids']),
            'supervised_tokens':sum(x!=-100 for x in ex['labels'])})
        tokens.append({'question_id':r['question_id'],'candidate_id':r['candidate_id'],**ex})
    assert 1<=len(data)<=512,'No complete, V0-accepted training responses; stop rather than duplicate samples or use truth to rescue selection'
    assert len({r['question_id'] for r in data})==len(data)
    directory=RUN/f'train/{target_round}';directory.mkdir(parents=True,exist_ok=True)
    descriptor={'source_round':source_round,'target_round':target_round,'source_G_sha256':sha(source),'source_V0_sha256':sha(scorepath),
        'protocol_sha256':sha(PROTOCOL),'selector_sha256':sha(ROOT/'src/pilot/day3_protocol.py'),
        'preparation_code_sha256':sha(ROOT/'src/pilot/formal_select.py'),'selection_uses_independent_labels':False,
        'selected_ids':[r['candidate_id'] for r in data],'exact_length_exclusions':excluded,'settings':train}
    ensure_run_manifest(directory/'selection.manifest.json',descriptor)
    for name,content in [('selected.jsonl',data),('tokenized.jsonl',tokens)]:
        path=directory/name
        if path.exists():assert read_jsonl(path)==content
        else:write_jsonl(path,content)
    # Independent references are read only after immutable selection has been committed.
    from .formal_labels import label_split
    labels={r['candidate_id']:r for r in label_split(source_round,'A')}
    q=quality([r['candidate_id'] for r in data],labels)
    accepted=[r for r in rows if scores[r['candidate_id']].get('score') is not None and scores[r['candidate_id']]['score']>=.5]
    report={'source_round':source_round,'target_round':target_round,'selected_before_independent_labelling':True,
        'selection_uses_gold':False,'selected_file_sha256':sha(directory/'selected.jsonl'),'tokenized_file_sha256':sha(directory/'tokenized.jsonl'),
        'actual_SFT_quality':q,'entire_accepted_pool_quality':quality([r['candidate_id'] for r in accepted],labels),
        'label_cost_candidates':len(rows),'label_cost_questions':600,
        'selected_questions':len(data),'questions_without_accepted_response':600-len({r['question_id'] for r in accepted}),
        'exact_length_exclusions':excluded,'pre_cap_frozen_sampler_examples':len(picked),
        'generation_truncated_candidates':sum(r['truncated'] for r in rows),
        'input_tokens':sum(r['input_tokens'] for r in data),'supervised_tokens':sum(r['supervised_tokens'] for r in data),
        'max_input_tokens':max(r['input_tokens'] for r in data),'created_at':utc(),
        'interpretation':'Actual data after one-per-question selection; imperfect verifier feedback. Truth records quality only and never removes wrong/unknown responses.'}
    write_json(REPORT/f'{target_round}_SFT_data_quality.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--from-round',choices=['G0','G1'],required=True);p.add_argument('--target',choices=['G1','G2'],required=True);a=p.parse_args();select(a.from_round,a.target)

if __name__=='__main__':main()
