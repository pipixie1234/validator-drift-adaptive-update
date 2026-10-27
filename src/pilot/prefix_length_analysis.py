"""CPU-only exploratory length analysis of already frozen prefix experiments."""
import json
import re
import shutil
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
from scipy import stats

from .core import ROOT, CFG, read_jsonl, write_json, ensure_run_manifest
from .formal_common import sha, utc
from .prefix_paraphrase_report import load_scores as load_paraphrases
from .prefix_paraphrase import PLAN as PREFIX_PLAN, RUN as PREFIX_RUN
from .awl_comparison_report import load_scores as load_awl
from .awl_comparison import PLAN as AWL_PLAN, RUN as AWL_RUN
from .fpr_pattern_diagnostics import write_csv

RUN=ROOT/'runs/prefix_length_20261025'
REPORT=ROOT/'reports/prefix_length_20261025'
PLAN=ROOT/'configs/prefix_length_20261025.json'
METRICS=['words','characters','prefix_tokens','prompt_token_delta']


def lengths(prefix,tok):
    return {'words':len(re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?",prefix)),
        'characters':len(prefix.strip()),'raw_characters':len(prefix),
        'prefix_tokens':len(tok.encode(prefix,add_special_tokens=False)),
        'tokens_without_edge_whitespace':len(tok.encode(prefix.strip(),add_special_tokens=False))}


def within(values,categories):
    v=np.asarray(values,float).copy();labels=np.asarray(categories)
    for c in set(categories):
        mask=labels==c;v[...,mask]-=v[...,mask].mean(axis=-1,keepdims=True)
    return v


def slope(x,y,categories=None):
    x=np.asarray(x,float);y=np.asarray(y,float)
    if categories is None:
        xc=x-x.mean(axis=-1,keepdims=True);yc=y-y.mean(axis=-1,keepdims=True)
    else:xc=within(x,categories);yc=within(y,categories)
    den=np.sum(xc*xc,axis=-1);num=np.sum(xc*yc,axis=-1)
    return np.divide(num,den,out=np.full_like(num,np.nan,dtype=float),where=den>1e-12)


def bootstrap_slopes(x,Y,categories,draws=10000,seed=20261025):
    """Y: question x prefix. Resample each question with its entire prefix row."""
    x=np.asarray(x,float);Y=np.asarray(Y,float);n,p=Y.shape
    assert len(x)==p==len(categories)
    rng=np.random.default_rng(seed)
    weights=rng.multinomial(n,np.full(n,1/n),size=draws)
    rates=weights@Y/n
    # Prefix bootstrap keeps the category counts fixed and each full prefix identity.
    order=[];labels=[]
    for c in sorted(set(categories)):
        ids=np.flatnonzero(np.asarray(categories)==c)
        order.append(rng.choice(ids,size=(draws,len(ids)),replace=True));labels.extend([c]*len(ids))
    picked=np.concatenate(order,axis=1)
    crossx=x[picked];crossy=np.take_along_axis(rates,picked,axis=1)
    out=[]
    for adjusted in [False,True]:
        cats=categories if adjusted else None;crosscats=labels if adjusted else None
        estimate=float(slope(x,Y.mean(0),cats))
        q=slope(x,rates,cats);cross=slope(crossx,crossy,crosscats)
        finite=cross[np.isfinite(cross)];qfinite=q[np.isfinite(q)]
        lo,hi=np.quantile(qfinite,[.025,.975]);cl,ch=np.quantile(finite,[.025,.975])
        out.append({'model':'category_fixed_effects' if adjusted else 'unadjusted','slope':estimate,
            'question_low':float(lo),'question_high':float(hi),'crossed_low':float(cl),'crossed_high':float(ch),
            'crossed_valid_draws':len(finite),'draws':draws,'prefixes':p,'questions':n})
    return out


def main():
    RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    pcfg,pdata,pby,pscores=load_paraphrases();acfg,arr,ascores,aby=load_awl()
    assert {r['candidate_id'] for r in pdata['prefix']}=={r['candidate_id'] for r in arr}
    paths=[PREFIX_PLAN,PREFIX_RUN/'V0_scores.jsonl',PREFIX_RUN/'prefix_answers.jsonl',
        AWL_PLAN,AWL_RUN/'V0_scores.jsonl',AWL_RUN/'answers.jsonl',Path(__file__)]
    cfg={'created_at':utc(),'kind':'Post-hoc exploratory analysis of existing cached scores; no new V or G inference',
       'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in paths},
       'main_view':'38 verbal prefixes: exclude3 controls and5 repeated surface-format treatments from old46',
       'sensitivity_views':['all43 noncontrol prefixes','31 excluding reasoning headers as well','40 AWL/nonAWL single-word verb headers with identical colon/newline'],
       'length_metrics':METRICS,'characters_definition':'len(prefix.strip()); includes punctuation and internal whitespace, excludes edge whitespace',
       'prefix_tokens_definition':'Original V0 tokenizer.encode(exact prefix,add_special_tokens=False), including final newline',
       'prompt_token_delta':'Median actual full-V-prompt length increment from already cached scores; min/max exported',
       'outcomes':['Original TN -> FP flip rate','Mean accept/reject log-odds change vs original'],
       'association':'Spearman descriptive ranks; OLS rate slope, with/without frozen original category fixed effects',
       'uncertainty':'10000 paired whole-question bootstrap; extra crossed resampling of prefixes within category with fixed category counts. No hypothesis p-values or confirmatory significance claim.',
       'vocabulary_limit':'Purposively chosen prefixes, not random language samples; crossed intervals are sensitivity checks, not population guarantees',
       'B_used':False,'T_read':False,'GPU_used':False,'V_update':False,'seed':20261025}
    if PLAN.exists():
        frozen=json.loads(PLAN.read_text());assert frozen['source_sha256']==cfg['source_sha256'];cfg=frozen
    else:ensure_run_manifest(PLAN,cfg)
    from transformers import AutoTokenizer
    tok=AutoTokenizer.from_pretrained(CFG['V_MODEL_DIR'],local_files_only=True)
    entries=[];matrices={};logmats={};answers_by={s:sorted([r for r in pdata['prefix'] if r['split']==s],key=lambda r:r['question_id']) for s in ['A','C']}
    catalogs={'phrases':pcfg['variants']['prefix'],'AWL_headers':acfg['variants']}
    for exp,vv in catalogs.items():
        for split,rr in answers_by.items():
            mats=[];logs=[]
            for v in vv:
                vals=[pby['prefix',r['candidate_id']][v['name']] if exp=='phrases' else aby[r['candidate_id'],v['name']] for r in rr]
                base=[pby['prefix',r['candidate_id']]['original'] if exp=='phrases' else aby[r['candidate_id'],'original'] for r in rr]
                y=np.array([s['accepted'] for s in vals],float)
                ld=np.array([s['log_likelihood_A']-s['log_likelihood_B']-b['log_likelihood_A']+b['log_likelihood_B'] for s,b in zip(vals,base)])
                increments=[s['prompt_tokens']-b['prompt_tokens'] for s,b in zip(vals,base)]
                e={'experiment':exp,'split':split,'variant':v['name'],'prefix':v['prefix'],
                   'category':v.get('category',v.get('group')),'responses':len(rr),'flips':int(y.sum()),'flip_rate':float(y.mean()),
                   'mean_log_odds_delta':float(ld.mean()),**lengths(v['prefix'],tok),
                   'prompt_token_delta':float(np.median(increments)),'prompt_token_delta_min':min(increments),'prompt_token_delta_max':max(increments)}
                entries.append(e);mats.append(y);logs.append(ld)
            matrices[exp,split]=np.array(mats).T;logmats[exp,split]=np.array(logs).T
    correlations=[];slopes=[];bins=[];categories=[]
    view_masks={}
    for exp,vv in catalogs.items():
        views=['main38','all43','no_headers31'] if exp=='phrases' else ['single_word40']
        for view in views:
            if exp=='phrases':
                mask=[i for i,v in enumerate(vv) if v['category']!='control' and (view=='all43' or v['category']!='surface_format') and (view!='no_headers31' or v['category']!='reasoning_header')]
            else:mask=[i for i,v in enumerate(vv) if v['group']!='control']
            view_masks[view]=mask
            for split in ['A','C']:
                allrows=[e for e in entries if e['experiment']==exp and e['split']==split];rows=[allrows[i] for i in mask]
                Y=matrices[exp,split][:,mask];L=logmats[exp,split][:,mask];cats=[r['category'] for r in rows]
                for metric in METRICS:
                    x=np.array([r[metric] for r in rows],float)
                    if np.ptp(x)==0:
                        correlations.append({'view':view,'split':split,'metric':metric,'prefixes':len(rows),'Spearman_flip':None,'Spearman_log_odds':None,'constant_length':True});continue
                    correlations.append({'view':view,'split':split,'metric':metric,'prefixes':len(rows),
                        'Spearman_flip':float(stats.spearmanr(x,Y.mean(0)).statistic),
                        'Spearman_log_odds':float(stats.spearmanr(x,L.mean(0)).statistic),'constant_length':False})
                    if view in ['main38','no_headers31','single_word40']:
                        for result in bootstrap_slopes(x,Y,cats):slopes.append({'view':view,'split':split,'metric':metric,**result})
                if view=='main38':
                    for band in ['1','2','3','4+']:
                        ii=[i for i,r in enumerate(rows) if str(r['words'])==band or band=='4+' and r['words']>=4]
                        if ii:bins.append({'view':view,'split':split,'word_count':band,'prefixes':len(ii),'mean_flip_rate':float(Y[:,ii].mean())})
                    for cat in sorted(set(cats)):
                        ii=[i for i,c in enumerate(cats) if c==cat]
                        categories.append({'split':split,'category':cat,'prefixes':len(ii),'mean_words':float(np.mean([rows[i]['words'] for i in ii])),
                            'mean_tokens':float(np.mean([rows[i]['prefix_tokens'] for i in ii])),'mean_flip_rate':float(Y[:,ii].mean())})
    tables={'prefix_lengths_and_outcomes':entries,'length_correlations':correlations,'length_slopes':slopes,'word_count_bins':bins,'original_categories':categories}
    for name,rr in tables.items():write_csv(REPORT/(name+'.csv'),rr)
    write_json(REPORT/'results.json',tables)
    print('Analysis complete; exporting plots.',flush=True)
    figures(entries,correlations,slopes)
    write_report(entries,correlations,slopes,bins,categories)
    from openpyxl import Workbook
    wb=Workbook();wb.remove(wb.active)
    for name,rr in tables.items():
        ws=wb.create_sheet(name[:31]);keys=list(dict.fromkeys(k for r in rr for k in r));ws.append(keys)
        for r in rr:ws.append([r.get(k) for k in keys])
        ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
    wb.save(REPORT/'prefix_length_review.xlsx')
    shutil.copyfile(PLAN,REPORT/PLAN.name);shutil.copyfile(Path(__file__),REPORT/Path(__file__).name)
    write_json(REPORT/'audit.json',{'source_hashes_verified':all(sha(ROOT/p)==h for p,h in cfg['source_sha256'].items()),
        'original_experiments_validated':True,'same112_questions_in_both_experiments':True,'GPU_used':False,'new_V_scores':0,'at':utc()})
    files=sorted(p for p in REPORT.iterdir() if p.is_file() and p.suffix!='.zip' and p.name!='artifact_manifest.json')
    write_json(REPORT/'artifact_manifest.json',{p.name:sha(p) for p in files})
    with zipfile.ZipFile(REPORT/'prefix_length_review.zip','w',zipfile.ZIP_DEFLATED) as z:
        for p in files+[REPORT/'artifact_manifest.json']:z.write(p,p.name)
    write_json(RUN/'state.json',{'status':'complete','report':str(REPORT/'prefix_length_report.md'),'at':utc()})
    print(json.dumps([r for r in correlations if r['view']=='main38'],indent=2),flush=True)


def figures(entries,correlations,slopes):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    labels={'words':'Word count','characters':'Characters (trimmed)','prefix_tokens':'V0 prefix token count','prompt_token_delta':'Actual V0 prompt token increment'}
    selected=['we_can','we_will','reason_step_by_step','reasoning','analysis','it_is_essential_to']
    cats=sorted({r['category'] for r in entries if r['experiment']=='phrases' and r['category'] not in ['control','surface_format']})
    colors={c:plt.get_cmap('tab10')(i) for i,c in enumerate(cats)}
    with PdfPages(REPORT/'prefix_length_figures.pdf') as pdf:
        fig,axes=plt.subplots(2,3,figsize=(15,8.5),constrained_layout=True)
        for row,split in enumerate(['A','C']):
            rr=[r for r in entries if r['experiment']=='phrases' and r['split']==split and r['category'] in cats]
            for ax,metric in zip(axes[row],['words','characters','prefix_tokens']):
                for cat in cats:
                    ss=[r for r in rr if r['category']==cat]
                    ax.scatter([r[metric] for r in ss],[r['flip_rate']*100 for r in ss],label=cat,s=40,alpha=.85,color=colors[cat])
                for r in rr:
                    if r['variant'] in selected and metric=='prefix_tokens':ax.annotate(r['variant'],(r[metric],100*r['flip_rate']),xytext=(3,4),textcoords='offset points',fontsize=7)
                cor=next(r for r in correlations if r['view']=='main38' and r['split']==split and r['metric']==metric)
                ax.set_title(f'{split}: Spearman rho={cor["Spearman_flip"]:+.3f}');ax.set_xlabel(labels[metric]);ax.set_ylabel('Wrong reject-to-accept flip rate (%)');ax.set_ylim(-1,32)
        handles,names=axes[0,0].get_legend_handles_labels();fig.legend(handles,names,loc='outside lower center',ncol=4,fontsize=8)
        fig.suptitle('Length vs flip rate: same 64 A / 48 C wrong, originally rejected answers\n38 verbal prefixes; no new inference; associations, not isolated causal length effects')
        fig.savefig(REPORT/'length_vs_flip_scatter.png',dpi=180);pdf.savefig(fig);plt.close(fig)

        fig,axes=plt.subplots(1,3,figsize=(15,4.7),constrained_layout=True)
        for ax,metric in zip(axes,['words','characters','prefix_tokens']):
            for i,(split,model) in enumerate([(s,m) for s in ['A','C'] for m in ['unadjusted','category_fixed_effects']]):
                r=next(r for r in slopes if r['view']=='main38' and r['split']==split and r['metric']==metric and r['model']==model)
                scale=100
                ax.plot([r['crossed_low']*scale,r['crossed_high']*scale],[i,i],color='#90a4ae',lw=5)
                ax.plot([r['question_low']*scale,r['question_high']*scale],[i,i],color='#b03a2e',lw=2)
                ax.plot(r['slope']*scale,i,'ko')
            ax.axvline(0,ls='--',color='black');ax.set_yticks(range(4),['A raw','A category-adjusted','C raw','C category-adjusted']);ax.invert_yaxis();ax.set_title(labels[metric]);ax.set_xlabel('Slope: percentage points per unit length')
        fig.suptitle('Rate slopes: red = question-bootstrap 95%; grey = questions + prefixes\nExploratory intervals; no simultaneous coverage or random vocabulary sampling')
        fig.savefig(REPORT/'length_slopes.png',dpi=180);pdf.savefig(fig);plt.close(fig)

        rr=[r for r in entries if r['experiment']=='phrases' and r['split']=='A' and r['category']!='control']
        rr=sorted(rr,key=lambda r:(r['characters'],r['prefix_tokens'],r['variant']))
        lookup={(r['split'],r['variant']):r for r in entries if r['experiment']=='phrases'}
        vals=np.array([[100*lookup[s,r['variant']]['flip_rate'] for s in ['A','C']] for r in rr])
        fig,ax=plt.subplots(figsize=(10,20),constrained_layout=True)
        im=ax.imshow(vals,vmin=0,vmax=30,cmap='YlOrRd',aspect='auto')
        def row_label(r):
            suffix=f' | W{r["words"]} C{r["characters"]} T{r["prefix_tokens"]}'
            return r['variant']+': '+repr(r['prefix'])+suffix
        ax.set_yticks(range(len(rr)),[row_label(r) for r in rr],fontsize=8)
        ax.set_xticks([0,1],['A (64 answers)','C (48 answers)']);ax.xaxis.tick_top()
        for (i,j),v in np.ndenumerate(vals):ax.text(j,i,f'{v:.1f}%',ha='center',va='center',color='white' if v>=20 else 'black')
        ax.set_title('All 43 noncontrol prefixes sorted by character length\nW = words; C = trimmed characters; T = exact-prefix V0 tokens',pad=40)
        fig.colorbar(im,ax=ax,label='Wrong reject-to-accept flip rate (%)',shrink=.6)
        fig.savefig(REPORT/'prefix_length_heatmap.png',dpi=160);pdf.savefig(fig);plt.close(fig)


def write_report(entries,correlations,slopes,bins,categories):
    lines=['# 注入前缀长度与验证器偏转：缓存分析','','## 结论','','当前数据呈现“字符较长的前缀翻转率倾向较低”的描述性趋势，但词数、V0 token数的关系弱得多；不能概括为“越短越容易骗过V”。同长度前缀之间有较大差异，句式、具体词语及格式都与长度纠缠。',
      '前一次AWL对照没有支持所选词对的词表效应，并不等于已经排除了所有学术语体因素。这里检验另一个相关因素，不把竞争解释当作已证实或已否定。',
      '', '## 1. 长度的三种定义','','主分析沿用原46种设置中的38个普通词语/句式前缀：排除3个控制、5个重复格式变体。每个前缀都作用于同一批原本被拒绝的错误回答，A64题、C48题；数字为TN→FP翻转率，不是整池FPR。',
      '词数按英语单词计（let\'s计1词），字符数含内部空格/标点、去掉首尾空白。token数用原V0本地tokenizer编码完整前缀，包含末尾换行；另外从历史评分提取完整V提示实际增加的token数，以免把独立编码误当作上下文中的真实长度。',
      '', '| 长度指标 | A Spearman相关 | C Spearman相关 |','|---|---:|---:|']
    display={'words':'词数','characters':'字符数','prefix_tokens':'V0前缀token数','prompt_token_delta':'完整V提示新增token数（中位数）'}
    for metric in METRICS:
        aa=next(r for r in correlations if r['view']=='main38' and r['split']=='A' and r['metric']==metric)
        cc=next(r for r in correlations if r['view']=='main38' and r['split']=='C' and r['metric']==metric)
        lines.append(f'| {display[metric]} | {aa["Spearman_flip"]:+.3f} | {cc["Spearman_flip"]:+.3f} |')
    lines += ['', '负号表示在当前前缀清单中，更长倾向于更低翻转率；相关系数不是因果效应，没有把43/38个相关前缀当成43/38次独立随机实验来计算显著性。完整表也导出连续接受/拒绝对数似然差，而不仅看0.5阈值。',
      '', '## 2. 用户观察与同长度反例','','| 前缀 | 词数 | 字符数 | V0前缀token数 | A翻转率 | C翻转率 |','|---|---:|---:|---:|---:|---:|']
    for name in ['we_can','we_will','reason_step_by_step','reasoning','analysis','it_is_essential_to','to_determine','we_need_to','double_newline','period']:
        a=next(r for r in entries if r['experiment']=='phrases' and r['split']=='A' and r['variant']==name)
        c=next(r for r in entries if r['experiment']=='phrases' and r['split']=='C' and r['variant']==name)
        lines.append(f'| `{a["prefix"].strip()}`（{name}） | {a["words"]} | {a["characters"]} | {a["prefix_tokens"]} | {100*a["flip_rate"]:.2f}% | {100*c["flip_rate"]:.2f}% |')
    lines += ['', '`we can`与`Reasoning:`都是3个token，但效果差很多；`Analysis:`只有2个token，效果仍很低。`it is essential to`更长，却具有较高翻转率。这些反例说明长度不能单独解释排序。格式变体虽然词语相同，也可能改变翻转率；完整前缀中的换行/标点均属于处理。',
      '', '## 3. 控制原有句式类别后的趋势','','用原先冻结的类别（necessity、modality_changed、syntax_stress、solve_for_family、reasoning_header等）作截距控制。这里只作描述性线性概率斜率；不是每增加一个字符的独立因果效应。',
      '', '|池|字符数原始斜率/百分点每字符|类别控制后|控制后题目配对95%区间|控制后题目+前缀区间|','|---|---:|---:|---|---|']
    for split in ['A','C']:
        raw=next(r for r in slopes if r['view']=='main38' and r['split']==split and r['metric']=='characters' and r['model']=='unadjusted')
        adj=next(r for r in slopes if r['view']=='main38' and r['split']==split and r['metric']=='characters' and r['model']=='category_fixed_effects')
        lines.append(f'|{split}|{100*raw["slope"]:+.3f}|{100*adj["slope"]:+.3f}|[{100*adj["question_low"]:+.3f}, {100*adj["question_high"]:+.3f}]|[{100*adj["crossed_low"]:+.3f}, {100*adj["crossed_high"]:+.3f}]|')
    lines += ['', '区间以整道题同时重采样其所有前缀处理；交叉区间再在各类别内重采样前缀，保留原类别数量。词汇是人工挑选的，交叉区间只是词表选择敏感性，不能当作任意英文前缀的总体保证。各区间为探索性逐项区间，无多重覆盖保证。',
      '', '## 4. 同一格式的40个单词标题作为旁证','','复用AWL实验的40个单词标题，统一“首字母大写单词+冒号+换行”，同样112个回答。全部只有1个词，因此无法估计词数效应；可观察字符数和token数。',
      '', '|指标|A相关|C相关|','|---|---:|---:|']
    for metric in ['characters','prefix_tokens','prompt_token_delta']:
        a=next(r for r in correlations if r['view']=='single_word40' and r['split']=='A' and r['metric']==metric)
        c=next(r for r in correlations if r['view']=='single_word40' and r['split']=='C' and r['metric']==metric)
        lines.append(f'|{display[metric]}|{a["Spearman_flip"]:+.3f}|{c["Spearman_flip"]:+.3f}|')
    lines += ['', '这是格式和词数相同的一组旁证，不是把词义、词频也匹配后的长度随机实验。当前两套前缀风格不同，不能合并成一个更大样本后宣称更精确。',
      '', '## 5. 可以怎样解释，下一步怎样区分','','现有结果支持用户观察到的几个具体短句比step-by-step前缀触发更强；字符长度有负向描述性关联。但词数/token数不呈现同样强的关系，单词标题中也未复现稳定的字符长度关联。当前更适合表述为“特定词形/句式与长度共同变化”，不能定位为纯长度机制。',
      '若下一步要做长度因果试验，应先固定词义/语气骨架，再为同一骨架预先定义不同长度版本，统一换行标点；用多个词义骨架复现，并分别记录新增误接受和新增误拒。语义扩写与纯空白/标点扩写分开，避免把两者都叫长度效应。本轮没有启动新评分或补生成。',
      '', '## 产物与核验','','`length_vs_flip_scatter.png`三种长度散点图、`length_slopes.png`原始/类别控制斜率区间、`prefix_length_heatmap.png`按字符长度排序的A/C热图；完整CSV、Excel、3页PDF与源配置一起提供。',
      '两个原实验的冻结配置、模型/评分来源、全部输入及分数哈希已重新核对；同一112道题、原样本均为已核验错误且V原拒绝。只加载tokenizer做CPU分析，无模型推理、无训练、B未使用、T未读取。敏感性表同时保留全43非控制前缀及进一步去掉推理标题的31前缀，不挑选最有利视图。']
    (REPORT/'prefix_length_report.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':main()
