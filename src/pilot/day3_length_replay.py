"""Seed-matched native-context replay of censored question batches; never overwrite the pilot."""
import json
import time
from collections import Counter
from .core import ROOT,read_jsonl,append_jsonl,write_json,ensure_run_manifest,prompt_ids,digest
from .runtime import load,model_identity,seed_all,conditional_label_scores
from .dapo_common import RUN,config,messages,sha
from .day2_protocol import label_response

OUT=ROOT/'runs/day3/length_replay'

def main():
    import torch
    cfg=config();OUT.mkdir(parents=True,exist_ok=True)
    original=read_jsonl(RUN/'G0_dapo100.jsonl');questions={r['question_id'] for r in original if r['truncated']}
    batches={q:sorted([r for r in original if r['question_id']==q],key=lambda r:r['candidate_index']) for q in sorted(questions)}
    fp,_=model_identity('G')
    ensure_run_manifest(OUT/'manifest.json',{'selection':'All question batches containing at least one3072-token-truncated candidate; never chosen by correctness or V score.',
        'questions':list(batches),'original_G_sha256':sha(RUN/'G0_dapo100.jsonl'),'G_fingerprint':fp,
        'settings':dict(cfg['generation'],max_new_tokens='4096 - exact prompt token count'),
        'same_per_question_seeds_and_four_candidate_batches':True,'purpose':'Length-only replay with prefix identity controls; primary pilot unchanged.',
        'runner_sha256':sha(ROOT/'src/pilot/day3_length_replay.py')})
    output=OUT/'G0_native_context.jsonl';done={r['question_id'] for r in read_jsonl(output)}
    if done==questions:print('Native-context replay cached.');return
    model,tok=load('G');assert model.config.max_position_embeddings==4096
    for q,old in batches.items():
        if q in done:continue
        msgs=messages('G',old[0]['question']);ids=prompt_ids(tok,msgs);settings=dict(cfg['generation'],max_new_tokens=4096-len(ids))
        seed_all(cfg['seed']+int(digest(q)[:8],16)%1000000)
        x=torch.tensor([ids],device='cuda');torch.cuda.synchronize();tick=time.perf_counter()
        with torch.inference_mode():seqs=model.generate(input_ids=x,attention_mask=torch.ones_like(x),**settings,pad_token_id=tok.pad_token_id)
        torch.cuda.synchronize();seconds=time.perf_counter()-tick;eos=model.generation_config.eos_token_id;eos=[eos] if isinstance(eos,int) else eos
        for j,seq in enumerate(seqs):
            tokens=seq[len(ids):].tolist();stop=next((k for k,t in enumerate(tokens) if t in eos),None);n=stop+1 if stop is not None else len(tokens)
            text=tok.decode(tokens[:n],skip_special_tokens=True)
            prefix=tok.decode(tokens[:old[j]['output_tokens']],skip_special_tokens=True)
            append_jsonl(output,{'question_id':q,'candidate_id':old[j]['candidate_id'],'candidate_index':j,
                'question':old[j]['question'],'text':text,'output_tokens':n,'prompt_tokens':len(ids),
                'truncated':stop is None and n>=settings['max_new_tokens'],'max_new_tokens':settings['max_new_tokens'],
                'original_truncated':old[j]['truncated'],'original_output_tokens':old[j]['output_tokens'],
                'same_original_decoded_prefix':prefix==old[j]['text'],
                'completed_control_unchanged':text==old[j]['text'] if not old[j]['truncated'] else None,
                'generation_seconds_for_question':seconds,'model_fingerprint':fp,'adapter_fingerprint':None,
                'protocol':'length_replay_native4096'})
        print(f'Native length replay {len(read_jsonl(output))//4}/{len(batches)} seconds={seconds:.2f}',flush=True)

if __name__=='__main__':main()
