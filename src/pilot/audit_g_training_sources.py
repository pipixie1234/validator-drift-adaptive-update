"""Read-only audit of actual G training tensors, step IDs and generation lineage."""
import csv
import json
import math
import random
from collections import Counter
from pathlib import Path
from .core import ROOT, read_jsonl, training_example, prompt_ids, cache_identity, digest
from .formal_common import RUN, REPORT, inputs, input_path, messages, protocol, plan, formal_generation_settings, sha, utc
from .formal_multigpu_revision import verify_execution_revision
from .runtime import model_identity
from .day3_protocol import select_feedback, quarantined_ids


def main():
    from transformers import AutoTokenizer
    verify_execution_revision()
    destination = REPORT/'training_data_audit_20261022'; destination.mkdir(exist_ok=True)
    p = protocol(); settings = p['training']['G']
    tok = AutoTokenizer.from_pretrained(p['models']['G']['path'], local_files_only=True)
    if tok.pad_token_id is None: tok.pad_token = tok.eos_token
    pools = {s: inputs(s) for s in ['A', 'B', 'C']}
    c_original = read_jsonl(ROOT/'data/dapo/inputs/dapo100.jsonl')
    formal_manifest = json.loads((ROOT/p['formal_split_manifest']).read_text())
    # T membership is read from already-frozen split metadata; never open T question/label files.
    split_sets = {'A': {r['question_id'] for r in pools['A']}, 'B': {r['question_id'] for r in pools['B']},
                  'C': {r['question_id'] for r in c_original}, 'T': set(formal_manifest['splits']['T_final']['question_ids'])}
    overlap = {f'{a}-{b}': len(split_sets[a] & split_sets[b]) for a, b in [('A', 'B'), ('A', 'C'), ('A', 'T'), ('B', 'C'), ('B', 'T'), ('C', 'T')]}
    assert not any(overlap.values())
    question_hash_overlap = {f'A-{s}': len({r['question_hash'] for r in pools['A']} & {r['question_hash'] for r in (pools[s] if s == 'B' else c_original)}) for s in ['B', 'C']}
    assert not any(question_hash_overlap.values())
    raw_files = list(RUN.glob('G[012]_[ABC].jsonl'))+list(RUN.glob('V0_G[012]_[ABC].jsonl'))
    for target in ['G1', 'G2']:
        raw_files += [RUN/f'train/{target}/{name}' for name in ['selected.jsonl', 'tokenized.jsonl', 'steps.jsonl', 'selection.manifest.json', 'training.manifest.json', 'report.json', 'training_ids.json']]
        raw_files += [f for f in (RUN/f'checkpoints/{target}_SFT').iterdir() if f.is_file()]
    original_hashes = {str(path.relative_to(ROOT)): sha(path) for path in raw_files}
    fp = {r: model_identity('G', None if r == 'G0' else RUN/f'checkpoints/{r}_SFT') for r in ['G0', 'G1', 'G2']}
    generations = {}; prompt_cache = {}; generation_audit = []
    for round_name in ['G0', 'G1', 'G2']:
        for split in ['A', 'B', 'C'] if round_name != 'G2' else ['B', 'C']:
            rows = read_jsonl(RUN/f'{round_name}_{split}.jsonl'); vs = read_jsonl(RUN/f'V0_{round_name}_{split}.jsonl')
            byv = {x['candidate_id']: x for x in vs}; qmap = {x['question_id']: x for x in pools[split]}
            assert len(rows) == len(vs) == len(qmap)*4
            assert Counter(x['question_id'] for x in rows) == Counter({q: 4 for q in qmap})
            assert len({x['candidate_id'] for x in rows}) == len(rows)
            for r in rows:
                q = qmap[r['question_id']]; vr = byv[r['candidate_id']]
                assert r['question'] == q['question'] and r['split'] == split and r['G_round'] == round_name
                assert not any(k in r for k in ['gold_answer', 'correct', 'reference'])
                assert (r['model_fingerprint'], r['adapter_fingerprint']) == fp[round_name]
                msgs = messages('G', q['question'])
                if q['question_id'] not in prompt_cache: prompt_cache[q['question_id']] = prompt_ids(tok, msgs)
                ids = prompt_cache[q['question_id']]; actual = formal_generation_settings(len(ids))
                seed = plan()['seed']+int(digest(q['question_id'])[:8], 16)%1000000
                identity = cache_identity('G', *fp[round_name], q['question_id'], msgs, dict(actual, seed=seed))
                assert r['generation_seed'] == seed and r['prompt_sha256'] == digest(msgs)
                assert r['prompt_tokens'] == len(ids) and r['max_new_tokens'] == actual['max_new_tokens']
                assert r['prompt_tokens']+r['output_tokens'] <= 4096
                assert r['question_cache_key'] == identity
                assert r['cache_key'] == digest({'question_key': identity, 'candidate_index': r['candidate_index']})
                assert r['candidate_id'] == f"{r['question_id']}:formal:{round_name}:{r['candidate_index']}"
                assert vr['model_fingerprint'] == p['models']['V']['fingerprint'] and vr['adapter_fingerprint'] is None and vr['threshold'] == .5
                assert vr['prompt_sha256'] == digest(messages('V', q['question'], r['text']))
                assert vr['prompt_tokens']+4 <= 8192
                difference = vr['log_likelihood_A']-vr['log_likelihood_B']
                score = 1/(1+math.exp(-difference)) if difference >= 0 else math.exp(difference)/(1+math.exp(difference))
                assert abs(score-vr['score']) < 2e-7 and vr['accepted'] == (difference >= 0)
            generations[(round_name, split)] = rows
            generation_audit.append({'round': round_name, 'split': split, 'questions': len(qmap), 'candidates': len(rows),
                'question_text_and_pool_membership_match': True, 'model_adapter_prompt_sampling_seed_cache_identity_match': True,
                'no_reference_fields_in_G_input_records': True, 'V0_full_response_prompt_hash_and_frozen_scoring_match': True})
            print(f'Generation verified {round_name}/{split}: {len(rows)} candidates', flush=True)
    audit_rows = []; training = {}
    for target, source in [('G1', 'G0'), ('G2', 'G1')]:
        directory = RUN/f'train/{target}'; selected = read_jsonl(directory/'selected.jsonl'); tokens = read_jsonl(directory/'tokenized.jsonl')
        steps = read_jsonl(directory/'steps.jsonl'); selection = json.loads((directory/'selection.manifest.json').read_text())
        train_manifest = json.loads((directory/'training.manifest.json').read_text()); report = json.loads((directory/'report.json').read_text())
        raw = generations[(source, 'A')]; byid = {x['candidate_id']: x for x in raw}
        vs = {x['candidate_id']: x for x in read_jsonl(RUN/f'V0_{source}_A.jsonl')}
        assert selection['source_G_sha256'] == sha(RUN/f'{source}_A.jsonl')
        assert selection['source_V0_sha256'] == sha(RUN/f'V0_{source}_A.jsonl')
        assert train_manifest['tokenized_sha256'] == sha(directory/'tokenized.jsonl')
        assert train_manifest['selected_sha256'] == sha(directory/'selected.jsonl')
        assert train_manifest['runner_sha256'] == sha(ROOT/'src/pilot/formal_train.py')
        assert selection['preparation_code_sha256'] == sha(ROOT/'src/pilot/formal_select.py')
        assert json.loads((RUN/f'checkpoints/{target}_SFT/training_provenance.json').read_text()) == train_manifest
        assert json.loads((directory/'training_ids.json').read_text()) == selected
        picked = select_feedback(raw, vs, .5, settings['seed'], settings['max_examples_per_round'], quarantined_ids(), settings['max_length'])
        rebuilt = []
        for r in picked:
            ex = training_example(tok, messages('G', r['question']), r['text'], settings['max_length'])
            if ex is not None: rebuilt.append((r, ex))
        assert [r['candidate_id'] for r, _ in rebuilt] == [r['candidate_id'] for r in selected] == selection['selected_ids']
        assert len(rebuilt) == len(tokens) == report['selected_examples']
        memberships = Counter(); exact_tensor = 0
        for chosen, encoded, (expected_raw, expected_tokens) in zip(selected, tokens, rebuilt):
            assert chosen['candidate_id'] in byid
            original = byid[chosen['candidate_id']]
            assert all(chosen[k] == original[k] for k in ['question_id', 'question', 'text', 'G_round', 'prompt_tokens', 'output_tokens'])
            assert encoded['question_id'] == chosen['question_id'] and encoded['candidate_id'] == chosen['candidate_id']
            assert all(encoded[k] == value for k, value in expected_tokens.items())
            assert chosen['V0_score'] == vs[chosen['candidate_id']]['score'] >= .5
            assert not any(k in chosen for k in ['gold_answer', 'correct', 'reference'])
            actual_pools = [s for s, ids in split_sets.items() if chosen['question_id'] in ids]
            assert actual_pools == ['A']; memberships.update(actual_pools); exact_tensor += 1
            audit_rows.append({'target': target, 'source_round': source, 'question_id': chosen['question_id'], 'candidate_id': chosen['candidate_id'],
                'actual_pool': 'A', 'in_B': False, 'in_C': False, 'in_T': False, 'selected_text_exactly_matches_raw_A': True,
                'input_ids_labels_attention_mask_exactly_reconstructed': True, 'input_tokens': len(encoded['input_ids']),
                'supervised_tokens': sum(t != -100 for t in encoded['labels'])})
        order = list(range(len(selected))); random.Random(settings['seed']).shuffle(order)
        expected_order = [tokens[i]['candidate_id'] for i in order]
        consumed = [cid for step in steps for cid in step['candidate_ids']]
        assert consumed == expected_order and len(set(consumed)) == len(selected)
        assert len(steps) == report['optimizer_steps'] == math.ceil(len(selected)/8)
        assert sum(s['examples'] for s in steps) == len(selected)
        assert sum(s['input_tokens'] for s in steps) == sum(len(t['input_ids']) for t in tokens) == report['input_tokens']
        assert sum(s['supervised_tokens'] for s in steps) == sum(sum(x != -100 for x in t['labels']) for t in tokens) == report['supervised_tokens']
        checkpoint_count = 0
        for path in (directory/'checkpoints').glob('step_*/complete.json'):
            complete = json.loads(path.read_text()); assert complete['training_manifest_sha256'] == sha(directory/'training.manifest.json'); checkpoint_count += 1
        assert report['final_adapter_fingerprint'] == fp[target][1]
        assert train_manifest['parent_adapter_fingerprint'] == (fp['G1'][1] if target == 'G2' else None)
        training[target] = {'source_G_file': f'runs/formal/{source}_A.jsonl', 'source_V0_file': f'runs/formal/V0_{source}_A.jsonl',
            'selected_examples': len(selected), 'supervised_training_pool_counts': {s: memberships[s] for s in ['A', 'B', 'C', 'T']},
            'exact_A_text_and_training_tensor_reconstructions': exact_tensor, 'frozen_selection_reproduces_all_selected_ids': True,
            'optimizer_steps': len(steps), 'logged_consumption_exactly_matches_seeded_A_only_order': True,
            'each_selected_response_consumed_once': True, 'checkpoint_manifests_verified': checkpoint_count,
            'input_tokens': report['input_tokens'], 'supervised_tokens': report['supervised_tokens'],
            'B_engineering_probe_used': True, 'probe_supplies_loss_or_backward_examples': False}
        print(f'Training verified {target}: {len(selected)} A-only exact tensors, {len(steps)} logged steps', flush=True)
    probe = pools['B'][0]; probe_ids = prompt_ids(tok, messages('G', probe['question']))[:128]
    result = {'checked_at': utc(), 'scope': 'Artifacts, exact CPU retokenization, recorded step consumption, source and cache hashes; no training rerun',
        'audit_code_sha256': sha(Path(__file__)), 'question_id_split_overlap_counts': overlap,
        'A_vs_B_C_exact_question_hash_overlap_counts': question_hash_overlap, 'T_questions_or_labels_decoded': False,
        'generation_audit': generation_audit, 'training_audit': training,
        'B_access_finding': {'exists': True, 'file': 'src/pilot/formal_train.py', 'line': 83,
            'question_id': probe['question_id'], 'probe_prompt_tokens': len(probe_ids), 'probe_ids_sha256': digest(probe_ids),
            'input': 'G prompt containing only first B question text, truncated to first128 prompt tokens; no B generated answer or gold label',
            'purpose': 'G2 parent-adapter continuity and before/after-save next-token logits consistency checks',
            'execution': 'model.eval(), torch.inference_mode(), use_cache=False; no loss, backward or optimizer step on probe',
            'training_rng': 'Fresh rounds reset seed after initial probe; resume restores checkpoint RNG after probe',
            'reported_save_reload_max_logit_difference': {r: json.loads((RUN/f'train/{r}/report.json').read_text())['reload_max_logit_difference'] for r in ['G1', 'G2']},
            'disclosure': 'B was accessed by training engineering checks. Saying B was only used for external evaluation was incomplete. No B/C/T supervised samples found.'},
        'original_file_hashes': original_hashes, 'original_code_data_weights_modified_by_audit': False,
        'training_replayed_without_probe': False, 'limitation': 'This audit verifies available artifacts and matching frozen implementation, not a counterfactual bitwise replay without the probe; approximate family dedup does not prove semantic independence.'}
    assert all(sha(ROOT/name) == h for name, h in original_hashes.items())
    (destination/'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    with (destination/'training_rows.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(audit_rows[0])); writer.writeheader(); writer.writerows(audit_rows)
    (destination/'audit_report.md').write_text(f'''# G更新数据来源复核

结论：逐条重建1001条实际训练张量、复现筛选并核对两轮126个优化器步骤记录，未发现B/C/T样本进入SFT监督数据。发现训练脚本明确读取B第一题前128个提示token，用作无梯度工程探针；这一接触需要单列，不能把“未用于梯度训练”说成“完全没有使用B”。

| 更新 | A监督样本 | B监督样本 | C监督样本 | T监督样本 | 精确重建训练张量 | 优化器步数 |
|---|---:|---:|---:|---:|---:|---:|
| G0→G1 | 500 | 0 | 0 | 0 | 500/500 | 63 |
| G1→G2 | 501 | 0 | 0 | 0 | 501/501 | 63 |

## 为什么出现B

当时用户明确执行顺序包含“用正式长度预算生成并缓存B/C，再由V0评分”和“每轮保存B/C输出并用V0评价”。因此生成、评分B用于跨轮诊断，之后也可用于预留的V更新池；这不授权将B答案用于G的SFT。后续说“B不是本轮必需、可以放附录”，是对本轮最小分析范围的澄清，不能被用来改写已经执行的B/C清单。

另外，实施者在训练脚本中选择B第一题作为固定工程探针，目的是检查G2继承G1和adapter保存重载是否保持相同输出。代码位置为formal_train.py第83、86、137、144行：输入只有题目提示，不使用该题G回答或标准答案；model.eval及torch.inference_mode下取logits，use_cache=False。初始检查后再设置训练模式并重置/恢复训练随机状态。进入反向传播的loss来自data[index]的训练张量。该探针的选题与披露不够严格；更清楚的做法是预先约定独立固定文本或A题，但本次不事后修改原实现或隐藏这段记录。

## 实际核验的证据

1. A/B/C/T的题目ID交集均为0；A与B/C的记录题面哈希交集为0。T仅检查已有划分元数据中的ID，不打开题目或标签。语义近似重题无法据此完全排除。
2. G1筛选来源哈希匹配G0_A和V0_G0_A；G2匹配G1_A和V0_G1_A。按冻结0.5阈值、每题随机至多1条及长度规则重算，500/501条候选ID完全一致。独立正确性标签不参与复算筛选。
3. 每条selected的问题、生成回答、候选ID和V0分数逐一匹配A原始缓存。重新tokenize并构造response-only模板，对全部input_ids、attention_mask、labels与prefix_length进行精确比较，1001条全部一致。训练labels是G回答token及prompt掩码，不是标准答案标签。
4. 对两轮各63步steps.jsonl，实际candidate_ids顺序完全等于种子42的A样本打乱顺序，每条只消费一次；样本、输入与监督token总量均与记录一致。保存检查点与最终adapter的provenance哈希匹配训练清单，G2实际继承G1。
5. 所有8352条生成记录的题池和题面、模型及adapter、提示、逐题种子、预算和缓存键匹配冻结实现；所有V0评分的完整回答提示哈希、模型、0.5阈值和对数似然归一化均通过。未发现把B问题伪装成A生成的问题。
6. 本次只读审计原代码、数据和模型文件，审计前后哈希一致。审计明细为audit.json，每条训练样本索引为training_rows.csv。

## 可以与不可以据此得出的结论

现有记录与精确张量核验支持“G的SFT监督样本只来自A”，不支持“B答案被加入SFT导致这次结果”的判断。B题面确实被用于无梯度工程检查，之前将其用途仅描述为外部诊断不完整，应予纠正。本次没有做去掉工程探针后的反事实训练重放，不能将产物审计冒称为这种实验，也不据此声称任何执行路径逐bit完全相同。未新增训练，未删除B记录，未修改原结果。

复现：`scripts/pilot audit_g_training_sources`。完整复核在CPU完成；T仍封存。
''')
    print(json.dumps({'training_supervised_rows': {r: x['supervised_training_pool_counts'] for r, x in training.items()},
        'exact_reconstructed_training_tensors': len(audit_rows), 'B_prompt_in_engineering_probe': True,
        'raw_generation_and_V0_scores_verified': sum(x['candidates'] for x in generation_audit), 'report': str(destination/'audit_report.md')}, ensure_ascii=False), flush=True)


if __name__ == '__main__': main()
