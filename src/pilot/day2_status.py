"""Read-only in-progress snapshot; never used for selecting or replacing questions."""
import json
from collections import Counter
from .core import ROOT,read_jsonl
from .day2_protocol import label_response

def main():
    info={}
    for name in ['gsm8k_C100','math70']:
        p=ROOT/f'runs/day2/G0_{name}.jsonl';rows=[]
        if p.exists():
            with p.open() as f:
                for line in f:
                    try:rows.append(json.loads(line))
                    except json.JSONDecodeError:break
        if not rows:info[name]={'questions_complete':0,'candidates':0};continue
        vpath=ROOT/f'runs/day2/V0_{name}.jsonl';vcount=sum(1 for line in vpath.open() if line.strip()) if vpath.exists() else 0
        data={'questions_complete':len({r['question_id'] for r in rows}),'candidates':len(rows),'V_scores':vcount,'truncated':sum(r['truncated'] for r in rows),'generated_tokens':sum(r['output_tokens'] for r in rows)}
        if name=='gsm8k_C100':
            gold={r['question_id']:r['gold_answer'] for r in read_jsonl(ROOT/f'data/day2/labels/{name}.jsonl')};labels=[{**label_response(r['text'],r['truncated'],gold[r['question_id']],r['task']),'question_id':r['question_id']} for r in rows];valid=[x for x in labels if x['correct'] is not None]
            data.update(verifiable=len(valid),natural_errors=sum(x['correct'] is False for x in valid),error_questions=len({x['question_id'] for x in valid if x['correct'] is False}),status_counts=dict(Counter(x['status'] for x in labels)))
        info[name]=data
    state=ROOT/'runs/day2/state.json'
    if state.exists():info['stage_state']=json.loads(state.read_text())
    print(json.dumps(info,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
