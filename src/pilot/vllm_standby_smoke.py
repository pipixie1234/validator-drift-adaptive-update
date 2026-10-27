"""Queue standby GPU checks after the existing oracle experiment has fully ended."""
import argparse
import importlib.metadata
import json
import os
import subprocess
import time
from pathlib import Path

from .core import ROOT,write_json,write_jsonl,prompt_ids
from .formal_common import protocol,messages,utc,gpu_lock,WorkBudget,sha

REPORT=ROOT/'reports/vllm_setup_20261022'


def execute():
    import torch
    from transformers import AutoTokenizer
    from vllm import LLM,SamplingParams
    from vllm.lora.request import LoRARequest
    from .day2_protocol import label_response
    from .oracle_control import checkpoint
    examples=[('Compute 17 + 23.','40'),('Find the sum of the roots of x^2 - 5*x + 6 = 0.','5'),
              ('Compute 2/3 + 1/6.','5/6'),('How many subsets does a set of five elements have?','32')]
    p=protocol();tok=AutoTokenizer.from_pretrained(p['models']['G']['path'],local_files_only=True)
    prompts=[{'prompt_token_ids':prompt_ids(tok,messages('G',q))} for q,_ in examples]
    settings={'dtype':'bfloat16','lora_dtype':'bfloat16','max_model_len':4096,'tensor_parallel_size':1,
              'gpu_memory_utilization':.70,'max_num_seqs':16,'max_num_batched_tokens':4096,
              'enable_lora':True,'max_lora_rank':16,'max_loras':1,'enforce_eager':True,
              'seed':42,'generation_config':'vllm'}
    adapter=checkpoint('Oracle_SFT');before=sha(adapter/'adapter_model.safetensors')
    started=time.perf_counter();summaries=[];rows=[]
    with gpu_lock():
        budget=WorkBudget('vllm_standby_independent_smoke')
        llm=LLM(model=p['models']['G']['path'],tokenizer=p['models']['G']['path'],**settings)
        params=SamplingParams(n=2,temperature=.7,top_p=.9,top_k=50,max_tokens=256,
                              seed=42,stop_token_ids=[151645,151643])
        for name,request in [('G0',None),('Oracle_adapter',LoRARequest('oracle_standby',1,str(adapter)))]:
            tick=time.perf_counter();outputs=llm.generate(prompts,params,lora_request=request,use_tqdm=False)
            elapsed=time.perf_counter()-tick;assert len(outputs)==4
            tokens=0
            for i,r in enumerate(outputs):
                assert len(r.outputs)==2 and r.prompt_token_ids==prompts[i]['prompt_token_ids']
                for out in r.outputs:
                    text=tok.decode(out.token_ids,skip_special_tokens=True)
                    label=label_response(text,out.finish_reason=='length',examples[i][1],'gsm8k')
                    tokens+=len(out.token_ids)
                    rows.append({'model':name,'synthetic_question':examples[i][0],'gold_for_smoke_only':examples[i][1],
                                 'text':text,'output_tokens':len(out.token_ids),'finish_reason':out.finish_reason,
                                 'correct':label['correct'],'parse_status':label['status']})
            summaries.append({'model':name,'seconds':elapsed,'output_tokens':tokens,'generated_tokens_per_second':tokens/elapsed,
                              'answers':8,'correct':sum(r['correct'] is True for r in rows if r['model']==name),
                              'unknown':sum(r['correct'] is None for r in rows if r['model']==name)})
            budget.charge(name)
        assert sha(adapter/'adapter_model.safetensors')==before
        write_jsonl(REPORT/'independent_smoke_outputs.jsonl',rows)
        result={'passed':True,'completed_at':utc(),'vllm':importlib.metadata.version('vllm'),
                'torch':torch.__version__,'torch_CUDA_build':torch.version.cuda,'settings':settings,
                'sampling':{'n':2,'temperature':.7,'top_p':.9,'top_k':50,'max_tokens':256,'seed':42},
                'models':summaries,'total_seconds_including_initialization':time.perf_counter()-started,
                'LoRA_disk_weights_unchanged':True,'A_B_C_T_questions_read':False,
                'not_formal_evaluation':True,'not_a_matched_TF_speed_benchmark':True,
                'precision_note':'vLLM0.10 LoRA Triton kernels require BF16/FP16 weights; smoke usesBF16 LoRA. Original PEFT inference used FP32 adapters. Numerical equivalence and long-output distribution must be checked before future protocol changes.'}
        write_json(REPORT/'gpu_smoke.json',result)
    print(json.dumps({'GPU_smoke':'passed','models':summaries}),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.execute:execute();return
    current=ROOT/'runs/oracle_control_20261022/state.json'
    while not (REPORT/'installed.json').exists() or not current.exists() or json.loads(current.read_text()).get('status')!='complete':
        write_json(REPORT/'gpu_smoke_status.json',{'stage':'queued_until_install_and_current_control_complete','updated_at':utc(),
                   'does_not_interrupt_current_GPU_jobs':True})
        time.sleep(30)
    write_json(REPORT/'gpu_smoke_status.json',{'stage':'running_after_current_control_finished','started_at':utc()})
    code=subprocess.call([str(ROOT/'scripts/pilot-vllm'),'-m','pilot.vllm_standby_smoke','--execute'],cwd=ROOT)
    write_json(REPORT/'gpu_smoke_status.json',{'stage':'passed' if code==0 else 'needs_attention','exit_code':code,'updated_at':utc()})
    if code:raise SystemExit(code)


if __name__=='__main__':main()
