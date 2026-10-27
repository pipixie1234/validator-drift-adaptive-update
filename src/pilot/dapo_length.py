"""Record native-context and output-length diagnostics without relabeling truncation."""
import argparse
from collections import Counter
import numpy as np
from .core import read_jsonl, write_json
from .dapo_common import BASE, RUN, REPORT, config

def length_summary(rows):
    lengths=[r['output_tokens'] for r in rows]
    return {'candidates':len(rows),'questions':len({r['question_id'] for r in rows}),
        'truncated':sum(r['truncated'] for r in rows),
        'truncated_questions':len({r['question_id'] for r in rows if r['truncated']}),
        'would_exceed_old_1536_output_cap':sum(n>1536 for n in lengths),
        'completed_above_1536':sum(not r['truncated'] and r['output_tokens']>1536 for r in rows),
        'mean':float(np.mean(lengths)),'median':float(np.median(lengths)),
        'p95':float(np.quantile(lengths,.95)),'max':max(lengths),
        'max_prompt_plus_output_tokens':max(r['prompt_tokens']+r['output_tokens'] for r in rows),
        'truncated_ids':[r['candidate_id'] for r in rows if r['truncated']],
        'unknown_policy':'All budget-truncated outputs stay unlabelled, even with an apparent complete box. Prefix1536 is a counterfactual length check only, never an extra answer or error.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--pilot',action='store_true');a=p.parse_args();cfg=config()
    rows=read_jsonl(RUN/'G0_dapo100.jsonl')
    if a.pilot:
        fixed={r['question_id'] for r in read_jsonl(BASE/'inputs/dapo100.jsonl')[:cfg['length_pilot_questions']]}
        rows=[r for r in rows if r['question_id'] in fixed]
        expected=cfg['length_pilot_questions']*4
        assert len(rows)==expected
    else:assert len(rows)==400
    assert all(n==4 for n in Counter(r['question_id'] for r in rows).values())
    summary=length_summary(rows)
    assert summary['max_prompt_plus_output_tokens']<=cfg['G_native_context']
    summary['phase']='fixed_first20_before_remaining80' if a.pilot else 'all100'
    summary['sufficiency_observed']=summary['truncated']/len(rows)<=cfg['feasibility_gate']['maximum_truncation_fraction']
    write_json(REPORT/('length_pilot.json' if a.pilot else 'length_full.json'),summary)
    print(__import__('json').dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
