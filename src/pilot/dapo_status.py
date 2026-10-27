"""Read-only development progress, without loading gold or running a model."""
import json
from .core import read_jsonl
from .dapo_common import RUN

def main():
    g=read_jsonl(RUN/'G0_dapo100.jsonl');v=read_jsonl(RUN/'V0_dapo100.jsonl')
    print(json.dumps({'G_questions':len({r['question_id'] for r in g}),'G_candidates':len(g),
        'V_scores':len(v),'truncated':sum(r['truncated'] for r in g),
        'completed_above1536':sum(not r['truncated'] and r['output_tokens']>1536 for r in g),
        'G_seconds_saved_questions':sum({r['question_id']:r['generation_seconds_for_question'] for r in g}.values()),
        'formal_training_started':False},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
