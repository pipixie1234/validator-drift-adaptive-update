"""Prepare an isolated CUDA12.6 vLLM standby environment without touching GPU jobs."""
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin
import requests

from .core import ROOT,write_json
from .formal_common import utc

REPORT=ROOT/'reports/vllm_setup_20261022'
CACHE=ROOT/'.cache/vllm_wheels'
ENV=ROOT/'.venv-vllm'
PACKAGES=['torch','torchvision','torchaudio','transformers','numpy','peft']


def versions():
    return {p:importlib.metadata.version(p) for p in PACKAGES}


def status(stage,**fields):
    write_json(REPORT/'status.json',{'stage':stage,'updated_at':utc(),**fields})
    print(json.dumps({'stage':stage,**fields}),flush=True)


def download(asset):
    target=CACHE/asset['name'];part=target.with_suffix(target.suffix+'.part')
    expected=asset['digest'].removeprefix('sha256:')
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==expected:return target
    urls=[asset['browser_download_url'],'https://ghfast.top/'+asset['browser_download_url']]
    for attempt in range(6):
        url=urls[attempt % len(urls)]
        begin=part.stat().st_size if part.exists() else 0
        headers={'Range':f'bytes={begin}-'} if begin else {}
        try:
            with requests.get(url,headers=headers,stream=True,timeout=(20,45)) as r:
                r.raise_for_status()
                if r.status_code!=206:begin=0
                downloaded=begin;start=time.perf_counter();last=0
                with part.open('ab' if begin else 'wb') as f:
                    for chunk in r.iter_content(1048576):
                        f.write(chunk);downloaded+=len(chunk)
                        if time.perf_counter()-last>5:
                            elapsed=time.perf_counter()-start
                            status('downloading_official_cu126_wheel',downloaded_bytes=downloaded,total_bytes=asset['size'],
                                   transfer_MiB_per_second=(downloaded-begin)/1048576/max(elapsed,.001),
                                   source='official_GitHub' if attempt%2==0 else 'public_download_proxy_with_official_SHA256_validation')
                            last=time.perf_counter()
            if part.stat().st_size!=asset['size']:raise ValueError('Wheel byte count differs from official release')
            actual=hashlib.sha256(part.read_bytes()).hexdigest()
            if actual!=expected:
                part.rename(part.with_suffix(part.suffix+'.checksum_failed'))
                raise ValueError('Wheel SHA256 differs from official GitHub release digest')
            part.replace(target)
            return target
        except Exception as e:
            status('download_retry',attempt=attempt+1,error=type(e).__name__,message=str(e).splitlines()[0][:160])
            time.sleep(3)
    raise RuntimeError('All resumable wheel download attempts failed')


def main():
    REPORT.mkdir(exist_ok=True);CACHE.mkdir(exist_ok=True)
    before=versions();write_json(REPORT/'original_environment_before.json',before)
    asset=json.loads((REPORT/'official_asset.json').read_text())
    assert asset['name']=='vllm-0.10.0+cu126-cp38-abi3-manylinux1_x86_64.whl'
    assert before['torch']=='2.7.1+cu126'
    wheel=download(asset)
    write_json(REPORT/'wheel_verified.json',{'name':wheel.name,'bytes':wheel.stat().st_size,
               'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'official_SHA256_match':True,'verified_at':utc()})
    constraint=REPORT/'constraints.txt'
    constraint.write_text('torch==2.7.1+cu126\ntorchvision==0.22.1+cu126\ntorchaudio==2.7.1+cu126\ntransformers==4.56.2\nnumpy==2.2.6\nvllm==0.10.0+cu126\n')
    links=json.loads((REPORT/'xformers_official_links.json').read_text())
    assert len(links)==1
    xf=urljoin('https://download.pytorch.org',links[0])
    # pip respects inherited system-site packages; uv's resolver ignores them and
    # would unnecessarily reinstall the existing multi-GB PyTorch stack.
    command=[str(ENV/'bin/python'),'-m','pip','install','--prefer-binary','--retries','5','--timeout','90',
             '--index-url','https://mirrors.huaweicloud.com/repository/pypi/simple/',
             '--extra-index-url','https://download.pytorch.org/whl/cu126',
             '--constraint',str(constraint),str(wheel),xf]
    write_json(REPORT/'install_plan.json',{'command':command,'environment':str(ENV),'system_packages_readonly_reuse':True,
               'vllm_version':'0.10.0+cu126','torch_version':'2.7.1+cu126','xformers_source':xf,
               'original_python':sys.executable,'original_packages':before,'GPU_inference_during_active_control':False})
    status('installing_dependencies',environment=str(ENV),wheel_SHA256_verified=True)
    with (REPORT/'pip_install.log').open('ab') as f:
        proc=subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,cwd=ROOT,env=dict(os.environ,PIP_CACHE_DIR=str(ROOT/'.cache/pip-vllm')))
    if proc.returncode:raise RuntimeError(f'pip exited{proc.returncode}; see pip_install.log')
    status('checking_CPU_imports',GPU_visible=False)
    code='''import json,torch,transformers,numpy,vllm,importlib.metadata as m
import vllm._C
print(json.dumps({'vllm':m.version('vllm'),'torch':torch.__version__,'torch_file':torch.__file__,'torch_CUDA_build':torch.version.cuda,'transformers':transformers.__version__,'numpy':numpy.__version__,'native_extension_imported':True,'CUDA_visible':torch.cuda.device_count()}))'''
    result=subprocess.run([str(ENV/'bin/python'),'-c',code],cwd=ROOT,capture_output=True,text=True,
                          env=dict(os.environ,CUDA_VISIBLE_DEVICES='',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1'),timeout=120)
    (REPORT/'import_check.log').write_text(result.stdout+'\n'+result.stderr)
    if result.returncode:raise RuntimeError('CPU-side import check failed; see import_check.log')
    info=json.loads(result.stdout.strip().splitlines()[-1]);assert info['torch']=='2.7.1+cu126' and info['vllm']=='0.10.0+cu126'
    after=versions();assert after==before,'Original environment unexpectedly changed'
    write_json(REPORT/'original_environment_after.json',after)
    write_json(REPORT/'installed.json',{'completed_at':utc(),'versions':info,'original_environment_unchanged':True,
               'GPU_generation_test':'pending until oracle_control GPU jobs release the card','environment':str(ENV),
               'official_asset_metadata_sha256':hashlib.sha256((REPORT/'official_asset.json').read_bytes()).hexdigest()})
    subprocess.run([str(ENV/'bin/python'),'-m','pip','freeze','--local'],stdout=(REPORT/'requirements_local.txt').open('w'),check=True)
    status('installed_CPU_import_passed_GPU_test_pending',versions=info,original_environment_unchanged=True)


if __name__=='__main__':
    try:main()
    except Exception as e:
        status('needs_attention',error=type(e).__name__,message=str(e)[:250]);raise
