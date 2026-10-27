"""One-shot week-seven evaluation, frozen before first T decoding."""
import json
import shutil
from pathlib import Path

from .core import ROOT,read_jsonl,write_json,write_jsonl,ensure_run_manifest,digest
from .formal_common import protocol,verify_fixed,sha,utc
from .runtime import model_identity
from .g3_common import ENGINE,RUN as G3_RUN,pool
from .verifier_update import join_source

RUN=ROOT/'runs/week_final_20261026'
REPORT=ROOT/'reports/week_final_20261026'
PLAN=ROOT/'configs/protocol_revisions/005_week_final_20261026.json'
GS=['G0','G1','G2','G3'];VS=['V0','Vrand','Vtarget']
TINPUT=ROOT/'data/dapo/formal/inputs/T_final.jsonl'
TLABEL=ROOT/'data/dapo/formal/labels/T_final.jsonl'
REVIEW_QIDS=['87652cb8-e3f0-4eef-872b-08f1606efdc2','beffc63a-0f60-4bda-8094-ec6eca603add',
             'b62d6a00-8422-4836-aa09-d6db0186d28d','0c73160f-95ca-4cc6-a4c3-dd641342f0b5','96c3a3c6-22fd-4d06-9d7d-3af786201960']


def gadapter(g):
    return None if g=='G0' else ROOT/f'runs/formal/checkpoints/{g}_SFT' if g in ['G1','G2'] else G3_RUN/'checkpoints/G3_SFT'


def vadapter(v):
    return None if v=='V0' else ROOT/f'runs/verifier_update_20261025/{v}/adapter'


def state(stage,**kw):write_json(RUN/'state.json',{'status':stage,'at':utc(),**kw})


def preserve():
    path=RUN/'preserved_existing_files.json'
    if path.exists():return
    rows={}
    for name in ['runs','reports','configs']:
        for p in sorted((ROOT/name).rglob('*')):
            if not p.is_file() or p.is_symlink() or p==PLAN or RUN in p.parents or REPORT in p.parents:continue
            if p.name.startswith('week_final_20261026') or p==ROOT/'runs/formal/work_ledger.jsonl' or p.name=='.gpu.lock':continue
            rows[str(p.relative_to(ROOT))]=sha(p)
    write_json(path,rows);shutil.copyfile(ROOT.parent/'一周前置实验方案_20261020.md',RUN/'prior_research_plan.md')


def freeze():
    RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    if PLAN.exists():return validate()
    verify_fixed();preserve()
    split_path=ROOT/'data/dapo/formal/manifest.json';split=json.loads(split_path.read_text())
    assert sha(TINPUT)==split['splits']['T_final']['input_sha256'] and sha(TLABEL)==split['splits']['T_final']['labels_sha256']
    checkpoints={};paths=[split_path,TINPUT,TLABEL,ROOT/'configs/protocol_revisions/003_dapo_day3.json',
       ROOT/'configs/protocol_revisions/004_g3_extension_20261024.json',ROOT/'configs/verifier_update_20261025.json',
       ROOT/'configs/dapo_answer_v3.json',ROOT/'src/pilot/runtime.py',ROOT/'src/pilot/day2_protocol.py',
       Path(__file__),ROOT/'src/pilot/week_final_generate.py',ROOT/'src/pilot/week_final_run.py',
       ROOT/'src/pilot/week_final_reasons.py',ROOT/'src/pilot/verifier_update.py',ROOT/'src/pilot/core.py']
    for role,names,fn in [('G',GS,gadapter),('V',VS,vadapter)]:
        for name in names:
            a=fn(name);base,ad=model_identity(role,a)
            checkpoints[name]={'role':role,'base_fingerprint':base,'adapter_fingerprint':ad,'adapter_path':str(a) if a else None}
            if a:paths += [a/'adapter_model.safetensors',a/'adapter_config.json']
    # Reason cohort is a transparent diagnostic selection from existing C, never T.
    reason=[];allowed={r['question_id'] for r in pool('C')}
    for g in GS:
        rr,pp=join_source(ROOT/'runs/formal' if g!='G3' else G3_RUN,g,'C',allowed)
        paths+=pp
        for q in REVIEW_QIDS:
            r=next(r for r in rr if r['question_id'].endswith(q) and r['candidate_index']==0)
            assert r['correct'] is not None and not r['truncated']
            reason.append(r)
    write_jsonl(RUN/'reason_inputs_C20.jsonl',reason)
    cfg={'revision':'005_week_final_20261026','frozen_at':utc(),
      'authorization':'User2026-10-26: finish all original seven-day deliverables before further mechanism work; includes planned Day7 final T evaluation',
      'prior_T_status':'No T decoding/inference before this configuration freeze; only manifest IDs and hashes checked',
      'T_input_path':str(TINPUT),'T_labels_path':str(TLABEL),'T_questions':300,'candidates_per_question':4,
      'checkpoints':checkpoints,'primary_G':['G0','G1','G2'],'secondary_G':['G3'],'verifiers':VS,
      'G_engine':{'name':'vLLM','settings':ENGINE,'uniform_all_four_G':True,
        'sampling':{'n':4,'temperature':.7,'top_p':.9,'top_k':50,'seed':'42+int(digest(question_id)[:8],16)%1000000',
                    'stop_token_ids':[151645,151643],'max_context':4096,'max_prompt':1024,'max_new_tokens':'min(3840,4096-exact_prompt_tokens)'},
        'limit':'Same vLLM BF16 runtime for all T stages; old TF C curves are not numerically identical sampling. Original G1/G2 training artifacts retained including disclosed source defects.'},
      'V_scoring':{'implementation':'original Transformers full A/B conditional likelihood, BF16 base/PEFT adapter runtime',
                   **protocol()['V_scoring'],'threshold':.5,'ties':'accept when score>=0.5','prompts':protocol()['prompts']},
      'T_question_quality':'Preserve all300 and all candidates; blind textual redflags recorded before generation. No outcome-based exclusions or reference repairs; unsupported/missing/conflicting/truncated answers unknown. Any independently confirmed source failure would be whole-question, all G/V, versioned sensitivity only.',
      'T_firewall':'Freeze all weights, prompts, thresholds and sampling before decoding T. No retraining, tuning, checkpoint choice or prefix mining after T. Fixed one trajectory and one V update budget; negative results retained.',
      'analysis':{'questions':'same300 paired across G/V','bootstrap':10000,'seed':20261026,
        'primary_contrasts':['Vtarget-Vrand on G2 FPR/FNR','Vrand-V0 and Vtarget-V0 on G2 FPR/FNR','V0 G2-G0 FPR/FNR'],
        'secondary':'G3 extension, historical G0/G1 retention, BA/AUROC/FDR/coverage/unknown/length/truncation, exact-score ties and fixed-answer correctness transitions',
        'inference':'Paired whole-question percentile95% intervals, exploratory multiple contrasts without simultaneous coverage; one training seed. Do not claim equivalence from non-significance.'},
      'rationale_review':{'split':'C only','answers':20,'versions':VS,'generations':60,'question_ids':REVIEW_QIDS,
        'sampling':'Five previously mathematically reviewed development questions x four G stages, candidate0; selected for feasible exact review, not population prevalence. Includes TP/FP/FN/TN globally; some stage/cell combinations absent.',
        'separate_protocol':'Independent concise verification then VERDICT:A/B; not the latent reason behind original A/B-only score',
        'generation':{'engine':'same Transformers/PEFT runtime','do_sample':False,'max_new_tokens':768,'max_context':8192,'batch_size':4,'max_words_requested':180},
        'review':'Chat assistant evidence-bearing initial annotations, exact computation where feasible; human columns left pending, not human gold. No rationale or step labels used for training.',
        'G_steps':'Preserve paragraph spans/character positions; record inspected process correctness and first witnessed error, later steps not automatically wrong; no PRM training'},
      'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in paths},
      'reason_inputs_sha256':sha(RUN/'reason_inputs_C20.jsonl'),'new_training':False,'prefix_experiments':False,
      'future_work_excluded':['GRPO independent branch','large-scale process labels or PRM training','multi-model ability/difficulty grid','hyperparameter repair of failed V updates'],
      'deliverables':['T G0/G1/G2(+G3) x V0/Vrand/Vtarget matrix','paired intervals and full raw caches','20C answers x3V rationale generation and assistant initial review','G step review workbook','seven-day status and final short report']}
    ensure_run_manifest(PLAN,cfg);state('protocol_frozen_T_not_decoded');return cfg


def validate():
    cfg=json.loads(PLAN.read_text());verify_fixed()
    assert all(sha(ROOT/n)==h for n,h in cfg['source_sha256'].items())
    assert sha(RUN/'reason_inputs_C20.jsonl')==cfg['reason_inputs_sha256']
    for name,r in cfg['checkpoints'].items():
        assert model_identity(r['role'],Path(r['adapter_path']) if r['adapter_path'] else None)==(r['base_fingerprint'],r['adapter_fingerprint'])
    return cfg


def unseal():
    cfg=validate();path=RUN/'T_inputs.jsonl'
    if path.exists():return read_jsonl(path)
    rows=read_jsonl(TINPUT);assert len(rows)==300 and len({r['question_id'] for r in rows})==300
    manifest=json.loads((ROOT/'data/dapo/formal/manifest.json').read_text())
    tids={r['question_id'] for r in rows}
    assert tids==set(manifest['splits']['T_final']['question_ids'])
    for name,s in manifest['splits'].items():
        if name!='T_final':assert not tids&set(s['question_ids'])
    assert not tids&{r['question_id'] for r in read_jsonl(ROOT/'data/dapo/inputs/dapo100.jsonl')}
    assert all(not any(k in r for k in ['gold_answer','correct','reference']) for r in rows)
    import re
    flags=[{'question_id':r['question_id'],'pattern':p,'question':r['question'],'status':'flag_only_no_exclusion'}
       for r in rows for p in [r'\b(?:figure|diagram|picture|image)\b',r'\[asy\]|<image>|!\['] if re.search(p,r['question'],re.I)]
    write_jsonl(RUN/'T_blind_source_flags.jsonl',flags);write_jsonl(path,rows)
    write_json(RUN/'T_first_unseal.json',{'at':utc(),'plan_sha256':sha(PLAN),'questions':300,'input_sha256':sha(path),
        'question_flags':len(flags),'exclusions':[],'weights_and_settings_frozen_before_T':True,'T_labels_decoded':False})
    state('T_unsealed_for_final_only',questions=300,source_flags=len(flags));return rows
