import random
import urllib.request
from .core import *

def main():
    counts={'A':600,'B':200,'C':100,'T':300}
    raw={}; sources=[]
    for split in ['train','test']:
        url=f'https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/{split}.jsonl'
        path=ROOT/f'data/raw/{split}.jsonl'
        if not path.exists():
            with urllib.request.urlopen(url,timeout=60) as f: blob=f.read()
            path.write_bytes(blob)
        raw[split]=read_jsonl(path)
        sources.append({'source':url,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'count':len(raw[split])})
    rng=random.Random(CFG['seed']); train=list(range(len(raw['train']))); test=list(range(len(raw['test']))); rng.shuffle(train); rng.shuffle(test)
    selections={'A':('train',train[:600]),'B':('train',train[600:800]),'C':('train',train[800:900]),'T':('test',test[:300])}
    all_hashes=set(); manifests={}
    for split,(original,indices) in selections.items():
        inputs=[];labels=[];manifest=[]
        for i in indices:
            item=raw[original][i]; qid=f'gsm8k:{original}:{i}';h=question_hash(item['question'])
            assert h not in all_hashes, f'Duplicate question {qid}'
            all_hashes.add(h)
            inputs.append({'question_id':qid,'split':split,'question':item['question'],'question_hash':h})
            labels.append({'question_id':qid,'gold_answer':extract_gold(item['answer']),'reference':item['answer']})
            manifest.append({'question_id':qid,'source_index':i,'question_hash':h})
        assert len(inputs)==counts[split]
        write_jsonl(ROOT/f'data/inputs/{split}.jsonl',inputs);write_jsonl(ROOT/f'data/labels/{split}.jsonl',labels)
        manifests[split]=manifest
    write_json(ROOT/'data/manifests/splits.json',{'seed':CFG['seed'],'sources':sources,'splits':manifests,'all_selected_hashes_disjoint':True,'T_locked':True})
    print('Saved disjoint A=600 B=200 C=100 T=300. T remains unused for day1.',flush=True)

if __name__=='__main__':main()
