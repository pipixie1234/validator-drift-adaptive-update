"""Fix development families before inference; use the small English train parquet."""
from collections import Counter, defaultdict
import json
import random
import re
from .core import ROOT, read_jsonl, write_json, write_jsonl, question_hash
from .dapo_common import BASE, RUN, REPORT, config, messages, sha, shingles

def family_groups(questions, source_ids):
    parent = list(range(len(questions)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    def union(a,b):
        a,b = root(a),root(b)
        parent[max(a,b)] = min(a,b)
    grams = [shingles(q) for q in questions]
    postings = defaultdict(list)
    hashes, uuids = {}, {}
    for i,(q,uid) in enumerate(zip(questions,source_ids)):
        h = question_hash(q)
        if h in hashes: union(i, hashes[h])
        hashes[h] = i
        if uid:
            if uid in uuids: union(i, uuids[uid])
            uuids[uid] = i
        for gram in grams[i]: postings[gram].append(i)
    edges = 0
    for i,a in enumerate(grams):
        candidates = Counter(j for gram in a if len(postings[gram]) <= 200
                             for j in postings[gram] if j < i)
        for j,n in candidates.items():
            if n < 3 or root(i) == root(j): continue
            shared = len(a & grams[j])
            if shared >= 8 and shared / max(1,min(len(a),len(grams[j]))) >= .8:
                union(i,j); edges += 1
    return [root(i) for i in range(len(questions))], edges

def main():
    import pyarrow.parquet as pq
    from transformers import AutoTokenizer
    from .core import prompt_ids
    cfg = config(); source = ROOT/'data/raw/dapo_en/train-00000-of-00001.parquet'
    assert sha(source) == cfg['file_sha256']
    for p in [BASE/'inputs',BASE/'labels',BASE/'manifests',RUN,REPORT]: p.mkdir(parents=True,exist_ok=True)
    assert not (RUN/'G0_dapo100.jsonl').exists(), 'Selection cannot change after generation'
    rows = pq.read_table(source).to_pylist(); assert len(rows) == 14116
    protected = [r for p in sorted((ROOT/'data/inputs').glob('*.jsonl')) for r in read_jsonl(p)]
    protected += read_jsonl(ROOT/'data/day2/inputs/math70.jsonl')
    # Only protected questions are read; heldout answers are never inspected.
    roots,edges = family_groups([r['question'] for r in protected]+[r['prompt'] for r in rows],
                               [None]*len(protected)+[r['extra_info']['index'] for r in rows])
    protected_roots = set(roots[:len(protected)]); groups = defaultdict(list); excluded = Counter()
    tok = AutoTokenizer.from_pretrained(cfg['G_MODEL_DIR'],local_files_only=True)
    prefix = 'Solve the following math problem step by step. The last line of your response should be of the form Answer: $Answer (without quotes) where $Answer is the answer to the problem.\n\n'
    suffix = '\n\nRemember to put your answer on its own line after "Answer:".'
    for i,r in enumerate(rows):
        q = r['prompt']; group = roots[len(protected)+i]
        assert len(r['source_prompt']) == 1 and r['source_prompt'][0]['role'] == 'user'
        assert r['source_prompt'][0]['content'] == prefix+q+suffix, 'Unknown wrapper: do not strip mathematical conditions'
        n = len(prompt_ids(tok,messages('G',q)))
        reason = ('protected_family' if group in protected_roots else
                  'not_english_CJK' if re.search(r'[\u3400-\u9fff\uf900-\ufaff]',q) else
                  'diagram' if re.search(r'\[asy\]|<img|!\[|diagram\s+(?:below|above|shown)|figure\s+(?:below|above|shown)',q,re.I) else
                  'prompt_context' if n > cfg['max_prompt_tokens'] else None)
        if reason: excluded[reason] += 1; continue
        h = question_hash(q)
        groups[group].append({'question_id':f"dapo:train:{r['extra_info']['index']}",
            'question':q,'question_hash':h,'source_uuid':r['extra_info']['index'],
            'source_row':i,'source_prompt_sha256':__import__('hashlib').sha256(r['source_prompt'][0]['content'].encode()).hexdigest(),
            'prompt_tokens_prechecked':n,'gold_answer':r['solution'],
            'reference_consistent':r['solution']==r['reward_model']['ground_truth']})
    eligible=[]
    for group,rs in groups.items():
        # Selection is based exclusively on question content and source UUID, never on gold.
        rs.sort(key=lambda r:(r['question_hash'],r['source_uuid']))
        family_id = min(r['question_hash'] for r in rs)
        representative = dict(rs[0],family_id=family_id,family_member_count=len(rs))
        representative['family_source_uuids'] = sorted({r['source_uuid'] for r in rs})
        eligible.append(representative)
    eligible.sort(key=lambda r:r['family_id']);random.Random(cfg['seed']).shuffle(eligible)
    assert len(eligible) >= 1200
    selected=eligible[:cfg['questions']]
    # Validate source schema globally; never skip rows based on answer values.
    assert all(re.fullmatch(r'[+-]?\d+',r['solution']) and r['solution']==r['reward_model']['ground_truth'] for r in rows)
    def inputs(rs,split):
        return [{k:v for k,v in r.items() if k not in ['gold_answer','reference_consistent']} |
                {'task':'dapo','split':split} for r in rs]
    write_jsonl(BASE/'inputs/dapo100.jsonl',inputs(selected,'DAPO_D'))
    write_jsonl(BASE/'labels/dapo100.jsonl',[{'question_id':r['question_id'],'gold_answer':r['gold_answer']} for r in selected])
    # Private preparation inventory; inference is restricted to the 100 development inputs.
    write_jsonl(BASE/'eligible_families.jsonl',eligible)
    manifest={'config_sha256':sha(ROOT/'configs/dapo_diagnostic.json'),'source_sha256':sha(source),
        'source_dataset':cfg['source_dataset'],'dataset':cfg['dataset'],'revision':cfg['revision'],
        'source_rows':len(rows),'protected_question_count':len(protected),'near_family_edges':edges,
        'eligible_families':len(eligible),'eligible_rows':sum(map(len,groups.values())),
        'excluded_rows':dict(excluded),'selected_ids':[r['question_id'] for r in selected],
        'selected_family_ids':[r['family_id'] for r in selected],
        'inputs_sha256':sha(BASE/'inputs/dapo100.jsonl'),'labels_sha256':sha(BASE/'labels/dapo100.jsonl'),
        'inventory_sha256':sha(BASE/'eligible_families.jsonl'),
        'selection_locked_before_generation':True,'answer_based_filtering':False,
        'input_text':'Processed prompt exactly retained; verified source_prompt differs only by known Answer-format wrapper. Mathematical answer transformations kept.',
        'dedup_limit':'Lexical components are approximate. No original competition problem identifier or semantic-equivalence guarantee available.',
        'formal_training_started':False,'heldout_inference':False}
    write_json(BASE/'manifests/selection_locked.json',manifest)
    print(json.dumps(manifest,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
