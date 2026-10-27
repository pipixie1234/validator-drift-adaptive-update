"""CPU-only follow-up: paired V0 errors, exact supervision audit, evidence cases.

Reads cached A/C only; never loads model weights or changes frozen runs.
"""
import csv
import hashlib
import json
import re
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path
import numpy as np
from .core import ROOT, read_jsonl, write_json, write_jsonl, training_example
from .formal_common import sha, utc, messages, protocol
from .explanation_probes import SUSPECT
from .v0_drift_analysis import clean, csv_write, interval

DEST=ROOT/'reports/cache_followup_20261024'
MODELS=['G0','Random_SFT','Vmatched_SFT','Oracle_SFT']
ARMS=MODELS[1:]
IMAGE='dapo:train:4b578eb4-1eb5-4e4e-9c89-68c2cffa64e0'
REFERENCE_A='dapo:train:93eb13ae-df32-4080-bdbf-36f09f2b6fc6'
NOTES=ROOT/'configs/cache_followup_review_20261024.json'
TRAIN={
 'Random_SFT':ROOT/'runs/random_control_20261024/Random_SFT/train/G1',
 'Vmatched_SFT':ROOT/'runs/oracle_control_20261022/Vmatched_SFT/train/G1',
 'Oracle_SFT':ROOT/'runs/oracle_control_20261022/Oracle_SFT/train/G1',
 'formal_G1':ROOT/'runs/formal/train/G1','formal_G2':ROOT/'runs/formal/train/G2'}

def counts(rows):
 tp=sum(r['correct'] is True and r['accepted'] for r in rows)
 fn=sum(r['correct'] is True and not r['accepted'] for r in rows)
 fp=sum(r['correct'] is False and r['accepted'] for r in rows)
 tn=sum(r['correct'] is False and not r['accepted'] for r in rows)
 ua=sum(r['correct'] is None and r['accepted'] for r in rows)
 ur=sum(r['correct'] is None and not r['accepted'] for r in rows)
 div=lambda a,b:a/b if b else None
 return {'questions':len({r['question_id'] for r in rows}),'total':len(rows),'TP':tp,'FN':fn,'FP':fp,'TN':tn,
  'correct':tp+fn,'wrong':fp+tn,'unknown':ua+ur,'FPR':div(fp,fp+tn),'FNR':div(fn,tp+fn),
  'G_accuracy_verified':div(tp+fn,tp+fn+fp+tn),'correct_all':div(tp+fn,len(rows)),
  'retention':div(tp+fp+ua,len(rows)),'FPR_unknown_lower':div(fp,fp+tn+ur),
  'FPR_unknown_upper':div(fp+ua,fp+tn+ua)}

def independent_bootstrap(data,qs,draws=10000):
 ix={q:i for i,q in enumerate(qs)}
 samples=np.random.default_rng(20261022).integers(len(qs),size=(draws,len(qs)))
 out={}
 for g,rows in data.items():
  a=np.zeros((len(qs),4),int)
  for r in rows:
   if r['correct'] is None:continue
   cell=(0 if r['accepted'] else 1) if r['correct'] else (2 if r['accepted'] else 3)
   a[ix[r['question_id']],cell]+=1
  v=a[samples].sum(1)
  with np.errstate(divide='ignore',invalid='ignore'):
   out[g]={'FPR':v[:,2]/(v[:,2]+v[:,3]),'FNR':v[:,1]/(v[:,0]+v[:,1])}
 return out

def error_intervals(data):
 old=json.loads((ROOT/'reports/random_control_20261024/results.json').read_text());rows=[];pts=[]
 for scenario in ['C95_exclude_reference_suspect','C96_frozen_original','C96_reference1_overlay']:
  view={g:[dict(r) for r in rr] for g,rr in data.items()}
  if scenario.startswith('C95'):view={g:[r for r in rr if r['question_id']!=SUSPECT] for g,rr in view.items()}
  elif scenario.endswith('overlay'):
   from .day2_protocol import numeric
   for rr in view.values():
    for r in rr:
     if r['question_id']==SUSPECT and r['correct'] is not None:r['correct']=numeric(r['extracted_answer'])=='1'
  qs=sorted({r['question_id'] for r in view['G0']});boot=independent_bootstrap(view,qs)
  point={g:counts(rr) for g,rr in view.items()}
  for g in MODELS:
   assert all(point[g][k]==old['metrics'][scenario+'::'+g]['point'][k] for k in ['TP','FN','FP','TN','unknown'])
   pts.append({'scenario':scenario,'model':g,**point[g]})
  for other in ['G0','Vmatched_SFT','Oracle_SFT']:
   for metric in ['FPR','FNR']:
    diff=boot['Random_SFT'][metric]-boot[other][metric];first=interval(diff[:2000])
    oldname='Random_SFT-G0' if other=='G0' else other+'-Random_SFT'
    previous=next(r for r in old['paired_deltas'] if r['scenario']==scenario and r['contrast']==oldname and r['metric']==metric)
    lo,hi=(previous['low'],previous['high']) if other=='G0' else (-previous['high'],-previous['low'])
    assert abs(lo-first['low'])<1e-12 and abs(hi-first['high'])<1e-12
    rows.append({'scenario':scenario,'contrast':'Random_SFT-'+other,'metric':metric,
     'estimate':point['Random_SFT'][metric]-point[other][metric],**interval(diff),'replicates':10000,
     'old_2000_low':first['low'],'old_2000_high':first['high'],'old_2000_exactly_reproduced':True})
 csv_write(DEST/'paired_FPR_FNR.csv',rows);csv_write(DEST/'point_counts_and_unknown_bounds.csv',pts)
 return rows,pts

def by_question(data):
 groups={g:defaultdict(list) for g in MODELS};rows=[];flips=[];decomp=[]
 for g,rr in data.items():
  for r in rr:
   if r['question_id']!=SUSPECT:groups[g][r['question_id']].append(r)
 for q in sorted(groups['G0']):
  row={'question_id':q,'question':groups['G0'][q][0]['question']}
  for g in MODELS:
   rr=groups[g][q];m=counts(rr)
   row.update({g+'__'+k:m[k] for k in ['correct','wrong','unknown','FP','FN']})
   row[g+'__mean_tokens']=float(np.mean([r['output_tokens'] for r in rr]))
   row[g+'__answers']=[r['extracted_answer'] for r in sorted(rr,key=lambda x:x['candidate_index'])]
  rows.append(row)
 for other in ['G0','Vmatched_SFT','Oracle_SFT']:
  a={(r['question_id'],r['candidate_index']):r for rr in groups['Random_SFT'].values() for r in rr}
  b={(r['question_id'],r['candidate_index']):r for rr in groups[other].values() for r in rr}
  for truth in [True,False]:
   pairs=[(r,b[k]) for k,r in a.items() if r['correct'] is truth and b[k]['correct'] is truth]
   flips.append({'other':other,'truth':truth,'pairs':len(pairs),
    'other_accept_random_reject':sum(s['accepted'] and not r['accepted'] for r,s in pairs),
    'other_reject_random_accept':sum(not s['accepted'] and r['accepted'] for r,s in pairs),
    'identical_text':sum(r['text']==s['text'] for r,s in pairs),
    'same_index_is_descriptive_not_paired_sampling_path':True})
  n0=sum(r['correct'] is False for rr in groups[other].values() for r in rr)
  n1=sum(r['correct'] is False for rr in groups['Random_SFT'].values() for r in rr)
  comp=within=entry=exit_=0.;common=enter=leave=0
  for q in groups[other]:
   x=[r for r in groups[other][q] if r['correct'] is False];y=[r for r in groups['Random_SFT'][q] if r['correct'] is False]
   w0=len(x)/n0;w1=len(y)/n1
   if x and y:
    common+=1;p0=np.mean([r['accepted'] for r in x]);p1=np.mean([r['accepted'] for r in y])
    comp+=(w1-w0)*(p1+p0)/2;within+=(p1-p0)*(w1+w0)/2
   elif y:enter+=1;entry+=sum(r['accepted'] for r in y)/n1
   elif x:leave+=1;exit_-=sum(r['accepted'] for r in x)/n0
  total=counts(list(a.values()))['FPR']-counts(list(b.values()))['FPR']
  assert abs(comp+within+entry+exit_-total)<1e-12
  decomp.append({'contrast':'Random_SFT-'+other,'common_questions':common,'new_wrong_questions':enter,
   'exited_wrong_questions':leave,'composition':comp,'within':within,'entry':entry,'exit':exit_,'total':total})
 csv_write(DEST/'question_comparison_C95.csv',rows);csv_write(DEST/'same_truth_decision_flips.csv',flips)
 csv_write(DEST/'FPR_question_support_decomposition.csv',decomp)
 return decomp

def answer_spans(text):
 spans=[]
 for m in re.finditer(r'\\boxed\s*\{',text):
  end=m.end();depth=1
  while end<len(text) and depth:
   if text[end]=='{':depth+=1
   elif text[end]=='}':depth-=1
   end+=1
  if not depth:spans.append((m.start(),end))
 spans += [m.span() for m in re.finditer(r'(?m)^\s*####[^\n]*',text)]
 return sorted(spans)

def mask_features(ex,offsets,start,text):
 ids,lab,prefix=ex['input_ids'],ex['labels'],ex['prefix_length']
 assert len(ids)==len(lab)==len(ex['attention_mask'])<=4096
 assert prefix>0 and all(t==-100 for t in lab[:prefix])
 assert lab[prefix:]==ids[prefix:] and all(t==1 for t in ex['attention_mask'])
 L=sum(t!=-100 for t in lab[1:]);assert L==len(ids)-prefix
 spans=answer_spans(text);indices=[]
 if spans:
  a,b=spans[-1];a+=start;b+=start
  indices=[i for i,(u,v) in enumerate(offsets) if v>u and v>a and u<b]
  assert indices and all(lab[i]!=-100 for i in indices)
 return {'input_tokens':len(ids),'prefix_tokens':prefix,'loss_tokens':L,'prompt_leaks':0,'assistant_holes':0,
  'explicit_markers':len(spans),'last_explicit_answer_tokens':len(indices),
  'last_answer_loss_fraction':len(indices)/L if spans else None,
  'supervised_im_end':sum(t==151645 and lab[i]!=-100 for i,t in enumerate(ids)),
  'last_token_id':ids[-1],'last_token_supervised':lab[-1]!=-100}

def supervision():
 from transformers import AutoTokenizer
 tok=AutoTokenizer.from_pretrained(protocol()['models']['G']['path'],local_files_only=True)
 details=[];summaries=[];orders={};selected_all={}
 for arm,path in TRAIN.items():
  selected=read_jsonl(path/'selected.jsonl');tensors=read_jsonl(path/'tokenized.jsonl');steps=read_jsonl(path/'steps.jsonl')
  assert len(selected)==len(tensors);source='G1' if arm=='formal_G2' else 'G0'
  labels={r['candidate_id']:r for r in read_jsonl(ROOT/f'runs/formal/labels_{source}_A.jsonl')}
  lookup={r['candidate_id']:r for r in selected};selected_all[arm]={r['question_id']:r for r in selected}
  batches={cid:(s['step'],len(s['candidate_ids'])) for s in steps for cid in s['candidate_ids']}
  assert len(batches)==len(selected)
  orders[arm]=[[lookup[cid]['question_id'] for cid in s['candidate_ids']] for s in steps]
  for r,ex in zip(selected,tensors):
   rebuilt=training_example(tok,messages('G',r['question']),r['text'],4096)
   assert all(ex[k]==rebuilt[k] for k in ['input_ids','labels','attention_mask','prefix_length'])
   prompt=tok.apply_chat_template(messages('G',r['question']),tokenize=False,add_generation_prompt=True)
   text=tok.apply_chat_template(messages('G',r['question'])+[{'role':'assistant','content':r['text']}],tokenize=False,add_generation_prompt=False)
   assert text.startswith(prompt) and text[len(prompt):].startswith(r['text'])
   encoded=tok(text,add_special_tokens=False,return_offsets_mapping=True);assert encoded['input_ids']==ex['input_ids']
   ft=mask_features(ex,encoded['offset_mapping'],len(prompt),r['text']);step,m=batches[r['candidate_id']];L=ft['loss_tokens']
   details.append({'arm':arm,'question_id':r['question_id'],'candidate_id':r['candidate_id'],
    'correct':labels[r['candidate_id']]['correct'],**ft,'step':step,'accumulation_size':m,
    'token_loss_coefficient':1/(m*L),'example_loss_coefficient':1/m,
    'normalized_epoch_loss_coefficient':1/(m*len(steps)),
    'answer_span_loss_coefficient':ft['last_explicit_answer_tokens']/(m*L)})
  rr=[r for r in details if r['arm']==arm];length=np.array([r['loss_tokens'] for r in rr]);total=int(length.sum())
  summaries.append({'arm':arm,'examples':len(rr),'steps':len(steps),'loss_tokens':total,
   'min':int(length.min()),'p10':float(np.quantile(length,.1)),'median':float(np.median(length)),
   'p90':float(np.quantile(length,.9)),'max':int(length.max()),'prompt_leaks':0,'assistant_holes':0,
   'multiple_markers':sum(r['explicit_markers']>1 for r in rr),'no_markers':sum(r['explicit_markers']==0 for r in rr),
   'last_answer_token_mass_fraction':sum(r['last_explicit_answer_tokens'] for r in rr)/total,
   'mean_last_answer_loss_fraction':float(np.mean([r['last_answer_loss_fraction'] for r in rr if r['last_answer_loss_fraction'] is not None])),
   'wrong_token_fraction':sum(r['loss_tokens'] for r in rr if r['correct'] is False)/total,
   'wrong_example_fraction':sum(r['correct'] is False for r in rr)/len(rr),
   'wrong_normalized_loss_coefficient':sum(r['normalized_epoch_loss_coefficient'] for r in rr if r['correct'] is False),
   'last_batch_size':len(steps[-1]['candidate_ids']),'last_batch_question_ids':orders[arm][-1]})
 assert orders[ARMS[0]]==orders[ARMS[1]]==orders[ARMS[2]]
 pair=[]
 indexed={g:{r['question_id']:r for r in details if r['arm']==g} for g in ARMS}
 for other in ARMS[1:]:
  for q,a in indexed[ARMS[0]].items():
   b=indexed[other][q]
   pair.append({'contrast':'Random_SFT-'+other,'question_id':q,'random_loss_tokens':a['loss_tokens'],
    'other_loss_tokens':b['loss_tokens'],'token_difference':a['loss_tokens']-b['loss_tokens'],
    'random_token_coefficient_over_other':b['loss_tokens']/a['loss_tokens'],
    'random_answer_loss_fraction':a['last_answer_loss_fraction'],'other_answer_loss_fraction':b['last_answer_loss_fraction'],
    'same_candidate':a['candidate_id']==b['candidate_id']})
 csv_write(DEST/'supervision_per_question.csv',details);csv_write(DEST/'supervision_summary.csv',summaries)
 csv_write(DEST/'supervision_pairwise.csv',pair)
 return summaries

def feedback():
 allrows=[];metrics=[];quality=[];issues=[]
 common={r['question_id'] for r in read_jsonl(ROOT/'runs/oracle_control_20261022/common_A_questions.jsonl')}
 for source,target in [('G0','G1'),('G1','G2')]:
  lab={r['candidate_id']:r for r in read_jsonl(ROOT/f'runs/formal/labels_{source}_A.jsonl')}
  score={r['candidate_id']:r for r in read_jsonl(ROOT/f'runs/formal/V0_{source}_A.jsonl')}
  selected={r['candidate_id'] for r in read_jsonl(ROOT/f'runs/formal/train/{target}/selected.jsonl')}
  rows=[{**r,**lab[r['candidate_id']],'accepted':score[r['candidate_id']]['accepted'],
   'score':score[r['candidate_id']]['score'],'actual_SFT_selected':r['candidate_id'] in selected,
   'feedback_update':source+'->'+target} for r in read_jsonl(ROOT/f'runs/formal/{source}_A.jsonl')]
  allrows+=rows
  for scenario,bad in [('original_A600',set()),('exclude_missing_image',{IMAGE}),('exclude_image_and_suspect_reference',{IMAGE,REFERENCE_A})]:
   rr=[r for r in rows if r['question_id'] not in bad];metrics.append({'feedback_update':source+'->'+target,'scenario':scenario,**counts(rr)})
   ss=[r for r in rr if r['actual_SFT_selected']];c=sum(r['correct'] is True for r in ss);w=sum(r['correct'] is False for r in ss)
   quality.append({'feedback_update':source+'->'+target,'scenario':scenario,'selected':len(ss),'correct':c,
    'wrong':w,'unknown':len(ss)-c-w,'verified_error_rate':w/(c+w)})
  for q in [IMAGE,REFERENCE_A]:
   rr=[r for r in rows if r['question_id']==q]
   issues.append({'feedback_update':source+'->'+target,'question_id':q,'question':rr[0]['question'],
    'issue':'missing_required_image' if q==IMAGE else 'literal_target_reference_conflict',
    'candidates':len(rr),'actual_training_ids':[r['candidate_id'] for r in rr if r['actual_SFT_selected']],
    'in_common_289':q in common,'human_review_status':'pending','original_labels_unchanged':True})
 csv_write(DEST/'A_feedback_metrics.csv',metrics);csv_write(DEST/'A_training_quality_sensitivity.csv',quality)
 write_json(DEST/'A_problem_issues.json',issues)
 write_jsonl(DEST/'A_misclassified_full.jsonl',[r for r in allrows if r['correct'] is not None and r['correct']!=r['accepted']])
 write_jsonl(DEST/'A_issue_all_candidates.jsonl',[r for r in allrows if r['question_id'] in [IMAGE,REFERENCE_A]])
 return allrows,metrics,quality,issues

def reviews(data,A):
 notes=json.loads(NOTES.read_text());by={r['candidate_id']:r for rr in data.values() for r in rr};by.update({r['candidate_id']:r for r in A})
 full=[];brief=[];lines=['# 缓存误判与同题回答直接初审','',
  '6道C题×3分支、两轮A各3条FP和3条FN，共30条目的抽查。不是总体类型比例估计；助手初审，人工复核待审。V只有A/B分数，没有理由文本，不能把可观测失败模式称为内部决策原因。','']
 for case in notes['cases']:
  r=by[case['candidate_id']]
  for quote in case['quotes']:assert quote in r['text'],(r['candidate_id'],quote)
  row={**r,'review':case,'annotation_source':'chat_assistant_direct_reading_and_exact_checks',
   'human_review_status':'pending','V_internal_reason_known':False};full.append(row)
  brief.append({k:case[k] for k in ['case_id','candidate_id','process_status','error_type','evidence']}|
   {'correct':r['correct'],'accepted':r['accepted'],'score':r['score'],'tokens':r['output_tokens']})
  lines += [f'## {case["case_id"]}: {case["title"]}',r['question'],
   f'ID={r["candidate_id"]}；最终标签={r["correct"]}；V接受={r["accepted"]}；score={r["score"]:.8g}；token={r["output_tokens"]}；实际入训={r.get("actual_SFT_selected","不适用")}。',
   '过程初审='+case['process_status']+'；类型='+case['error_type']+'。',case['evidence'],
   '\n'.join('> '+q for q in case['quotes']),'<details><summary>完整原文</summary>\n\n'+r['text']+'\n\n</details>']
 assert len(full)==30
 write_jsonl(DEST/'reviewed_cases_full.jsonl',full);csv_write(DEST/'reviewed_cases_index.csv',brief)
 (DEST/'case_reviews.md').write_text('\n\n'.join(lines))
 qs={r['question_id'] for r in full if r['split']=='C'}
 write_jsonl(DEST/'C_review_questions_all_candidates.jsonl',[r for rr in data.values() for r in rr if r['question_id'] in qs])
 return brief

def main():
 DEST.mkdir(exist_ok=True)
 paths=[ROOT/'reports/random_control_20261024/candidates_full.jsonl',ROOT/'reports/random_control_20261024/results.json',
  ROOT/'src/pilot/formal_train.py',ROOT/'src/pilot/core.py']
 for directory in TRAIN.values():paths += [directory/name for name in ['selected.jsonl','tokenized.jsonl','steps.jsonl','report.json']]
 for g in ['G0','G1']:paths += [ROOT/f'runs/formal/{pre}{g}_A.jsonl' for pre in ['', 'V0_', 'labels_']]
 hashes={str(p.relative_to(ROOT)):sha(p) for p in paths}
 write_json(DEST/'analysis_plan.json',{'started_at':utc(),'source_sha256':hashes,'bootstrap':10000,'seed':20261022,
  'unit':'Paired whole question; exploratory pointwise95, no multiplicity adjustment, single training seed',
  'review':'Purposive 6 C questions x3 updated models; 3FP+3FN per A round; not prevalence estimation',
  'T_read':False,'GPU_used':False,'originals_read_only':True})
 raw=read_jsonl(paths[0]);data={g:[r for r in raw if r['G_round']==g] for g in MODELS}
 intervals,pts=error_intervals(data);print('Intervals independently reproduced.',flush=True)
 decomposition=by_question(data);sup=supervision();print('1868 supervision tensors checked.',flush=True)
 A,metrics,quality,issues=feedback();case_index=reviews(data,A)
 result=clean({'intervals':intervals,'points':pts,'question_support_decomposition':decomposition,
  'supervision':sup,'feedback_metrics':metrics,'training_quality':quality,'issues':issues,'case_index':case_index})
 write_json(DEST/'analysis.json',result)
 from .cache_followup_export import render,bundle
 render(result)
 assert all(sha(ROOT/n)==h for n,h in hashes.items())
 write_json(DEST/'source_integrity.json',{'unchanged':True,'files_sha256':hashes,'T_read':False})
 write_json(DEST/'validation.json',{'passed':True,'completed_at':utc(),'interval_reproductions':18,
  'tensor_rows':sum(r['examples'] for r in sup),'cases_reviewed':len(case_index),'all_quotes_matched':True,
  'originals_unchanged':True,'GPU_used':False,'T_read':False})
 bundle();print(str(DEST/'cache_analysis_report.md'),flush=True)

if __name__=='__main__':main()
