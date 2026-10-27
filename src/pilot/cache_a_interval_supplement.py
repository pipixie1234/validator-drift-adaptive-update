"""CPU-only paired A feedback error-rate changes; separate from held-out C."""
import json
import shutil
from .core import ROOT,read_jsonl,write_json
from .formal_common import sha,utc
from .cache_followup import DEST,IMAGE,REFERENCE_A,independent_bootstrap,counts
from .v0_drift_analysis import interval,csv_write,load_rows
from .explanation_probes import SUSPECT


def calculate(view,scenario,comparison):
 qs=sorted({r['question_id'] for r in view['G0']})
 boot=independent_bootstrap(view,qs);points={g:counts(rr) for g,rr in view.items()}
 out=[]
 for key in ['FPR','FNR','balanced_accuracy']:
  if key=='balanced_accuracy':
   p={g:1-(points[g]['FPR']+points[g]['FNR'])/2 for g in view}
   b={g:1-(boot[g]['FPR']+boot[g]['FNR'])/2 for g in view}
  else:p={g:points[g][key] for g in view};b={g:boot[g][key] for g in view}
  out.append({'scenario':scenario,'comparison':comparison,'metric':key,'G0':p['G0'],'G1':p['G1'],
   'difference':p['G1']-p['G0'],**interval(b['G1']-b['G0'])})
 return out


def main():
 data={};hashes={}
 for g in ['G0','G1']:
  paths=[ROOT/f'runs/formal/{prefix}{g}_A.jsonl' for prefix in ['', 'labels_', 'V0_']]
  for p in paths:hashes[str(p.relative_to(ROOT))]=sha(p)
  raw,labels,scores=[read_jsonl(p) for p in paths]
  labels={r['candidate_id']:r for r in labels};scores={r['candidate_id']:r for r in scores}
  data[g]=[{**r,**labels[r['candidate_id']],'accepted':scores[r['candidate_id']]['accepted']} for r in raw]
 results=[]
 for scenario,bad in [('A600_original',set()),('A599_no_missing_image',{IMAGE}),
  ('A598_no_image_or_suspect_reference',{IMAGE,REFERENCE_A})]:
  view={g:[r for r in rr if r['question_id'] not in bad] for g,rr in data.items()}
  results+=calculate(view,scenario,'V0 on G1_A minus V0 on G0_A')
 c={g:[r for r in load_rows(g,'C') if r['question_id']!=SUSPECT] for g in ['G0','G1']}
 for g in ['G0','G1']:
  for pre in ['', 'labels_', 'V0_']:
   p=ROOT/f'runs/formal/{pre}{g}_C.jsonl';hashes[str(p.relative_to(ROOT))]=sha(p)
 results+=calculate(c,'C95_independent_of_G_training','V0 on original G1_C minus V0 on G0_C')
 assert all(sha(ROOT/n)==h for n,h in hashes.items())
 result={'computed_at':utc(),'replicates':10000,'seed':20261022,
  'unit':'whole paired question, four candidates together',
  'interval':'exploratory pointwise percentile95; no training-seed uncertainty or multiplicity correction',
  'results':results,'original_source_sha256':hashes,'T_read':False,'GPU_used':False}
 write_json(DEST/'A_feedback_paired_intervals_supplement.json',result)
 csv_write(DEST/'A_feedback_paired_intervals_supplement.csv',results)
 from .oracle_visuals import table
 text='\n\n'.join(['## 补充：A反馈池确有FPR上升信号，不能与独立C混为一谈',
  '表中G0→G1、G1→G2表示两次生成—筛选—训练流程；两行的V0统计分别来自G0_A和G1_A，比较的是G0与G1输出，不是已经生成的G1与G2输出。',
  table(['题池/口径','指标','G1−G0/百分点','成对95%区间/百分点'],
   [[r['scenario'],r['metric'],f'{100*r["difference"]:+.2f}',f'[{100*r["low"]:+.2f}, {100*r["high"]:+.2f}]'] for r in results]),
  '**结论需明确限定：原A600反馈池的FPR上升3.11个百分点，探索性点位95%区间[+0.09,+6.04]，支持该池中误接受风险上升；不能笼统表述成没有跨轮退化信号。**FNR下降约1.14个百分点，其区间跨0。若两类错误等权，平衡准确率点估计下降约0.98个百分点，但其区间同样跨0；不同条件分母的两个百分点不能当作实际错判条数直接相减。',
  '排除已发现问题题目后，FPR升幅仍约2.98个百分点，区间仅略跨0；效应点估计几乎不变，不能据此宣布退化消失，但统计证据处于边界。所有区间均未作多重比较校正，且不包含重新训练种子的变异。',
  'A是实际用于G反馈更新的题池，因此这是训练题域上的更新后诊断。独立于G训练的C95上，原G0→G1的FPR差为−0.51个百分点，区间[−8.01,+6.95]，未复现同方向上升，区间也较宽。C已被反复用于开发，不能称为未触碰的最终测试。',
  '**当前可以支持“A反馈池出现固定V误接受风险上升”，尚不能外推为独立题上稳定退化，更不能归因为G能力提升导致V失效。**问题组成、具体错误文本变化和训练题域效应仍需区分。补充计算只读A/C缓存，T未读取，无GPU使用。'])
 (DEST/'A_feedback_interval_note.md').write_text(text+'\n')
 report=DEST/'cache_analysis_report.md';old=report.read_text()
 marker='\n\n## 补充：A反馈池确有FPR上升信号，不能与独立C混为一谈'
 if marker in old:old=old.split(marker)[0]
 report.write_text(old.rstrip()+'\n\n'+text+'\n')
 from .cache_followup_export import bundle
 shutil.copy2(ROOT/'src/pilot/cache_a_interval_supplement.py',DEST/'source/cache_a_interval_supplement.py')
 bundle()
 print(json.dumps({'complete':True,'report':str(report),'interval_rows':len(results)},ensure_ascii=False))


if __name__=='__main__':main()
