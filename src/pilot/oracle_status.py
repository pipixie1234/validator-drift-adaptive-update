"""Human-readable status for the durable oracle-control run."""
import argparse
import json
import time
from datetime import datetime,timezone,timedelta
from .core import read_jsonl
from .oracle_control import RUN,REPORT,ARMS


def update():
    stamp=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S 北京时间')
    counts={arm:len(read_jsonl(RUN/arm/'eval/generated_C.jsonl')) for arm in ARMS}
    scores={arm:len(read_jsonl(RUN/arm/'eval/V0_C.jsonl')) for arm in ARMS}
    complete=(RUN/'state.json').exists() and json.loads((RUN/'state.json').read_text()).get('status')=='complete'
    lines=['# Oracle对照进度','',f'更新：{stamp}','',
           '状态：'+('完整评估与报告已完成。' if complete else '后台持续运行；未按中途效果调整或停止。'),'',
           '| 分支 | 训练 | C回答 | V0评分 |','|---|---|---|---|']
    for arm in ARMS:
        r=RUN/arm/'train/G1/report.json'
        train='完成：289题，225565监督token，37步' if r.exists() else '待完成'
        lines.append(f'| {arm} | {train} | {counts[arm]}/384 | {scores[arm]}/384 |')
    lines += ['','两组均从原G0开始；共同A题目，完整上下文，不重复正确样本。B未用于训练或评估，T封存。',
              '训练合计约161秒；完整开发生成显著更久，单卡4个原采样进程、每分支2个。输出增量落盘，可断线继续。',
              '后台会话：`tmux attach -t oracle_control_eval_20261022`；总日志：`evaluation.log`。',
              '完成后报告：`oracle_control_report.md`。']
    (REPORT/'progress_report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'completed':complete,'generated':counts,'scored':scores}),flush=True)
    return complete


def main():
    p=argparse.ArgumentParser();p.add_argument('--watch',action='store_true');a=p.parse_args()
    while True:
        done=update()
        if not a.watch or done:break
        time.sleep(30)


if __name__=='__main__':main()
