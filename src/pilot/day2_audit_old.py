from collections import Counter
from .core import *
from .day2_protocol import label_response,extract,VERSION

def main():
    old=read_jsonl(ROOT/'runs/day1/G0_C50.jsonl');scores={r['candidate_id']:r for r in read_jsonl(ROOT/'runs/day1/V0_G0_C50.jsonl')};gold={r['question_id']:r['gold_answer'] for r in read_jsonl(ROOT/'data/labels/C.jsonl')};all_rows=[];failed=[]
    for r in old:
        label=label_response(r['text'],r['truncated'],gold[r['question_id']],'gsm8k');s=scores[r['candidate_id']]
        item={'question_id':r['question_id'],'candidate_id':r['candidate_id'],'previous_status':r['verification_status'],'previously_verifiable':r['verification_status']=='verified_numeric','new_status':label['status'],'extracted_answer':label['value'],'correct':label['correct'],'independent_gold':gold[r['question_id']],'V0_accepted':s['accepted'],'V0_score':s['score'],'comparison':label['comparison'],'truncated':r['truncated'],'answer_format':label['format'],'trailing_description':label.get('trailing_description',False),'question':r['question'],'raw_text':r['text'],'protocol':VERSION}
        all_rows.append(item)
        if r['verification_status']!='verified_numeric':failed.append(item)
    assert len(failed)==50
    def group(rows):
        v=[r for r in rows if r['correct'] is not None]
        return {'total':len(rows),'verified':len(v),'correct':sum(r['correct'] for r in v),'incorrect':sum(not r['correct'] for r in v),'accuracy_on_verified':sum(r['correct'] for r in v)/len(v) if v else None,'accuracy_lower_bound_all_rows':sum(r['correct'] for r in v)/len(rows) if rows else None,'accuracy_upper_bound_all_rows':(sum(r['correct'] for r in v)+len(rows)-len(v))/len(rows) if rows else None,'incorrect_questions':len({r['question_id'] for r in v if not r['correct']}),'false_accepts':sum(not r['correct'] and r['V0_accepted'] for r in v),'false_rejects':sum(r['correct'] and not r['V0_accepted'] for r in v)}
    summary={'original_verified':group([r for r in all_rows if r['previously_verifiable']]),'old_50_failed_reaudited':group(failed),'all_200_reaudited':group(all_rows),'remaining_unresolved_reasons':dict(Counter(r['new_status'] for r in failed if r['correct'] is None)),'interpretation':'Same raw responses and original V0 scores; isolates extraction change without regenerating answers or changing verifier. Old and newly recovered correctness compared descriptively, no selection by correctness.'}
    write_jsonl(ROOT/'runs/day2/day1_C50_relabelled.jsonl',all_rows);write_jsonl(ROOT/'runs/day2/day1_50_failed_audit.jsonl',failed);write_json(ROOT/'reports/day2/day1_50_failed_audit_summary.json',summary)
    lines=['# 第一天50条不可核验回答逐条审计','',json.dumps(summary,ensure_ascii=False,indent=2),'']
    for i,r in enumerate(failed,1):
        lines += [f"## {i}. {r['candidate_id']}",f"题目：{r['question']}",f"旧状态={r['previous_status']}；新状态={r['new_status']}；答案={r['extracted_answer']}；标准答案={r['independent_gold']}；正确={r['correct']}；V0接受={r['V0_accepted']}。",f"```text\n{r['raw_text']}\n```",'']
    (ROOT/'reports/day2/day1_50_failed_audit.md').write_text('\n'.join(lines));print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
