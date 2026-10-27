import concurrent.futures
import hashlib
import json
import random
import urllib.request
from collections import Counter,defaultdict
from .core import ROOT,read_jsonl,write_json,write_jsonl,question_hash
from .day2_protocol import extract

def main():
    import pyarrow.parquet as pq
    cfg=json.loads((ROOT/'configs/day2.json').read_text());rev=cfg['math_revision'];api=f'https://hf-mirror.com/api/datasets/{cfg["math_dataset"]}/tree/{rev}?recursive=true'
    metadata=json.load(urllib.request.urlopen(api,timeout=30));files=[x for x in metadata if x.get('path','').endswith('.parquet') and '/train-' in x['path']];assert len(files)==7
    def download(item):
        path=ROOT/'data/raw/math_train'/item['path'];path.parent.mkdir(parents=True,exist_ok=True);url=f'https://hf-mirror.com/datasets/{cfg["math_dataset"]}/resolve/{rev}/{item["path"]}'
        if not path.exists():
            with urllib.request.urlopen(url,timeout=60) as f:blob=f.read()
            path.write_bytes(blob)
        sha=hashlib.sha256(path.read_bytes()).hexdigest();assert sha==item['lfs']['oid']
        return {'path':str(path),'url':url,'sha256':sha,'rows':pq.read_table(path).to_pylist(),'source_path':item['path']}
    with concurrent.futures.ThreadPoolExecutor(4) as pool:sources=list(pool.map(download,files))
    groups=defaultdict(list);excluded=[]
    protected=json.loads((ROOT/'data/manifests/splits.json').read_text())
    excluded_hashes={r['question_hash'] for split in protected['splits'].values() for r in split}
    seen=set()
    for source in sorted(sources,key=lambda x:x['source_path']):
        for i,row in enumerate(source['rows']):
            qid=f'math:train:{source["source_path"].split("/")[0]}:{i}';h=question_hash(row['problem'])
            reason='unknown_difficulty' if row['level'] not in [f'Level {n}' for n in range(1,6)] else 'diagram_required' if '[asy]' in row['problem'].lower() or '<img' in row['problem'].lower() else 'duplicate_or_protected' if h in seen or h in excluded_hashes else None
            if reason:excluded.append({'question_id':qid,'reason':reason});continue
            seen.add(h);groups[(row['type'],row['level'])].append((qid,h,row))
    rng=random.Random(cfg['seed']);chosen=[];strata=[]
    for key,group in sorted(groups.items()):
        assert len(group)>=2;rng.shuffle(group);selected=group[:cfg['math_questions_per_subject_level']];chosen.extend(selected);strata.append({'type':key[0],'level':key[1],'eligible_count':len(group),'selected_ids':[x[0] for x in selected]})
    assert len(groups)==35 and len(chosen)==70
    inputs=[];labels=[]
    for qid,h,row in chosen:
        inputs.append({'question_id':qid,'split':'MATH_D','task':'math','question':row['problem'],'question_hash':h,'type':row['type'],'level':row['level']})
        gold=extract(row['solution'],reference=True,task='math')
        labels.append({'question_id':qid,'gold_answer':gold['value'],'reference':row['solution'],'reference_extraction_status':gold['status']})
    manifests={'seed':cfg['seed'],'revision':rev,'sampling_before_generation':True,'answer_based_filtering':False,'strata':strata,'excluded':excluded,'sources':[{k:v for k,v in s.items() if k!='rows'} for s in sources],'test_downloaded':False}
    p=ROOT/'data/day2/manifests/math70.json'
    if p.exists():assert json.loads(p.read_text())==manifests,'Refuse to change fixed sampling'
    write_json(p,manifests);write_jsonl(ROOT/'data/day2/inputs/math70.jsonl',inputs);write_jsonl(ROOT/'data/day2/labels/math70.jsonl',labels)
    c=[{**r,'task':'gsm8k'} for r in read_jsonl(ROOT/'data/inputs/C.jsonl')];assert len(c)==100
    write_jsonl(ROOT/'data/day2/inputs/gsm8k_C100.jsonl',c);write_jsonl(ROOT/'data/day2/labels/gsm8k_C100.jsonl',read_jsonl(ROOT/'data/labels/C.jsonl'))
    write_json(ROOT/'data/day2/manifests/selection_locked.json',{'config_sha256':hashlib.sha256((ROOT/'configs/day2.json').read_bytes()).hexdigest(),'gsm8k_ids':[r['question_id'] for r in c],'math_ids':[r['question_id'] for r in inputs],'selection_locked_before_generation':True,'GSM8K_T_unchanged':True,'MATH_test_not_downloaded':True})
    print(f'Prepared GSM8K C100 + MATH train70; 35 strata ×2, excluded {len(excluded)} text-ineligible or duplicated questions.',flush=True)

if __name__=='__main__':main()
