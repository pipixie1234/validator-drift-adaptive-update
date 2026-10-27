import math
import platform
import subprocess
from .core import *

def command(cmd):
    try:return subprocess.check_output(cmd,text=True,stderr=subprocess.STDOUT)
    except Exception as e:return str(e)

def main():
    import torch
    from transformers import AutoTokenizer,AutoConfig
    info={'python':platform.python_version(),'torch':torch.__version__,'torch_cuda_runtime':torch.version.cuda,'bf16':torch.cuda.is_bf16_supported(),'gpu':command(['nvidia-smi']),'nvcc':command(['nvcc','--version']),'memory':command(['free','-h']),'disk':command(['df','-h',str(ROOT)]),'venv':str(ROOT/'.venv'),'isolation':'venv --system-site-packages: reuse immutable existing Torch/CUDA; project dependencies installed in venv','models':{}}
    for role in ['G','V']:
        p=Path(CFG[f'{role}_MODEL_DIR']);config=AutoConfig.from_pretrained(p,local_files_only=True);tok=AutoTokenizer.from_pretrained(p,local_files_only=True)
        actual={};n=0;files=[]
        for f in sorted(p.glob('*.safetensors')):
            with f.open('rb') as stream:
                first=stream.read(8);assert not first.startswith(b'version '),'LFS pointer'
                length=int.from_bytes(first,'little');header=json.loads(stream.read(length))
            tensors={k:v for k,v in header.items() if k!='__metadata__'}
            assert max(v['data_offsets'][1] for v in tensors.values())+length+8==f.stat().st_size
            for name,value in tensors.items():assert name not in actual;actual[name]=f.name;n+=math.prod(value['shape'])
            files.append({'name':f.name,'bytes':f.stat().st_size,'header_sha256':digest(header)})
        index=p/'model.safetensors.index.json'
        if index.exists():assert json.loads(index.read_text())['weight_map']==actual
        sample=messages(role,'What is 1 + 1?','#### 2' if role=='V' else None)
        info['models'][role]={'path':str(p),'model_type':config.model_type,'architectures':config.architectures,'stored_parameters':n,'weights':files,'weight_index_verified':index.exists(),'chat_template_tokens':len(prompt_ids(tok,sample)),'label_token_ids':{x:tok.encode(x,add_special_tokens=False) for x in ['A','B']} if role=='V' else None}
    x=torch.randn(256,256,device='cuda',dtype=torch.bfloat16);info['bf16_matmul_finite']=bool(torch.isfinite(x@x).all())
    write_json(ROOT/'reports/environment.json',info)
    (ROOT/'reports/pip-freeze.txt').write_text(command([str(ROOT/'.venv/bin/python'),'-m','pip','freeze','--all']))
    (ROOT/'reports/pip-check.txt').write_text(command([str(ROOT/'.venv/bin/python'),'-m','pip','check']))
    print(json.dumps(info,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
