"""Read-only progress view; safe in a second tmux window while GPU work continues."""
import json
from .core import read_jsonl
from .formal_common import RUN,REPORT,plan

def main():
    result={}
    for name in ['state','progress','current_child']:
        p=RUN/f'{name}.json'
        if p.exists():result[name]=json.loads(p.read_text())
    result['candidate_counts']={p.stem:sum(1 for _ in p.open()) for p in sorted(RUN.glob('G[012]_[ABC].jsonl'))}
    result['V_score_counts']={p.stem:sum(1 for _ in p.open()) for p in sorted(RUN.glob('V0_G[012]_[ABC].jsonl'))}
    result['recorded_formal_stage_work_hours']=sum(r['seconds'] for r in read_jsonl(RUN/'work_ledger.jsonl'))/3600
    result['prior_work_reserve_hours']=plan()['prior_work_reserve_hours'];result['global_cap_hours']=40
    result['final_report_exists']=(REPORT/'final_report.md').exists()
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
