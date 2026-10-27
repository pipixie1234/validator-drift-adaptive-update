"""Durable sequential controller for the authorized fixed-V0 G0/G1/G2 trajectory."""
import fcntl
import json
import os
import subprocess
import time
import traceback
from .core import ROOT,read_jsonl,write_json,append_jsonl
from .formal_common import RUN,REPORT,PLAN,PROTOCOL,plan,preflight,sha,utc

def steps():
    result=[]
    def add(name,*args):result.append((name,list(args)))
    def baseline(r):
        for s in ['B','C']:add(f'generate_{r}_{s}','formal_multigpu','generate','--round',r,'--split',s)
        for s in ['B','C']:
            add(f'score_{r}_{s}','formal_run','score','--round',r,'--split',s)
            add(f'evaluate_{r}_{s}','formal_labels','evaluate','--round',r,'--split',s)
        add(f'report_{r}','formal_report')
    baseline('G0')
    for source,target in [('G0','G1'),('G1','G2')]:
        add(f'generate_{source}_A','formal_multigpu','generate','--round',source,'--split','A')
        add(f'score_{source}_A','formal_run','score','--round',source,'--split','A')
        add(f'select_{target}','formal_select','--from-round',source,'--target',target)
        add(f'train_{target}_SFT','formal_train','--target',target)
        baseline(target)
    add('final_report','formal_report','--final')
    return result

def main():
    RUN.mkdir(exist_ok=True);REPORT.mkdir(exist_ok=True)
    with (RUN/'.controller.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:print('A formal controller is already active; not starting another.',flush=True);return
        statepath=RUN/'state.json'
        previous=json.loads(statepath.read_text()) if statepath.exists() else {}
        if previous.get('status')=='complete':print('Formal trajectory already complete; final report retained.',flush=True);return
        try:
            from .formal_multigpu_revision import verify_execution_revision
            verify_execution_revision()
            audit=preflight();write_json(RUN/'controller.json',{'pid':os.getpid(),'started_at':utc(),'protocol_sha256':sha(PROTOCOL),'execution_sha256':sha(PLAN)})
            write_json(ROOT/'configs/stage_status.json',{'phase':'formal_fixed_V0_trajectory_running','formal_training_authorized':True,'T_inferred':False})
            current=json.loads((ROOT/'configs/current_protocol.json').read_text());current['formal_training']='running_authorized_fixed_V0';write_json(ROOT/'configs/current_protocol.json',current)
            for index,(name,args) in enumerate(steps()):
                state={'status':'running','stage':name,'stage_index':index+1,'stages_total':len(steps()),'updated_at':utc(),
                    'pid':os.getpid(),'T_inferred':False,'current_log':f'runs/formal/{name}.log'}
                write_json(statepath,state);print(f'[{utc()}] START {index+1}/{len(steps())} {name}',flush=True)
                path=RUN/f'{name}.log';start=time.perf_counter()
                with path.open('ab') as log:
                    child=subprocess.Popen([str(ROOT/'scripts/pilot'),*args],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
                    write_json(RUN/'current_child.json',{'pid':child.pid,'stage':name,'log':str(path.relative_to(ROOT))})
                    last_heartbeat=time.monotonic()
                    while child.poll() is None:
                        time.sleep(5)
                        if time.monotonic()-last_heartbeat>=45:
                            progress=json.loads((RUN/'progress.json').read_text()) if (RUN/'progress.json').exists() else {}
                            print(f'[{utc()}] RUNNING {name} progress={json.dumps(progress,ensure_ascii=False)}',flush=True)
                            last_heartbeat=time.monotonic()
                    code=child.returncode
                append_jsonl(RUN/'stage_history.jsonl',{'stage':name,'exit_code':code,'wall_seconds':time.perf_counter()-start,'ended_at':utc()})
                if code:
                    text=path.read_text(errors='replace')[-5000:]
                    status='budget_limit' if '40h work budget reached' in text else 'failed'
                    write_json(statepath,{**state,'status':status,'exit_code':code,'error_log_tail':text,'updated_at':utc()})
                    write_json(ROOT/'configs/stage_status.json',{'phase':f'formal_{status}','stage':name,'T_inferred':False})
                    print(f'STOP {name} exit={code}; outputs/checkpoints retained; see {path}',flush=True);raise SystemExit(code)
                print(f'[{utc()}] DONE {name}',flush=True)
            write_json(statepath,{'status':'complete','ended_at':utc(),'T_inferred':False,'report':'reports/formal/final_report.md',
                'G_checkpoints':['runs/formal/checkpoints/G1_SFT','runs/formal/checkpoints/G2_SFT'],'V0_updated':False})
            write_json(ROOT/'configs/stage_status.json',{'phase':'fixed_V0_G0_G1_G2_complete','T_inferred':False})
            current=json.loads((ROOT/'configs/current_protocol.json').read_text());current['formal_training']='two_generator_updates_complete_V0_fixed';write_json(ROOT/'configs/current_protocol.json',current)
            print('COMPLETE: reports/formal/final_report.md; T remains sealed.',flush=True)
        except Exception:
            failure=traceback.format_exc();write_json(statepath,{'status':'controller_failed','error':failure,'updated_at':utc(),'T_inferred':False})
            print(failure,flush=True);raise

if __name__=='__main__':main()
