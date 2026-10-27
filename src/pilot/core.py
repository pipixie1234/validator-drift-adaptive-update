import hashlib
import json
import re
import unicodedata
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CFG = json.loads((ROOT / 'configs/day1.json').read_text())
G_SYSTEM = 'You are a careful mathematical problem solver. Solve the problem step by step. End your response with exactly one final line: #### <number>. Use an integer, decimal, or simple fraction, without units or extra text after that line.'
V_SYSTEM = 'Judge whether the FINAL NUMERICAL ANSWER in the candidate response correctly answers the question. Work out the answer independently. A means the final answer is correct; B means it is incorrect. Judge final-answer correctness, not the completeness of the explanation. Respond with exactly A or B.'

def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def question_hash(q):
    return hashlib.sha256(' '.join(unicodedata.normalize('NFKC', q).casefold().split()).encode()).hexdigest()

def read_jsonl(path):
    if not Path(path).exists(): return []
    with Path(path).open() as stream:
        return [json.loads(line) for line in stream if line.strip()]

def write_json(path, obj):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_suffix(p.suffix + '.tmp'); temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2)); temp.replace(p)

def write_jsonl(path, rows):
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    temp = p.with_suffix(p.suffix + '.tmp')
    with temp.open('w') as f:
        for row in rows: f.write(json.dumps(row, ensure_ascii=False) + '\n')
    temp.replace(p)

def recover_jsonl(path):
    # Preserve evidence of a torn final write, then recover only the valid prefix.
    import time
    p=Path(path)
    if not p.exists(): return []
    lines=p.read_bytes().splitlines(keepends=True);rows=[];offset=0
    for i,line in enumerate(lines):
        try:
            if line.strip(): rows.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            if i != len(lines)-1: raise
            p.with_name(p.name+f".partial_{time.time_ns()}").write_bytes(line)
            with p.open("r+b") as f: f.truncate(offset)
            return rows
        offset += len(line)
    if lines and not lines[-1].endswith(b"\n"):
        with p.open("ab") as f: f.write(b"\n")
    return rows

def ensure_run_manifest(path, descriptor):
    p=Path(path)
    if p.exists():
        if json.loads(p.read_text()) != descriptor:
            raise RuntimeError(f"Run identity changed; use a fresh tag instead of mixing artifacts: {p}")
    else: write_json(p, descriptor)

def append_jsonl(path, obj):
    import os
    with Path(path).open('a') as f:
        f.write(json.dumps(obj, ensure_ascii=False) + '\n'); f.flush(); os.fsync(f.fileno())

NUMBER = r'[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)'

def normalize_number(text):
    text = text.strip().replace('−', '-')
    if not re.fullmatch(NUMBER + r'(?:\s*/\s*' + NUMBER + r')?', text):
        raise ValueError('Not an unambiguous numeric literal')
    bits = text.replace(',', '').split('/')
    out = Fraction(bits[0].strip())
    if len(bits) == 2: out /= Fraction(bits[1].strip())
    return str(out)

def extract_answer(text, truncated=False):
    # Never fall back to the last number in prose. Multiple final markers are ambiguous.
    if truncated: return {'status': 'truncated', 'answer': None}
    markers = re.findall(r'(?m)^\s*####\s*(.*?)\s*$', text)
    if len(markers) != 1: return {'status': 'ambiguous' if len(markers)>1 else 'missing_marker', 'answer': None}
    if not re.search(r'####[^\n]*\s*\Z', text): return {'status': 'trailing_text', 'answer': None}
    try: return {'status': 'verified_numeric', 'answer': normalize_number(markers[0])}
    except (ValueError, ZeroDivisionError): return {'status': 'invalid_number', 'answer': None}

def extract_candidate_answer(text, truncated=False):
    primary=extract_answer(text,truncated)
    if primary['status']=='verified_numeric':return primary | {'answer_format':'hash','format_compliant':True}
    failed=primary | {'answer_format':None,'format_compliant':False}
    if primary['status']!='missing_marker' or '####' in text:return failed
    # C-only diagnostic extension: one numeric box, only closing math delimiters after it.
    locations=list(re.finditer(r'\\boxed\s*\{',text))
    if len(locations)!=1:return failed
    begin=locations[0].end();depth=1;end=begin
    while end<len(text) and depth:
        if text[end]=='{':depth+=1
        elif text[end]=='}':depth-=1
        end+=1
    if depth:return failed
    tail=text[end:].strip()
    tail=re.sub(r'\\[\]\)]|\$', '',tail).strip()
    if tail.strip(' .。!！')!='':return failed
    value=text[begin:end-1].strip()
    fraction=re.fullmatch(r'\\(?:dfrac|frac)\s*\{([^{}]+)\}\s*\{([^{}]+)\}',value)
    if fraction:value=fraction[1]+'/'+fraction[2]
    try:value=normalize_number(value)
    except (ValueError,ZeroDivisionError):return failed
    return {'status':'verified_numeric','answer':value,'answer_format':'boxed','format_compliant':False}

def extract_gold(text):
    parts = text.rsplit('####', 1)
    if len(parts) != 2: raise ValueError('Reference has no final marker')
    return normalize_number(parts[1])

def messages(role, question, response=None):
    if role == 'G': return [{'role':'system','content':G_SYSTEM},{'role':'user','content':question + '\n\nGive your reasoning, then finish with a separate final line in this exact format:\n#### <your final numerical answer>\nDo not put the final answer in a box. Do not add units or words after the number.'}]
    return [{'role':'system','content':V_SYSTEM},{'role':'user','content':f'Question:\n{question}\n\nCandidate response:\n{response}'}]

def prompt_ids(tokenizer, msgs):
    return tokenizer.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True)

def training_example(tokenizer, msgs, target, max_length):
    prefix = prompt_ids(tokenizer, msgs)
    full = tokenizer.apply_chat_template(msgs + [{'role':'assistant','content':target}], tokenize=True, add_generation_prompt=False)
    if full[:len(prefix)] != prefix: raise ValueError('Training and inference template prefixes differ')
    if len(full)>max_length: return None
    if len(full) <= len(prefix): raise ValueError('Empty assistant target')
    return {'input_ids': full, 'attention_mask': [1]*len(full), 'labels': [-100]*len(prefix)+full[len(prefix):], 'prefix_length':len(prefix)}

def collate(examples, pad_id):
    import torch
    length = max(len(x['input_ids']) for x in examples)
    return {k: torch.tensor([x[k]+[(-100 if k=='labels' else 0 if k=='attention_mask' else pad_id)]*(length-len(x[k])) for x in examples]) for k in ['input_ids','attention_mask','labels']}

def cache_identity(role, fingerprint, adapter, data_ids, prompts, settings):
    return digest({'role':role, 'base':fingerprint,'adapter':adapter,'data_ids':data_ids,'prompts':prompts,'settings':settings})
