import gc
import time
from .core import *

def seed_all(seed):
    import random,numpy as np,torch
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed);torch.cuda.manual_seed_all(seed)

def load(role, adapter=None):
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    path=CFG[f'{role}_MODEL_DIR']
    tok=AutoTokenizer.from_pretrained(path,local_files_only=True)
    tok.padding_side='left'
    if tok.pad_token_id is None:tok.pad_token=tok.eos_token
    model=AutoModelForCausalLM.from_pretrained(path,local_files_only=True,torch_dtype=torch.bfloat16,attn_implementation='sdpa',device_map={'':'cuda:0'})
    if adapter:
        from peft import PeftModel
        model=PeftModel.from_pretrained(model,str(adapter),is_trainable=False,local_files_only=True)
    model.eval();return model,tok

def cleanup(model):
    import torch
    del model;gc.collect();torch.cuda.empty_cache()

def model_identity(role, adapter=None):
    path=Path(CFG[f'{role}_MODEL_DIR'])
    weights=[{'name':p.name,'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in sorted(path.glob('*.safetensors'))]
    identity={'path':str(path),'config':json.loads((path/'config.json').read_text()),'weights':weights,'tokenizer_sha256':hashlib.sha256((path/'tokenizer_config.json').read_bytes()).hexdigest()}
    adapter_hash=None
    if adapter:
        files=sorted(Path(adapter).glob('adapter*'))
        adapter_hash=digest([{p.name:hashlib.sha256(p.read_bytes()).hexdigest()} for p in files])
    return digest(identity),adapter_hash

def conditional_label_scores(model, tokenizer, msgs, labels=('A','B')):
    import torch
    prefix=prompt_ids(tokenizer,msgs); rows=[];label_ids=[]
    for label in labels:
        rendered=tokenizer.apply_chat_template(msgs, tokenize=False,add_generation_prompt=True)
        full=tokenizer.encode(rendered+label,add_special_tokens=False)
        if full[:len(prefix)]!=prefix:raise ValueError('Label changes prompt tokenization')
        ids=full[len(prefix):]
        if not ids:raise ValueError('Empty label')
        label_ids.append(ids);rows.append(full)
    length=max(map(len,rows)); pad=tokenizer.pad_token_id
    ids=torch.tensor([r+[pad]*(length-len(r)) for r in rows],device=model.device)
    mask=torch.tensor([[1]*len(r)+[0]*(length-len(r)) for r in rows],device=model.device)
    with torch.inference_mode():
        logits=model(input_ids=ids,attention_mask=mask,use_cache=False).logits
        scores=[]
        for i,continuation in enumerate(label_ids):
            pos=len(prefix)-1
            logprobs=logits[i,pos:pos+len(continuation)].float().log_softmax(-1)
            target=torch.tensor(continuation,device=logprobs.device)
            scores.append(logprobs.gather(1,target[:,None]).sum())
        values=torch.stack(scores);proxy=values.softmax(0)
    return {'log_likelihood_A':float(values[0]),'log_likelihood_B':float(values[1]),'score':float(proxy[0]),'accepted':bool(values[0]>=values[1]),'label_ids':label_ids,'prompt_tokens':len(prefix)}
