"""User-defined SITE: fixed-input slices, conditional FPR and heterogeneity."""
import json
import math
import re

import numpy as np
from scipy import stats

from .core import ROOT, write_json, digest, ensure_run_manifest
from .formal_common import sha, utc
from .fpr_pattern_diagnostics import load, pool, WORK, DEST, CONFIG, SEED, q_arrays, rate_and_if, bh, write_csv
from .v0_drift_analysis import interval, clean
from .efp_intervention import PLAN, opening_grams

SITE_PLAN = WORK/'SITE_input_slices_frozen.json'
STRONG_TOPICS = {
 'geometry':r'\b(?:triangle|circle|polygon|hexagon|octagon|pentagon|rectangle|angle|radius|radii|perimeter|area|volume|sphere|cylinder|coordinate|parallel|perpendicular|diagonal|vertex|vertices)s?\b|\b(?:square|cube)\s+(?:has|with|whose|of side|of edge)|\b(?:side|edge)\s+(?:length|of (?:a |the )?(?:square|cube))',
 'probability':r'\b(?:probability|random|randomly|expected|expectation|dice|die|coin|deck|card)s?\b',
 'combinatorics':r'\b(?:arrange|arranged|arrangement|permutation|combination|choose|chosen|subset|tiling|tile|coloring|colouring|ways|seating)s?\b',
 'algebra':r'\b(?:polynomial|equation|quadratic|root|function|logarithm|exponential|coefficient|expression|sequence)s?\b',
 'number_theory':r'\b(?:prime|divisor|divisible|remainder|modulo|gcd|lcm|coprime|integer|digit|factorization)s?\b',
 'rate_word_problem':r'\b(?:speed|mile|hour|minute|distance|price|cost|dollar|worker|percent|percentage|age|year)s?\b',
}
SYMBOLS = {
 'sqrt':r'\\sqrt\b|√',
 'fraction':r'\\(?:d?frac)\b',
 'summation':r'\\sum\b|∑',
 'integral':r'\\int\b|∫',
 'matrix':r'\\begin\{(?:[bpvBV]?matrix|array)\}|\\matrix\b',
 'modulo':r'\\(?:pmod|bmod)\b|\bmod(?:ulo)?\b',
 'inequality':r'\\(?:leq?|geq?|lt|gt)\b|[<>≤≥]',
 'floor_ceiling':r'\\(?:lfloor|rfloor|lceil|rceil)\b|[⌊⌋⌈⌉]',
 'binomial':r'\\binom\b',
}


def math_density(text):
    return sum(c.isdigit() or c in '\\$^_{}=+-*/<>≤≥√∑∫' for c in text)/max(len(text),1)


def topic(question):
    for name,pattern in STRONG_TOPICS.items():
        if re.search(pattern,question,re.I):return name
    return 'other'


def prepare():
    # Features and thresholds use question text only; no response/V outcomes.
    a=pool('A');threshold=float(np.median([math_density(r['question']) for r in a]))
    cfg={'definition':'User-defined SITE: input-bound systematic error; conditional FPR slices across G2/G3 under fixed V0',
         'base_config_sha256':sha(CONFIG),'topics':STRONG_TOPICS,'topic_priority':list(STRONG_TOPICS)+['other'],
         'topic_label_status':'Transparent keyword proxy, not dataset official/expert math taxonomy; square/cube alone not geometry',
         'symbols':SYMBOLS,'math_density_threshold_A_input_median':threshold,
         'additional_slices':['prompt<=128','prompt129-256','prompt>256','math_density_high','clauses>=3'],
         'clauses':'Count comma, semicolon and colon >=3; lexical complexity proxy only',
         'minimum_primary_support':{'questions':10,'wrong_per_round':10},
         'all_sparse_slices_exported':'including integral/matrix absent counts; excluded from inferential families, never relabelled absent effects',
         'variance':'Exclusive keyword topic groups; common eligible groups; population variance(ddof0) and fixed question-weighted variance; same groups/weights each round',
         'intervals':'10000 paired question bootstrap seed20261024; per-slice and variance95 intervals; no training-seed uncertainty',
         'pvalues':'Student t on question-cluster influence of FPR differences; BH within each pool across all eligible slices, separately interaction vs complement',
         'causality':'Fixed V parameter ability does not change; risk varies with G output conditional on input. Slice heterogeneity and composition alone do not identify an internal mechanism',
         'B_used':False,'T_read':False}
    ensure_run_manifest(SITE_PLAN,cfg);return cfg


def input_features():
    cfg=prepare();details=[]
    for split in ['A','C']:
        for r in pool(split):
            q=r['question'];features={'topic:'+k:bool(re.search(p,q,re.I)) for k,p in STRONG_TOPICS.items()}
            features.update({'symbol:'+k:bool(re.search(p,q,re.I)) for k,p in SYMBOLS.items()})
            n=r['prompt_tokens_prechecked'];features.update({'prompt<=128':n<=128,'prompt129-256':128<n<=256,'prompt>256':n>256,
                'math_density_high':math_density(q)>=cfg['math_density_threshold_A_input_median'],
                'clauses>=3':sum(q.count(x) for x in ',;:')>=3})
            details.append({'split':split,'question_id':r['question_id'],'question':q,'topic_exclusive':topic(q),
                'prompt_tokens':n,'math_density':math_density(q),**features})
    write_csv(DEST/'SITE_question_input_features.csv',details)
    return details


def analyze():
    rows=load();features=input_features();lookup={r['question_id']:r for r in features}
    feature_names=[k for k in features[0] if k.startswith(('topic:','symbol:','prompt')) or k in ['math_density_high','clauses>=3']]
    output=[];variance=[];exclusive=[]
    for split in ['A','C']:
        qs,a=q_arrays(rows,split);G=len(qs)
        indices=np.random.default_rng(SEED).integers(G,size=(10000,G))
        for feature in feature_names:
            mask=np.array([lookup[q][feature] for q in qs]);b0=a[0]*mask[:,None];b1=a[1]*mask[:,None]
            n0,n1=b0[:,1].sum(),b1[:,1].sum();f0,f1=b0[:,3].sum(),b1[:,3].sum()
            eligible=bool(mask.sum()>=10 and (~mask).sum()>=10 and min(n0,n1)>=10)
            p0,i0=rate_and_if(a[0],mask);p1,i1=rate_and_if(a[1],mask)
            c0,ic0=rate_and_if(a[0],~mask);c1,ic1=rate_and_if(a[1],~mask)
            row={'split':split,'feature':feature,'questions':int(mask.sum()),'G2_wrong':int(n0),'G3_wrong':int(n1),
                 'G2_FP':int(f0),'G3_FP':int(f1),'G2_FPR':float(p0),'G3_FPR':float(p1),
                 'G2_unknown':int(b0[:,2].sum()),'G3_unknown':int(b1[:,2].sum()),
                 'G2_truncated':int(b0[:,7].sum()),'G3_truncated':int(b1[:,7].sum()),
                 'delta':float(p1-p0),'delta_minus_complement':float((p1-p0)-(c1-c0)),
                 'eligible':eligible,'p':None,'interaction_p':None}
            if eligible:
                with np.errstate(divide='ignore',invalid='ignore'):
                    boot=b1[indices,3].sum(1)/b1[indices,1].sum(1)-b0[indices,3].sum(1)/b0[indices,1].sum(1)
                    z0=a[0]*(~mask)[:,None];z1=a[1]*(~mask)[:,None]
                    bootother=z1[indices,3].sum(1)/z1[indices,1].sum(1)-z0[indices,3].sum(1)/z0[indices,1].sum(1)
                row.update(interval(boot));ci=interval(boot-bootother)
                row.update({'interaction_low':ci['low'],'interaction_high':ci['high']})
                se=math.sqrt(G/(G-1)*np.sum((i1-i0)**2));ise=math.sqrt(G/(G-1)*np.sum((i1-i0-ic1+ic0)**2))
                if se>1e-12:row['p']=float(2*stats.t.sf(abs(row['delta'])/se,G-1))
                if ise>1e-12:row['interaction_p']=float(2*stats.t.sf(abs(row['delta_minus_complement'])/ise,G-1))
            output.append(row)
        topics=list(STRONG_TOPICS)+['other'];eligible_topics=[];rates0=[];rates1=[];boot0=[];boot1=[];weights=[]
        for t in topics:
            mask=np.array([lookup[q]['topic_exclusive']==t for q in qs]);z0=a[0]*mask[:,None];z1=a[1]*mask[:,None]
            p0,_=rate_and_if(a[0],mask);p1,_=rate_and_if(a[1],mask)
            eligible=bool(mask.sum()>=10 and min(z0[:,1].sum(),z1[:,1].sum())>=10)
            exclusive.append({'split':split,'topic':t,'questions':int(mask.sum()),
                'G2_wrong':int(z0[:,1].sum()),'G3_wrong':int(z1[:,1].sum()),
                'G2_FP':int(z0[:,3].sum()),'G3_FP':int(z1[:,3].sum()),'G2_FPR':p0,'G3_FPR':p1,'eligible_for_variance':eligible})
            if eligible:
                with np.errstate(divide='ignore',invalid='ignore'):
                    boot0.append(z0[indices,3].sum(1)/z0[indices,1].sum(1));boot1.append(z1[indices,3].sum(1)/z1[indices,1].sum(1))
                eligible_topics.append(t);rates0.append(p0);rates1.append(p1);weights.append(mask.sum())
        if len(eligible_topics)>=2:
            r0=np.asarray(rates0);r1=np.asarray(rates1);bb0=np.asarray(boot0).T;bb1=np.asarray(boot1).T
            for name,w in [('unweighted',np.ones(len(r0))/len(r0)),('fixed_question_weights',np.array(weights)/sum(weights))]:
                v0=float(np.sum(w*(r0-np.sum(w*r0))**2));v1=float(np.sum(w*(r1-np.sum(w*r1))**2))
                bvar0=np.sum(w*(bb0-np.sum(w*bb0,1)[:,None])**2,1)
                bvar1=np.sum(w*(bb1-np.sum(w*bb1,1)[:,None])**2,1)
                variance.append({'split':split,'weighting':name,'groups':eligible_topics,'G2_variance':v0,'G3_variance':v1,
                    'delta':v1-v0,**interval(bvar1-bvar0),'units':'squared fraction; multiply10000 for squared percentage points'})
    for split in ['A','C']:
        rr=[r for r in output if r['split']==split and r['eligible']]
        for pk,qk in [('p','q_BH'),('interaction_p','interaction_q_BH')]:
            for r,q in zip(rr,bh([r[pk] if r[pk] is not None else np.nan for r in rr])):r[qk]=float(q) if np.isfinite(q) else None
    write_csv(DEST/'SITE_input_slice_FPR.csv',output);write_csv(DEST/'SITE_exclusive_topic_FPR.csv',exclusive)
    write_csv(DEST/'SITE_conditional_FPR_variance.csv',variance)
    write_json(DEST/'SITE_results.json',clean({'completed_at':utc(),'slices':output,'variance':variance,'exclusive_topics':exclusive,
        'method_definition_received_from_user':True,'question_feature_plan_sha256':sha(SITE_PLAN),'B_used':False,'T_read':False}))
    natural_patterns(rows)
    print(json.dumps(clean({'complete':True,'variance':variance}),ensure_ascii=False),flush=True)


def natural_patterns(rows):
    plan=json.loads(PLAN.read_text())
    patterns=[v for v in plan['variants'] if v['name'] not in ['original','blank_control','neutral_control']]
    out=[]
    for view in ['A_discovery','A_confirmation','A_all','C_development']:
        for name in ['G2','G3']:
            rr=[r for r in rows if r['G_round']==name and (r['split']=='A' if view=='A_all' else r['view']==view)]
            for v in patterns:
                def matches(r):
                    if 'pattern' in v:return v['pattern'] in opening_grams(r['text'])
                    if v['name'].startswith('symbol_'):return r['text'].lstrip().startswith(v['prefix'].strip())
                    return v['prefix'].strip().lower() in r['text'].lower()
                for condition in ['all_outputs','all_wrong','FP','TN']:
                    ss=[r for r in rr if condition=='all_outputs' or (r['correct'] is False and (condition=='all_wrong' or r['accepted']==(condition=='FP')))]
                    present=[r for r in ss if matches(r)]
                    out.append({'view':view,'model':name,'variant':v['name'],'prefix':v['prefix'],'condition':condition,
                        'responses':len(ss),'with_pattern':len(present),'prevalence':len(present)/len(ss) if ss else None,
                        'with_pattern_questions':len({r['question_id'] for r in present})})
    write_csv(DEST/'EFP_natural_pattern_prevalence.csv',out)


if __name__=='__main__':analyze()
