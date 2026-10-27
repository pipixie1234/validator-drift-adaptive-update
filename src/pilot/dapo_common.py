"""Additive DAPO diagnostic; frozen day1/day2 implementations stay unchanged."""
import hashlib
import json
import re
from .core import ROOT, question_hash

BASE = ROOT / 'data/dapo'
RUN = ROOT / 'runs/dapo'
REPORT = ROOT / 'reports/dapo'

def config():
    return json.loads((ROOT / 'configs/dapo_diagnostic.json').read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def messages(role, question, response=None):
    cfg = config()
    if role == 'G':
        return [{'role':'system','content':cfg['G_system']},
                {'role':'user','content':question + cfg['G_user_suffix']}]
    return [{'role':'system','content':cfg['V_system']},
            {'role':'user','content':f'Question:\n{question}\n\nCandidate response:\n{response}'}]

def shingles(question):
    words = re.findall(r'[a-z0-9]+', question.casefold())
    return set(zip(words, words[1:], words[2:]))

def near_family(left, right):
    """Conservative lexical containment, including reformulated answer suffixes."""
    if question_hash(left) == question_hash(right):
        return True
    a, b = shingles(left), shingles(right)
    common = len(a & b)
    return common >= 8 and common / max(1, min(len(a), len(b))) >= .8

def available_output_tokens(prompt_tokens, context=4096):
    if not 0 < prompt_tokens < context:
        raise ValueError('Prompt does not leave native context for a response')
    return context - prompt_tokens
