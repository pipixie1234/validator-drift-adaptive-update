"""Question quarantine, independent labels, working point and a single formal freeze."""
import argparse
import json
import math
import random
import re
from collections import Counter,defaultdict
from pathlib import Path
from .core import ROOT,read_jsonl,write_json,write_jsonl,ensure_run_manifest,digest
from .dapo_common import BASE,RUN,REPORT,config,sha
from .day2_protocol import label_response,extract,numeric,VERSION
from .day2_evaluate import stats,confidence

OUT=ROOT/'runs/day3'
DOC=ROOT/'reports/day3'

def quarantined_ids():
    return {r['question_id'] for r in json.loads((REPORT/'source_quality_review.json').read_text())['issues']}

def formal_generation_settings(prompt_tokens):
    protocol=json.loads((ROOT/'configs/protocol_revisions/003_dapo_day3.json').read_text())
    if not 0<prompt_tokens<=protocol['G_max_prompt_tokens']:
        raise ValueError('Question prompt outside the frozen text/context eligibility rule')
    settings=dict(protocol['sampling'])
    settings['max_new_tokens']=min(settings['max_new_tokens'],protocol['G_context']-prompt_tokens)
    return settings

def select_feedback(rows,scores,threshold=.5,seed=42,max_examples=512,bad_ids=frozenset(),max_length=4096):
    """No gold/independent labels in the interface. One random accepted response per question."""
    groups=defaultdict(list)
    for r in rows:
        if r['question_id'] in bad_ids or r['truncated']:continue
        if r['prompt_tokens']+r['output_tokens']+1>max_length:continue
        score=scores[r['candidate_id']].get('score')
        if score is not None and score>=threshold:groups[r['question_id']].append(r)
    chosen=[]
    for q,rs in sorted(groups.items()):
        rng=random.Random(seed+int(digest(q)[:8],16))
        chosen.append(rng.choice(sorted(rs,key=lambda r:r['candidate_index'])))
    if len(chosen)>max_examples:
        random.Random(seed).shuffle(chosen);chosen=chosen[:max_examples]
    return chosen

def quality(ids,labels):
    values=[labels[c]['correct'] for c in ids];c=sum(x is True for x in values);w=sum(x is False for x in values);u=len(values)-c-w
    return {'selected':len(values),'correct':c,'wrong':w,'unknown':u,
        'verified_error_fraction':w/(c+w) if c+w else None,
        'all_selected_error_lower_bound':w/len(values) if values else None,
        'all_selected_error_upper_bound':(w+u)/len(values) if values else None}

def risk_summary(rows):
    m=stats(rows);accepted=[r for r in rows if r.get('accepted') is True]
    unknown=sum(r['correct'] is None for r in accepted)
    m.update({'accepted_total':len(accepted),'accepted_unknown':unknown,
        'accepted_label_coverage':(len(accepted)-unknown)/len(accepted) if accepted else None,
        'accepted_error_lower_bound_all':m['FP']/len(accepted) if accepted else None,
        'accepted_error_upper_bound_all':(m['FP']+unknown)/len(accepted) if accepted else None})
    return m

def prepare():
    OUT.mkdir(exist_ok=True);DOC.mkdir(exist_ok=True)
    g=read_jsonl(RUN/'G0_dapo100.jsonl');v={r['candidate_id']:r for r in read_jsonl(RUN/'V0_dapo100.jsonl')}
    assert len(g)==len(v)==400
    review=json.loads((REPORT/'source_quality_review.json').read_text());issues={r['question_id']:r for r in review['issues']}
    gold={r['question_id']:r['gold_answer'] for r in read_jsonl(BASE/'labels/dapo100.jsonl')}
    labels=[];joined=[]
    for r in g:
        lab=label_response(r['text'],r['truncated'],gold[r['question_id']],'gsm8k')
        parsed=extract(r['text'],r['truncated'],'gsm8k')
        try:
            n=numeric(lab['value']) if lab['value'] is not None else None
            typ='integer' if n is not None and '/' not in n else 'rational' if n is not None else 'unresolved'
        except (ValueError,ZeroDivisionError):typ='unsupported_expression'
        bad=r['question_id'] in issues
        row={'question_id':r['question_id'],'candidate_id':r['candidate_id'],
            'question_status':'quarantined' if bad else 'eligible',
            'question_issue':issues.get(r['question_id']),
            'correct':None if bad else lab['correct'],'gold_answer':gold[r['question_id']],
            'verification_status':'question_quarantined' if bad else lab['status'],
            'parse_status':parsed['status'],'answer_type':typ,'extracted_answer':lab['value'],
            'comparison':lab['comparison'],'answer_format':lab.get('format'),
            'answer_unknown_reason':lab['status'] if not bad and lab['correct'] is None else None,
            'grader_version':VERSION+':dapo_exact_rational_v3',
            'label_source':'automatic_reference_comparison' if not bad else 'question_level_assistant_audit',
            'source_gold_only_diagnostic_correct':lab['correct'],
            'revision_history':[{'operation':'quarantine_entire_question','reason':issues[r['question_id']]['category'],
                'source':'reports/dapo/source_quality_review.json'}] if bad else []}
        labels.append(row)
        joined.append({**r,**v[r['candidate_id']],**row,'prompt_tokens':r['prompt_tokens']})
    write_jsonl(OUT/'labels_C.jsonl',labels)
    valid=[r for r in joined if r['question_status']=='eligible'];bad=[r for r in joined if r['question_status']=='quarantined']
    assert len(valid)==384 and len(bad)==16 and len({r['question_id'] for r in bad})==4
    assert all(n==4 for n in Counter(r['question_id'] for r in bad).values())
    write_jsonl(OUT/'quarantined_C_candidates.jsonl',bad)
    write_jsonl(OUT/'valid_C_candidates.jsonl',valid)
    inputs=read_jsonl(BASE/'inputs/dapo100.jsonl')
    quarantine=[{**r,'issue':issues[r['question_id']],'all_candidate_ids':[x['candidate_id'] for x in bad if x['question_id']==r['question_id']]} for r in inputs if r['question_id'] in issues]
    write_jsonl(BASE/'manifests/question_quarantine.jsonl',quarantine)
    m=risk_summary(valid);m['uncertainty']=confidence(valid,'dapo')
    m['answer_unknown_reasons']=dict(Counter(r['answer_unknown_reason'] for r in valid if r['correct'] is None))
    m['truncated_count']=sum(r['truncated'] for r in valid)
    m['all_original_candidates']=400;m['quarantined_questions']=4;m['quarantined_candidates']=16
    m['original_all_400_unknown_accounting']={'unknown_or_quarantined':sum(r['correct'] is None for r in joined),
        'all_truncated':sum(r['truncated'] for r in joined),'quarantined_candidates':16,
        'truncated_inside_quarantine':sum(r['truncated'] for r in bad),'eligible_answer_unknown':m['unknown']}
    labels_byid={r['candidate_id']:r for r in labels};table=[]
    for t in [.5,.6,.7,.8,.9,.95,.99]:
        rs=[{**r,'accepted':r['score']>=t} for r in valid]
        sm=risk_summary(rs);chosen=select_feedback(g,v,t,bad_ids=set(issues))
        table.append({'threshold':t,'accepted_total':sm['accepted_total'],'retention_all':sm['acceptance_rate_all_scored'],
            'FPR':sm['false_accept_rate'],'FNR':sm['false_reject_rate'],
            'accepted_risk_verified':sm['accepted_error_fraction'],
            'accepted_risk_lower_all':sm['accepted_error_lower_bound_all'],
            'accepted_risk_upper_all':sm['accepted_error_upper_bound_all'],
            'one_per_question_SFT_preview':quality([r['candidate_id'] for r in chosen],labels_byid)})
    decision={'selected_threshold':.5,'development_grid':table,
        'decision':'Retain0.5. Higher tested thresholds reduce question coverage and do not improve independently labelled error proportion after the frozen one-response-per-question sampling. Scores are uncalibrated.',
        'development_only':True,'test_used':False,'threshold_changes_from_day2':0,
        'sampling_rule':'One random accepted response per question, seed42 plus SHA256(question_id); no label input; exclude whole quarantined questions, generation truncation or over-context only.',
        'not_actual_A_training_data':'This preview uses C only; actual A selections must be independently labelled AFTER selection and quality reported for each generator update.'}
    write_json(DOC/'working_point.json',decision)
    chosen=select_feedback(g,v,.5,bad_ids=set(issues))
    write_jsonl(OUT/'C_sft_preview.jsonl',[{'question_id':r['question_id'],'candidate_id':r['candidate_id'],'selection_used_gold':False} for r in chosen])
    m['SFT_preview_quality']=quality([r['candidate_id'] for r in chosen],labels_byid)
    write_json(DOC/'C_valid_metrics.json',m)
    # Targeted small audit, explicitly not a random estimate of grading accuracy.
    audit=[]
    for kind,truth in [('correct',True),('wrong',False),('unknown',None)]:
        seen=set()
        for r in sorted([r for r in valid if r['correct'] is truth],key=lambda r:(r['output_tokens'],r['candidate_id'])):
            if r['question_id'] in seen:continue
            seen.add(r['question_id']);audit.append({**r,'audit_stratum':kind,'assistant_review':None,'human_review':None})
            if len(seen)==2:break
    auditpath=DOC/'answer_audit_sample.jsonl'
    if auditpath.exists():
        assert {r['candidate_id'] for r in read_jsonl(auditpath)}=={r['candidate_id'] for r in audit}
    else:write_jsonl(auditpath,audit)
    print(json.dumps({'valid_questions':96,'valid_candidates':384,'correct':m['correct'],'wrong':m['natural_errors'],'answer_unknown':m['unknown'],
        'valid_truncated':m['truncated_count'],'SFT_preview':m['SFT_preview_quality'],'threshold':.5},ensure_ascii=False,indent=2))

def formal_splits():
    inventory=read_jsonl(BASE/'eligible_families.jsonl');cfg=config()
    old=json.loads((BASE/'manifests/selection_locked.json').read_text());assert sha(BASE/'eligible_families.jsonl')==old['inventory_sha256']
    dev=read_jsonl(BASE/'inputs/dapo100.jsonl');assert [r['question_id'] for r in inventory[:100]]==[r['question_id'] for r in dev]
    remaining=[];excluded=[]
    for r in inventory[100:]:
        # New text-only source-quality safeguard motivated by C; no answers or model scores used.
        if re.search(r'(?:dot\s+plot|diagram|graph|figure)\s+(?:below|above|shown)',r['question'],re.I):excluded.append({'question_id':r['question_id'],'family_id':r['family_id'],'reason':'explicit_missing_figure_reference'});continue
        remaining.append(r)
    members={'C_development':dev};manifest={};offset=0
    for split,size in cfg['conditional_formal_pool_sizes'].items():
        chosen=remaining[offset:offset+size];offset+=size;assert len(chosen)==size
        inputs=[{k:v for k,v in r.items() if k not in ['gold_answer','reference_consistent']}|{'split':split,'task':'dapo'} for r in chosen]
        labels=[{'question_id':r['question_id'],'gold_answer':r['gold_answer']} for r in chosen]
        write_jsonl(BASE/f'formal/inputs/{split}.jsonl',inputs);write_jsonl(BASE/f'formal/labels/{split}.jsonl',labels)
        members[split]=inputs;manifest[split]={'questions':size,'input_sha256':sha(BASE/f'formal/inputs/{split}.jsonl'),
            'labels_sha256':sha(BASE/f'formal/labels/{split}.jsonl'),'question_ids':[r['question_id'] for r in inputs],'family_ids':[r['family_id'] for r in inputs]}
    for field in ['question_id','question_hash','family_id']:
        sets=[{r[field] for r in rs} for rs in members.values()];assert len(set.union(*sets))==sum(map(len,sets))
    uuids=[{u for r in rs for u in r['family_source_uuids']} for rs in members.values()];assert len(set.union(*uuids))==sum(map(len,uuids))
    write_jsonl(BASE/'formal/preallocation_exclusions.jsonl',excluded)
    result={'seed':42,'source_revision':cfg['revision'],'source_sha256':cfg['file_sha256'],'splits':manifest,
        'C_questions':100,'C_valid':96,'C_quarantined':4,'all_C_families_excluded_from_A_B_T':True,
        'allocation':'Fixed pre-shuffled family inventory; skip explicit missing-figure references based solely on text; no answer or model-performance selection.',
        'additional_question_exclusions':len(excluded),'dedup_limit':old['dedup_limit'],
        'heldout_is_custom_split_of_public_train':True,'T_inferred':False,'formal_training_started':False}
    ensure_run_manifest(BASE/'formal/manifest.json',result);return result

def freeze():
    cfg=config();m=json.loads((DOC/'C_valid_metrics.json').read_text())
    replay=read_jsonl(OUT/'length_replay/G0_native_context.jsonl')
    assert len(replay)==32 and len({r['question_id'] for r in replay})==8
    assert all(r['same_original_decoded_prefix'] for r in replay)
    assert all(r['completed_control_unchanged'] is not False for r in replay)
    bad=quarantined_ids();oldcensored=[r for r in replay if r['original_truncated']]
    summary={'primary_400_unchanged':True,'supplementary_not_merged':True,'questions':8,'candidates':32,
        'all_prefixes_match':True,'all_completed_controls_unchanged':True,'original_truncated':len(oldcensored),
        'old_truncated_completed_at_native_context':sum(not r['truncated'] for r in oldcensored),
        'remaining_truncated_at_native_context':sum(r['truncated'] for r in oldcensored),
        'eligible_old_truncated':sum(r['question_id'] not in bad for r in oldcensored),
        'eligible_old_truncated_completed':sum(r['question_id'] not in bad and not r['truncated'] for r in oldcensored),
        'supplementary_G_seconds':sum({r['question_id']:r['generation_seconds_for_question'] for r in replay}.values()),
        'formal_decision':'For G0/G1/G2, max_new_tokens=min(3840,4096-exact_prompt_tokens), same tokenizer and rule every round; prompt<=1024. Native4096 total context is never extended. Original3072 diagnostic remains unchanged. All4 newly completed responses finish by3678 tokens; all residual cap truncations remain unknown.'}
    write_json(DOC/'length_decision.json',summary)
    splits=formal_splits()
    training={'G':{'objective':'response_only_SFT','max_length':4096,'max_examples_per_round':512,'epochs':1,
        'micro_batch':1,'gradient_accumulation':8,'learning_rate':1e-4,'lora_r':16,'lora_alpha':32,'lora_dropout':.05,
        'optimizer':'AdamW','seed':42,'G1_initialization':'original G0 plus new adapter','G2_initialization':'continue the G1 adapter',
        'sample_selection':'pilot.day3_protocol.select_feedback; whole invalid questions excluded; one random V0-accepted response per question; unknown answer labels do not filter selections; cap512 without duplicating rows',
        'quality_reporting':'For each actual A selection, separately report correct/wrong/unknown and bounds after selection; never silently filter by truth.',
        'truncation':'Never truncate tensors; reject over-context examples, log IDs/reasons; generation-truncated answers excluded by fixed structural rule.'},
        'V_smoke':json.loads((ROOT/'configs/day3_smoke.json').read_text()),
        'V_update_future':'Independent V0 initializations for random/target branches, identical context and optimization; exact matched sample budget frozen after B is available, before either update.'}
    answer={'version':'dapo_answer_v3_20261021','implementation':'src/pilot/day2_protocol.py','implementation_sha256':sha(ROOT/'src/pilot/day2_protocol.py'),
        'reference':'Signed integer source reference retained separately, never sent to G/V.',
        'extract':'Balanced boxed/fbox or explicit hash marker; never last number in prose. Nonconflicting descriptive suffix allowed; conflicting later math is unknown.',
        'multiple_boxes':'Accept only if every box normalizes to an exactly equal rational; otherwise unknown.',
        'equivalence':'Exact Fraction: signed integers, decimals, grouped thousands, simple fractions including LaTeX frac, established nonconflicting unit normalization. Equivalent34,34.0,68/2 accepted.',
        'unsupported':'Undecidable/symbolic/non-rational expressions unsupported by exact-rational path remain unknown, not negative.',
        'truncation':'Always unknown even if an apparent final box is present.',
        'question_quarantine':'Entire question and all candidates excluded from primary metrics and all training/evaluation sets until a versioned source repair. Keep original100/400 archival accounting separately.',
        'unknown':'Eligible-question answer unknown remains in coverage and correctness bounds; never V negative supervision.',
        'fields':['question_status','parse_status','answer_type','extracted_answer','comparison','grader_version','answer_unknown_reason','revision_history']}
    write_json(ROOT/'configs/dapo_answer_v3.json',answer)
    protocol={'revision':'003_DAPO_day3_20261021','research_goal':'Verifier reliability across generator updates; no assumed generator improvement',
        'dataset':cfg['dataset'],'dataset_revision':cfg['revision'],'source_file_sha256':cfg['file_sha256'],
        'models':{role:{'path':cfg[role+'_MODEL_DIR'],'fingerprint':__import__('pilot.runtime',fromlist=['model_identity']).model_identity(role)[0],
            'adapter_for_primary_inference':None} for role in ['G','V']},
        'prompts':{k:cfg[k] for k in ['G_system','G_user_suffix','V_system']},'sampling':dict(cfg['generation'],max_new_tokens=3840),
        'G_output_budget_rule':'min(3840,4096 - exact rendered prompt token count); same rule and tokenizer for G0/G1/G2',
        'G_context':4096,'G_max_prompt_tokens':1024,'V_scoring':cfg['verifier'],
        'training':training,'answer_protocol_file':'configs/dapo_answer_v3.json','answer_protocol_sha256':sha(ROOT/'configs/dapo_answer_v3.json'),
        'working_point_file':'reports/day3/working_point.json','working_point_sha256':sha(DOC/'working_point.json'),
        'length_decision_file':'reports/day3/length_decision.json','length_decision_sha256':sha(DOC/'length_decision.json'),
        'quarantine_file':'data/dapo/manifests/question_quarantine.jsonl','quarantine_sha256':sha(BASE/'manifests/question_quarantine.jsonl'),
        'formal_split_manifest':'data/dapo/formal/manifest.json','formal_split_sha256':sha(BASE/'formal/manifest.json'),
        'preserved_day2_config_sha256':sha(ROOT/'configs/formal_protocol.json'),
        'preserved_original_G400_sha256':sha(RUN/'G0_dapo100.jsonl'),'preserved_original_V400_sha256':sha(RUN/'V0_dapo100.jsonl'),
        'primary_analysis_scope':'96 eligible C questions384 outputs; entire4 bad questions16 outputs quarantined; original100 remain C and excluded from all formal pools.',
        'changes':'User-directed whole-question quarantine; complete separate native-length check; formal G output cap3840 with total native4096 and V threshold0.5. Original3072 development400 unchanged, supplemental outputs separate. No gold-based feedback filtering.',
        'formal_training_started':False,'G1_G2_status':'Day3 only; start generator updates in the next stage from G0, never from smoke adapters',
        'T_inferred':False,'source_quality_limit':'Only obvious source failures screened. Approximate lexical family dedup cannot guarantee semantic identity or eliminate pretraining exposure.'}
    ensure_run_manifest(ROOT/'configs/protocol_revisions/003_dapo_day3.json',protocol)
    write_json(ROOT/'configs/current_protocol.json',{'active_revision':'configs/protocol_revisions/003_dapo_day3.json','formal_training':'not_started','previous_frozen_config_preserved':'configs/formal_protocol.json'})
    write_json(ROOT/'configs/stage_status.json',{'phase':'day3_protocol_frozen_pending_smoke','formal_G1_G2_training':'not_started','formal_training_started':False})
    print(json.dumps({'formal_lengths':{'G_output_cap':3840,'G_total':4096,'V_total':8192},'threshold':.5,'splits':{k:v['questions'] for k,v in splits['splits'].items()},'replay':summary},ensure_ascii=False,indent=2))

def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','freeze']);a=p.parse_args()
    prepare() if a.stage=='prepare' else freeze()

if __name__=='__main__':main()
