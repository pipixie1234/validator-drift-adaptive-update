"""Read-only lexical and question-local FPR diagnostics; named methods pending definition.

Same-engine G2/G3 only. Unknown labels never become negatives. No B/T access.
"""
import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy import stats

from .core import ROOT, read_jsonl, write_json, write_jsonl, digest, ensure_run_manifest
from .formal_common import sha, utc
from .g3_common import RUN as SOURCE, pool, validate
from .g3_report import paired
from .v0_drift_analysis import clean, interval
from .explanation_analysis import question_support_decomposition

DEST = ROOT/'reports/fpr_patterns_20261024'
WORK = ROOT/'runs/fpr_patterns_20261024'
CONFIG = ROOT/'configs/fpr_patterns_20261024.json'
SEED = 20261024
STOP = set('the a an and or of to in on at by for from with as is are was were be been being this that these those it its we our us i you your they their them he she his her let then if so can could would should will shall have has had do does did using use used find value values number numbers given get also which where when how what such each any all both into than there here now'.split())
WORDS = {
 'therefore':r'\btherefore\b', 'thus':r'\bthus\b', 'hence':r'\bhence\b',
 'clearly':r'\bclearly\b', 'obviously':r'\bobviously\b', 'certainly':r'\bcertainly\b',
 'we conclude':r'\bwe conclude\b', 'it follows':r'\bit follows\b',
 'final answer':r'\bfinal answer\b', 'answer is':r'\banswer is\b',
 'assume':r'\bassum(?:e|ing)\b', 'suppose':r'\bsuppos(?:e|ing)\b',
 'check':r'\bcheck(?:ing|ed)?\b', 'verify':r'\bverif(?:y|ied|ication)\b',
 'alternatively':r'\balternatively\b', 'however':r'\bhowever\b',
 'note that':r'\bnote that\b', 'must be':r'\bmust be\b',
 'only':r'\bonly\b', 'unique':r'\bunique\b', 'not':r'\bnot\b',
 'possible':r'\bpossible\b', 'case':r'\bcases?\b',
 'step heading':r'(?:^|\n)\s*(?:#+\s*)?step\s+\d',
}
TOPICS = {
 'geometry':r'\b(?:triangle|circle|polygon|hexagon|octagon|pentagon|square|rectangle|angle|radius|radii|perimeter|area|volume|sphere|cube|cylinder|coordinate|parallel|perpendicular)s?\b',
 'number_theory':r'\b(?:prime|divisor|divisible|remainder|modulo|gcd|lcm|coprime|integer|digit|factorization)s?\b',
 'combinatorics':r'\b(?:arrange|arranged|arrangement|permutation|combination|choose|chosen|subset|tiling|tile|coloring|colouring|ways|seating)s?\b',
 'algebra':r'\b(?:polynomial|equation|quadratic|root|function|logarithm|exponential|coefficient|expression|sequence)s?\b',
 'probability':r'\b(?:probability|random|randomly|expected|expectation|dice|die|coin|deck|card)s?\b',
 'rate_word_problem':r'\b(?:speed|mile|hour|minute|distance|price|cost|dollar|worker|percent|percentage|age|year)s?\b',
}


def bh(pvalues):
    """Benjamini-Hochberg including only finite, actually estimable tests."""
    p = np.asarray(pvalues, float); out = np.full(p.shape, np.nan)
    valid = np.flatnonzero(np.isfinite(p)); order = valid[np.argsort(p[valid])]
    if len(order):out[order] = np.minimum(1, np.minimum.accumulate((p[order]*len(order)/np.arange(1,len(order)+1))[::-1])[::-1])
    return out


def group_name(q):
    return 'A_discovery' if int(digest('fpr-patterns:'+q)[:8],16)%2 == 0 else 'A_confirmation'


def freeze():
    validate(); WORK.mkdir(exist_ok=True); DEST.mkdir(exist_ok=True)
    paths=[SOURCE/f'{prefix}{g}_{s}.jsonl' for prefix in ['', 'V0_', 'labels_'] for g in ['G2','G3'] for s in ['A','C']]
    ids={s:[r['question_id'] for r in pool(s)] for s in ['A','C']}
    cfg={'created_for':'Post-hoc G2-G3 FPR lexical associations and question/slice diagnostics',
         'term_status':'EFP and SITE full names/method references requested from user; no named-method reproduction claimed.',
         'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in paths},
         'same_engine':'vLLM0.10.0 BF16 LoRA both G2/G3; fixed original V0 conditional A/B threshold0.5',
         'question_ids':ids,'A_discovery':[q for q in ids['A'] if group_name(q)=='A_discovery'],
         'A_confirmation':[q for q in ids['A'] if group_name(q)=='A_confirmation'],
         'C_role':'Repeated-use development C95, never G training; no untouched final-test claim',
         'seed':SEED,'bootstrap':10000,'unknown':'exclude from FPR denominator; preserve counts and bounds',
         'vocabulary':'lowercase ASCII word unigrams/bigrams, >=3 letters, no stop words; remove LaTeX command names. Built only on wrong A_discovery responses, ignoring V decisions. At least12 question support, >=12 absent questions; max1500 by question prevalence.',
         'lexical_model':'one-feature linear probability regression with question*round fixed effects and log1p(output_tokens); SE cluster by question; two-sided Student t approximate p, min8 directly informative questions',
         'discovery_FDR':'BH separately for pooled word association and word*round interaction, across all finite vocabulary tests; predefined family separate',
         'confirmation':'top12 positive pooled vocabulary coefficients, ranked by discovery BH q then effect, frozen before A_confirmation/C tests; BH across12 for each confirmation domain',
         'word_patterns':WORDS,'topic_patterns':TOPICS,
         'question_tests':'one-sided Fisher exact G3 FPR>G2, independent candidates approximation, BH within each pool; each round <=4 wrong responses so low power, no formal single-question confirmation expected',
         'slice_tests':'nonexclusive question topic regex and prompt-length bins; paired question bootstrap and question-cluster influence SE; FPR delta and delta-minus-complement, BH within split',
         'no_causal_trigger_claim_from_correlations':True,'GPU_used':False,'B_used':False,'T_read':False}
    ensure_run_manifest(CONFIG,cfg)
    return cfg


def load():
    cfg=freeze(); assert all(sha(ROOT/p)==h for p,h in cfg['source_sha256'].items())
    out=[]
    for split in ['A','C']:
        allowed=set(cfg['question_ids'][split])
        for name in ['G2','G3']:
            g=read_jsonl(SOURCE/f'{name}_{split}.jsonl')
            v={r['candidate_id']:r for r in read_jsonl(SOURCE/f'V0_{name}_{split}.jsonl')}
            labs={r['candidate_id']:r for r in read_jsonl(SOURCE/f'labels_{name}_{split}.jsonl')}
            assert len(g)==len(v)==len(labs)==len(allowed)*4
            assert {r['question_id'] for r in g}==allowed
            for r in g:
                vr=v[r['candidate_id']];lab=labs[r['candidate_id']]
                assert vr['threshold']==.5 and vr['V_version']=='V0' and vr['adapter_fingerprint'] is None
                assert vr['accepted']==(vr['score']>=.5)
                assert vr['question_id']==r['question_id']==lab['question_id']
                assert r['engine_settings_sha256']==digest(cfg_engine())
                out.append({k:r[k] for k in ['question_id','candidate_id','candidate_index','question','text','output_tokens','prompt_tokens','truncated']}|
                    {**lab,'G_round':name,'split':split,'view':group_name(r['question_id']) if split=='A' else 'C_development',
                     'accepted':vr['accepted'],'score':vr['score']})
    return out


def cfg_engine():
    from .g3_common import ENGINE
    return ENGINE


def grams(text):
    text=re.sub(r'\\[a-zA-Z]+',' ',text.lower())
    words=re.findall(r"[a-z]+(?:'[a-z]+)?",text)
    uni={w for w in words if len(w)>=3 and w not in STOP}
    bi={' '.join((a,b)) for a,b in zip(words,words[1:]) if len(a)>=3 and len(b)>=3 and a not in STOP and b not in STOP}
    return uni|bi


def feature_matrix(rows,features):
    sets=[grams(r['text']) for r in rows]
    return np.array([[f in words for f in features] for words in sets],float)


def demean(values,groups):
    values=np.asarray(values,float); n=max(groups)+1
    sums=np.zeros((n,)+values.shape[1:]);np.add.at(sums,groups,values)
    means=sums/np.bincount(groups).reshape((-1,)+(1,)*(values.ndim-1))
    return values-means[groups]


def lexical_fit(rows, X, names):
    """One feature at a time; FE(q,round)+log length, sandwich SE(q)."""
    assert len(rows)==len(X) and X.shape[1]==len(names)
    if not rows:return []
    qp={q:i for i,q in enumerate(sorted({r['question_id'] for r in rows}))}
    gp={g:i for i,g in enumerate(sorted({(r['question_id'],r['G_round']) for r in rows}))}
    qi=np.array([qp[r['question_id']] for r in rows]);gi=np.array([gp[r['question_id'],r['G_round']] for r in rows])
    y=np.array([r['accepted'] for r in rows],float);round1=np.array([r['G_round']=='G3' for r in rows],float)
    length=np.log1p([r['output_tokens'] for r in rows]); ld=demean(length,gi); yd=demean(y,gi)
    ll=ld@ld
    def resid(z):
        d=demean(z,gi)
        return d - np.outer(ld,ld@d/ll) if d.ndim==2 and ll>1e-12 else d-ld*(ld@d/ll) if ll>1e-12 else d
    xr=resid(X); yr=resid(y); output=[]
    df=max(1,len(rows)-len(gp)-2);G=len(qp)
    correction=G/max(G-1,1)*(len(rows)-1)/df
    for j,name in enumerate(names):
        x=xr[:,j]; xx=x@x
        # Eligibility uses feature and decision variation in the same q-round.
        xd=demean(X[:,j],gi)
        informative=len({rows[i]['question_id'] for i in range(len(rows)) if abs(xd[i])>1e-10 and abs(yd[i])>1e-10})
        present=X[:,j]>0
        n1=int(present.sum());n0=len(rows)-n1
        result={'feature':name,'wrong_with':n1,'wrong_without':n0,
                'questions_with':len(set(qi[present])),'questions_without':len(set(qi[~present])),
                'FP_with':int(y[present].sum()),'FP_without':int(y[~present].sum()),
                'FPR_with':float(y[present].mean()) if n1 else None,
                'FPR_without':float(y[~present].mean()) if n0 else None,
                'informative_questions':informative,'coefficient':None,'SE':None,'p':None,
                'round_interaction':None,'interaction_p':None,'eligible':False}
        for g in ['G2','G3']:
            use=round1==(g=='G3');pr=present&use;ab=~present&use
            result.update({g+'_wrong_with':int(pr.sum()),g+'_FP_with':int(y[pr].sum()),
                g+'_FPR_with':float(y[pr].mean()) if pr.any() else None,
                g+'_FPR_without':float(y[ab].mean()) if ab.any() else None,
                g+'_prevalence':float(present[use].mean()) if use.any() else None})
        if xx>1e-10 and informative>=8 and G>2:
            beta=(x@yr)/xx;u=yr-beta*x
            sums=np.bincount(qi,weights=x*u,minlength=G)
            se=math.sqrt(correction*float(sums@sums))/xx
            p=2*stats.t.sf(abs(beta/se),G-1) if se>1e-12 else 0. if abs(beta)>1e-12 else 1.
            result.update({'coefficient':float(beta),'SE':se,'p':float(p),'eligible':True})
            z=resid(np.column_stack((X[:,j]*(1-round1),X[:,j]*round1)))
            zz=z.T@z
            if np.linalg.matrix_rank(zz)==2:
                inv=np.linalg.inv(zz);bb=inv@(z.T@yr);uu=yr-z@bb
                ss=np.zeros((G,2));np.add.at(ss,qi,z*uu[:,None]); cov=correction*inv@(ss.T@ss)@inv
                c=np.array([-1.,1.]);variance=float(c@cov@c)
                if variance>1e-14:
                    result.update({'round_interaction':float(c@bb),
                        'interaction_p':float(2*stats.t.sf(abs(float(c@bb))/math.sqrt(variance),G-1))})
        output.append(result)
    for pkey,qkey in [('p','q_BH'),('interaction_p','interaction_q_BH')]:
        qq=bh([r[pkey] if r[pkey] is not None else np.nan for r in output])
        for r,q in zip(output,qq):r[qkey]=float(q) if np.isfinite(q) else None
    return output


def lexical(rows):
    wrong=[r for r in rows if r['correct'] is False]
    discovery=[r for r in wrong if r['view']=='A_discovery']
    supporters=defaultdict(set)
    for r in discovery:
        for f in grams(r['text']):supporters[f].add(r['question_id'])
    total=len({r['question_id'] for r in discovery})
    vocab=sorted((f for f,qs in supporters.items() if len(qs)>=12 and total-len(qs)>=12),
                 key=lambda f:(-len(supporters[f]),f))[:1500]
    write_json(WORK/'vocabulary.json',vocab)
    scan=lexical_fit(discovery,feature_matrix(discovery,vocab),vocab)
    candidates=sorted((r for r in scan if r['eligible'] and r['coefficient']>0),
                      key=lambda r:(r['q_BH'],-r['coefficient'],r['feature']))[:12]
    selected=[r['feature'] for r in candidates]
    ensure_run_manifest(WORK/'selected_word_hypotheses.json',{'features':selected,
         'discovery':'A_discovery only; no confirmation/C decisions inspected in selection',
         'discovery_scan_sha256':digest(clean(scan)),'config_sha256':sha(CONFIG)})
    results=[{'view':'A_discovery','family':'vocabulary',**r} for r in scan]
    for view in ['A_confirmation','C_development']:
        rr=[r for r in wrong if r['view']==view]
        results += [{'view':view,'family':'selected_vocabulary',**r} for r in lexical_fit(rr,feature_matrix(rr,selected),selected)]
    for view in ['A_discovery','A_confirmation','A_all','C_development']:
        rr=[r for r in wrong if (r['split']=='A' if view=='A_all' else r['view']==view)]
        xx=np.array([[bool(re.search(pattern,r['text'],re.I|re.M)) for pattern in WORDS.values()] for r in rr],float)
        results += [{'view':view,'family':'predefined',**r} for r in lexical_fit(rr,xx,list(WORDS))]
    write_csv(DEST/'lexical_associations.csv',results)
    return results,selected


def q_arrays(rows,split):
    qs=sorted(r['question_id'] for r in pool(split));ix={q:i for i,q in enumerate(qs)}
    # correct, wrong, unknown, FP, FN, unknown accepted, output length, truncated
    a=np.zeros((2,len(qs),8),int)
    for r in rows:
        if r['split']!=split:continue
        g=0 if r['G_round']=='G2' else 1;i=ix[r['question_id']]
        y=r['correct'];a[g,i,0 if y is True else 1 if y is False else 2]+=1
        a[g,i,3]+=int(y is False and r['accepted']);a[g,i,4]+=int(y is True and not r['accepted'])
        a[g,i,5]+=int(y is None and r['accepted']);a[g,i,6]+=r['output_tokens'];a[g,i,7]+=r['truncated']
    assert np.all(a[:,:,:3].sum(2)==4)
    return qs,a


def question_scan(rows):
    result=[];summary=[];decomp=[]
    texts={r['question_id']:r['question'] for r in rows}
    for split in ['A','C']:
        qs,a=q_arrays(rows,split);n0=a[0,:,1];n1=a[1,:,1];f0=a[0,:,3];f1=a[1,:,3]
        global0=f0.sum()/n0.sum();global1=f1.sum()/n1.sum()
        decomposition=clean(question_support_decomposition(n0,f0,n1,f1));decomp.append({'split':split,**decomposition})
        for i,q in enumerate(qs):
            b,c=a[0,i],a[1,i];p0=b[3]/b[1] if b[1] else None;p1=c[3]/c[1] if c[1] else None
            common=p0 is not None and p1 is not None
            p=float(stats.fisher_exact([[int(c[3]),int(c[1]-c[3])],[int(b[3]),int(b[1]-b[3])]],alternative='greater').pvalue) if common else None
            contribution=c[3]/n1.sum()-b[3]/n0.sum()
            result.append({'split':split,'question_id':q,'question':texts[q],
                **{g+'_'+k:int(arr[j]) for g,arr in [('G2',b),('G3',c)] for j,k in enumerate(['correct','wrong','unknown','FP','FN','unknown_accepted','length_sum','truncated'])},
                'G2_FPR':p0,'G3_FPR':p1,'delta_FPR':p1-p0 if common else None,
                'p_Fisher_increase':p,'contribution_to_global_FPR_delta':float(contribution),
                'worsening_watchlist':bool(common and b[1]>=2 and c[1]>=2 and p1-p0>=.5),
                'persistent_high_FPR':bool(common and b[1]>=2 and c[1]>=2 and p0>=.75 and p1>=.75),
                'entered_wrong_support':bool(b[1]==0 and c[1]>0),'left_wrong_support':bool(c[1]==0 and b[1]>0)})
        rr=[r for r in result if r['split']==split]
        for r,q in zip(rr,bh([r['p_Fisher_increase'] if r['p_Fisher_increase'] is not None else np.nan for r in rr])):
            r['q_BH_increase']=float(q) if np.isfinite(q) else None
        common=(n0>0)&(n1>0);delta=np.divide(f1,n1,out=np.zeros(len(qs)),where=n1>0)-np.divide(f0,n0,out=np.zeros(len(qs)),where=n0>0)
        ordered=sorted(range(len(qs)),key=lambda i:-(f1[i]/n1.sum()-f0[i]/n0.sum()))
        sensitivity=[]
        for k in [1,5,10,20]:
            keep=np.array([i not in set(ordered[:k]) for i in range(len(qs))])
            sensitivity.append({'removed':k,'remaining_delta_FPR':float(f1[keep].sum()/n1[keep].sum()-f0[keep].sum()/n0[keep].sum()),
                                'post_hoc_sensitivity_not_new_population_estimate':True})
        summary.append({'split':split,'questions':len(qs),'G2_wrong':int(n0.sum()),'G3_wrong':int(n1.sum()),
            'G2_FP':int(f0.sum()),'G3_FP':int(f1.sum()),'G2_FPR':float(global0),'G3_FPR':float(global1),
            'delta_FPR':float(global1-global0),'common_wrong_questions':int(common.sum()),
            'worsening_questions':int(((delta>1e-12)&common).sum()),'improving_questions':int(((delta< -1e-12)&common).sum()),
            'unchanged_questions':int(((abs(delta)<=1e-12)&common).sum()),
            'worsening_watchlist':sum(r['worsening_watchlist'] for r in rr),
            'persistent_high_FPR':sum(r['persistent_high_FPR'] for r in rr),
            'single_question_BH_significant':sum(r['q_BH_increase'] is not None and r['q_BH_increase']<.05 for r in rr),
            'minimum_raw_p':min(r['p_Fisher_increase'] for r in rr if r['p_Fisher_increase'] is not None),
            'top10_positive_contribution':sum(r['contribution_to_global_FPR_delta'] for r in sorted(rr,key=lambda r:-r['contribution_to_global_FPR_delta'])[:10]),
            'trim_sensitivity':sensitivity})
    write_csv(DEST/'question_FPR_scan.csv',result);write_csv(DEST/'question_composition_decomposition.csv',decomp)
    write_json(DEST/'question_scan_summary.json',summary)
    return result,summary,decomp


def rate_and_if(a,mask):
    n=a[:,1]*mask;f=a[:,3]*mask;den=n.sum()
    if den==0:return np.nan,np.full(len(mask),np.nan)
    p=f.sum()/den
    return p,(f-p*n)/den


def slices(rows):
    result=[]
    for split in ['A','C']:
        qs,a=q_arrays(rows,split);inputs={r['question_id']:r for r in pool(split)}
        masks={k:np.array([bool(re.search(p,inputs[q]['question'],re.I)) for q in qs]) for k,p in TOPICS.items()}
        for name,lo,hi in [('prompt<=128',0,128),('prompt129-256',129,256),('prompt>256',257,9999)]:
            masks[name]=np.array([lo<=inputs[q]['prompt_tokens_prechecked']<=hi for q in qs])
        indices=np.random.default_rng(SEED).integers(len(qs),size=(10000,len(qs)))
        for name,mask in masks.items():
            n0=(a[0,:,1]*mask);n1=(a[1,:,1]*mask);f0=a[0,:,3]*mask;f1=a[1,:,3]*mask
            p0,i0=rate_and_if(a[0],mask);p1,i1=rate_and_if(a[1],mask)
            out0,io0=rate_and_if(a[0],~mask);out1,io1=rate_and_if(a[1],~mask)
            if not (mask.sum()>=10 and (~mask).sum()>=10 and min(n0.sum(),n1.sum())>=10):continue
            delta=p1-p0; interaction=delta-(out1-out0)
            infl=(i1-i0);intinfl=infl-(io1-io0)
            se=math.sqrt(len(qs)/(len(qs)-1)*np.sum(infl**2));sei=math.sqrt(len(qs)/(len(qs)-1)*np.sum(intinfl**2))
            with np.errstate(invalid='ignore',divide='ignore'):
                boot=f1[indices].sum(1)/n1[indices].sum(1)-f0[indices].sum(1)/n0[indices].sum(1)
                other=(a[1,:,3]*(~mask))[indices].sum(1)/(a[1,:,1]*(~mask))[indices].sum(1)-(a[0,:,3]*(~mask))[indices].sum(1)/(a[0,:,1]*(~mask))[indices].sum(1)
            result.append({'split':split,'slice':name,'questions':int(mask.sum()),'G2_wrong':int(n0.sum()),'G3_wrong':int(n1.sum()),
                'G2_FP':int(f0.sum()),'G3_FP':int(f1.sum()),'G2_FPR':float(p0),'G3_FPR':float(p1),
                'delta':float(delta),**interval(boot),'p':float(2*stats.t.sf(abs(delta/se),len(qs)-1)) if se>1e-12 else None,
                'delta_minus_complement':float(interaction),'interaction_low':interval(boot-other)['low'],'interaction_high':interval(boot-other)['high'],
                'interaction_p':float(2*stats.t.sf(abs(interaction/sei),len(qs)-1)) if sei>1e-12 else None,
                'G2_unknown':int((a[0,:,2]*mask).sum()),'G3_unknown':int((a[1,:,2]*mask).sum()),
                'nonexclusive_regex_topic_proxy':True})
    for split in ['A','C']:
        rr=[r for r in result if r['split']==split]
        for pk,qk in [('p','q_BH'),('interaction_p','interaction_q_BH')]:
            for r,q in zip(rr,bh([r[pk] if r[pk] is not None else np.nan for r in rr])):r[qk]=float(q) if np.isfinite(q) else None
    write_csv(DEST/'question_topic_slices.csv',result)
    return result


def write_csv(path,rows):
    import csv
    if not rows:path.write_text('');return
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fields);w.writeheader()
        for row in clean(rows):w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=['freeze','analyze'],default='analyze');args=p.parse_args()
    if args.stage=='freeze':print(json.dumps(freeze(),ensure_ascii=False));return
    rows=load();print('Cache identities verified; analyzing lexical features.',flush=True)
    lex,features=lexical(rows);print('Lexical scan complete; analyzing question support.',flush=True)
    qq,summary,decomp=question_scan(rows);ss=slices(rows)
    overall={}
    for split in ['A','C']:
        view={g:[r for r in rows if r['G_round']==g and r['split']==split] for g in ['G2','G3']}
        pt,bb=paired(view,sorted(r['question_id'] for r in pool(split)))
        overall[split]={'point':pt,'FPR_delta_CI':interval(bb['G3']['FPR']-bb['G2']['FPR'])}
    write_json(DEST/'results.json',{'completed_at':utc(),'overall':overall,'selected_features':features,
        'question_summary':summary,'decomposition':decomp,'topic_slices':ss,
        'terms_pending_confirmation':True,'GPU_used':False,'T_read':False})
    write_json(WORK/'state.json',{'status':'CPU_analysis_complete','completed_at':utc(),'source_hashes':json.loads(CONFIG.read_text())['source_sha256']})
    print(json.dumps({'complete':True,'destination':str(DEST),'selected_features':features,'question_summary':summary},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
