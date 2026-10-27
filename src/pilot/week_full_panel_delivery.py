"""Append the new full-cohort diagnostic without changing existing experiments."""
import json
import shutil
from pathlib import Path

from .core import ROOT, write_json
from .formal_common import sha, utc
from .prefix_full_panel import REPORT as PANEL
from .week_prefix_delivery import REPORT, pdf_tools, verify_figures, table

BACKUP = ROOT/'runs/week_final_20261026/delivery_before_full_panel'


def main():
    Reader, Writer = pdf_tools()
    assert json.loads((PANEL/'independent_verification.json').read_text())['status'] == 'passed'
    if not BACKUP.exists():
        BACKUP.mkdir(parents=True)
        names = ['week_final_figures.pdf', 'prefix_figures_guide.pdf', 'figure_sections.json', 'week_final_figures_index.md',
                 'prefix_findings_report.md', 'week_final_report.md', 'README.md', 'prefix_integration_audit.json',
                 'week_final_delivery.zip', 'delivery_SHA256.json', 'delivery_verification.json', 'independent_verification.json']
        for name in names: shutil.copy2(REPORT/name, BACKUP/name)
        write_json(BACKUP/'backup_manifest.json', {name: sha(BACKUP/name) for name in names})
    assert all(sha(BACKUP/name) == h for name, h in json.loads((BACKUP/'backup_manifest.json').read_text()).items())
    # Always start from the prior 32-page version, so reruns never append twice.
    manifest = json.loads((BACKUP/'figure_sections.json').read_text()); assert manifest['total_pages'] == 32
    audit = json.loads((BACKUP/'prefix_integration_audit.json').read_text())
    source_records = audit['source_files'][:]
    destination = REPORT/'prefix_sources'/PANEL.name
    for p in sorted(PANEL.iterdir()):
        if not p.is_file(): continue
        destination.mkdir(parents=True, exist_ok=True); target = destination/p.name; shutil.copy2(p, target)
        assert sha(target) == sha(p)
        source_records.append({'source': str(p.relative_to(ROOT)), 'copy': str(target.relative_to(REPORT)), 'sha256': sha(p)})
    sections = manifest['sections']
    relative = str((destination/'full_panel_figures.pdf').relative_to(REPORT))
    sections.append({'title_cn': '完整C95面板：错误优势与正确收益', 'title_en': 'Complete C95 panel: wrong advantage and correct benefit',
                     'source_pdf': relative, 'pages': 3, 'start_page': 33, 'end_page': 35,
                     'report': str((destination/'full_panel_report.md').relative_to(REPORT)), 'source_sha256': sha(destination/'full_panel_figures.pdf')})
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(8.27, 11.69)); fig.patch.set_facecolor('white')
    fig.text(.07, .94, 'Week final: figure guide', fontsize=20, weight='bold')
    fig.text(.07, .90, 'Final-T results and prefix diagnostics, including the new complete panel', fontsize=10)
    fig.text(.07, .86, 'Section', fontsize=11, weight='bold'); fig.text(.85, .86, 'Pages', fontsize=11, weight='bold')
    for i, s in enumerate(sections):
        y = .815-i*.055
        fig.text(.07, y, s['title_en'], fontsize=9.3)
        fig.text(.85, y, f'{s["start_page"]}-{s["end_page"]}', fontsize=10)
    fig.text(.07, .12, 'New C95 panel: ALL four G3 answers per question, including FP and FN.\n'
             'Six prefixes plus original and neutral control: 3,040 logical scores.\n'
             'V0 fixed. Both FPR and FNR, continuous scores, and removal tradeoffs.\n'
             'No new G generation or training; no new T interventions.', fontsize=9.5, linespacing=1.6)
    fig.savefig(REPORT/'prefix_figures_guide.pdf'); plt.close(fig)
    writer = Writer()
    for s in sections:
        s['source_sha256'] = sha(REPORT/s['source_pdf'])
        writer.append(str(REPORT/s['source_pdf']), outline_item=s['title_cn'], import_outline=False)
    writer.add_metadata({'/Title': 'Week final and prefix diagnostics: complete C95 panel', '/Subject': 'Fixed V0; all truths and all original decisions'})
    target = REPORT/'week_final_figures.pdf'; tmp = target.with_suffix('.tmp.pdf')
    with tmp.open('wb') as f: writer.write(f)
    writer.close(); tmp.replace(target)
    manifest.update({'at': utc(), 'total_pages': 35, 'sections': sections,
                     'new_inference': True, 'new_inference_scope': 'C95 full-answer prefix diagnostic only; old T results unchanged'})
    write_json(REPORT/'figure_sections.json', manifest); checked = verify_figures()
    toc = table(['部分', '合并图册页码', '报告'], [[s['title_cn'], f'{s["start_page"]}—{s["end_page"]}', '[查看]('+s['report']+')'] for s in sections])
    (REPORT/'week_final_figures_index.md').write_text('# 合并图册目录\n\n前6页为原T评价，原八组前缀图保留在8—32页；新增完整C95面板为33—35页。\n\n'+toc)
    result = json.loads((PANEL/'results.json').read_text()); rows = result['net_rates']
    panel_link = str((destination/'full_panel_report.md').relative_to(REPORT))
    supplement = '\n## 9. 新增完整C95面板：全部错误回答上的净优势及正确回答代价\n\n'
    supplement += '纳入全部95题×4条已有G3回答：正确147、错误218、未知15；原TP126/FN21/FP119/TN99全部包括。六前缀加原文和中性控制，共3040逻辑评分；固定原V0、完整正文、答案标签及0.5阈值。\n\n'
    supplement += table(['前缀', 'FPR', 'ΔFPR/百分点', 'FNR', 'ΔFNR/百分点'],
                        [[r['variant'], f'{r["FPR"]:.2%}', f'{100*r["FPR_delta"]:+.2f}', f'{r["FNR"]:.2%}', f'{100*r["FNR_delta"]:+.2f}'] for r in rows])
    supplement += '\n五个代表前缀同时提高全部错误回答上的FPR、降低正确回答FNR，在本次12项BH检验中均通过；reason-step-by-step方向相反且未通过。前缀选自既有探索、C已被重复使用，BH12不能校正整个历史选词过程。\n\n'
    supplement += '例如we can让23条TN→FP，未发生FP→TN；同时10条FN→TP、1条TP→FN。若精确去掉这次人工加上的前缀，便能消除23条FP，同时重新增加净9条FN。它不等于自然删词或部署词汇惩罚器的效果。\n\n'
    supplement += '错误与正确回答的平均分提升之差、margin提升之差，其95%区间均跨0；支持某些表达使V更愿接受，尚不能确认对错误回答有选择性的额外偏爱。完整报告同时提供双向翻转、连续评分、接受集风险、BA、未知界限和精确逆操作的取舍。\n\n'
    supplement += f'[新增完整报告]({panel_link}) · [Excel](prefix_sources/{PANEL.name}/full_panel_review.xlsx) · [三页图](prefix_sources/{PANEL.name}/full_panel_figures.pdf)\n\n'
    text = (BACKUP/'prefix_findings_report.md').read_text()
    text = text.replace('本报告将八组已完成诊断放到week_final交付中；仅整理旧缓存与图表，没有新增生成、V评分或训练。',
                        '本报告汇总八组既有诊断，并新增第9组完整C95前缀面板。前八组沿用旧缓存与图表；第9组补齐缺失V0评分，没有新增G生成或任何训练。')
    text = text.replace('没有完成新的100题×4回答完整面板；本次按安排只整理已有证据，原数据和结果都保留。',
                        '现已新增95题×4回答的完整面板，见第9节；未知标签保留，原数据和结果全部保留。')
    text = text.split('## 图册页码与原报告')[0] + supplement + '\n## 图册页码与原报告\n\n' + toc
    text += '\n[打开合并图册](week_final_figures.pdf) · [返回七天收尾报告](week_final_report.md)\n'
    (REPORT/'prefix_findings_report.md').write_text(text)
    maintext = (BACKUP/'week_final_report.md').read_text()
    maintext = maintext.replace('同前缀四阶段测试已有完整表，但不是100题×4回答的全体面板。此次仅整理，不新增评分、训练或标签。',
                                '同前缀四阶段表已整理；随后新增C95×4完整面板，补齐全部错误回答的净FPR与正确回答FNR、连续评分和双向翻转。新面板只补V0评分，不新增G生成或训练。')
    maintext += '\n\n## 新增完整C95前缀面板\n\n'+f'[完整报告]({panel_link})，合并图册第33—35页。五个代表前缀呈现FPR上升、FNR下降；错误相比正确回答的连续评分额外提升尚无明确证据。删除人工前缀同时存在减少FP和新增FN的取舍。详见前缀总报告第9节，原T结果保持。\n'
    (REPORT/'week_final_report.md').write_text(maintext)
    readme = (BACKUP/'README.md').read_text().replace('32页，含原T六页及此前八组诊断', '35页，含原T六页、此前八组诊断及新增完整C95面板')
    readme += f'\n- [新增完整C95面板]({panel_link})：全体FP/TN/TP/FN、连续分数与删除取舍；图册33—35页。\n'
    (REPORT/'README.md').write_text(readme)
    compute = json.loads((PANEL/'compute.json').read_text())
    audit.update({'at': utc(), **checked, 'source_reports': 9, 'source_copies': len(source_records), 'source_files': source_records,
                  'new_G_generation': 0, 'new_V_scores': compute['fresh_this_invocation'],
                  'new_V_sentinel_scores': compute['sentinel_rechecks_this_invocation'], 'new_training_steps': 0,
                  'new_full_panel': {'logical_scores': 3040, 'fresh': compute['fresh_this_invocation'],
                                     'reused': compute['reused_this_invocation'], 'B_used': False, 'T_used': False},
                  'prior_delivery_backup': str(BACKUP.relative_to(ROOT)), 'merged_pdf_sha256': sha(target)})
    for r in source_records: assert sha(ROOT/r['source']) == r['sha256'] == sha(REPORT/r['copy'])
    write_json(REPORT/'prefix_integration_audit.json', audit)
    shutil.copy2(Path(__file__), REPORT/'week_full_panel_delivery.py')
    from .week_final_report import package_delivery
    package_delivery(True)
    print(json.dumps(checked, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__': main()
