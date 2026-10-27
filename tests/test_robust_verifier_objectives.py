import math
import unittest

import torch
import torch.nn.functional as F

from pilot.robust_verifier_train import loss_for
from pilot.robust_verifier_common import RUN
from pilot.core import ROOT, read_jsonl
from pilot.robust_verifier_report import analyze
from pilot.robust_verifier_common import ARMS


class Objectives(unittest.TestCase):
    def test_sft_is_full_vocabulary_one_label_cross_entropy(self):
        logits = torch.randn(1, 40, dtype=torch.float64, requires_grad=True)
        r = {'chosen_label_id': 32}
        loss = loss_for(logits, r, {}, 'SFT_aug', .1)
        self.assertAlmostEqual(loss.item(), (-logits.log_softmax(-1)[0, 32]).item())
        loss.backward(); self.assertLess(logits.grad[0, 32], 0)
        # A non-label vocabulary token still contributes to the ordinary CE denominator.
        self.assertGreater(logits.grad[0, 10], 0)

    def test_dpo_initial_reference_loss_and_correct_gradient(self):
        for correct in [True, False]:
            logits = torch.zeros(1, 40, requires_grad=True)
            logits.data[0, 32] = 1.25; logits.data[0, 33] = -.75
            r = {'chosen_label_id': 32 if correct else 33, 'rejected_label_id': 33 if correct else 32,
                 'correct': correct, 'job_id': 'one'}
            reference = {'one': {'log_likelihood_A': -2., 'log_likelihood_B': -4.}}
            loss = loss_for(logits, r, reference, 'DPO_aug', .1)
            self.assertAlmostEqual(loss.item(), math.log(2), places=6)
            loss.backward(); self.assertLess(logits.grad[0, r['chosen_label_id']], 0)
            self.assertGreater(logits.grad[0, r['rejected_label_id']], 0)
            self.assertEqual(logits.grad[0, 10], 0)

    def test_dpo_matches_full_likelihood_preference_formula(self):
        logits = torch.randn(1, 40, requires_grad=True); lp = logits.log_softmax(-1)
        r = {'chosen_label_id': 33, 'rejected_label_id': 32, 'correct': False, 'job_id': 'x'}
        ref = {'x': {'log_likelihood_A': -5., 'log_likelihood_B': -2.}}
        expected = -F.logsigmoid(.1*((lp[0, 33]-lp[0, 32])-3.))
        self.assertTrue(torch.allclose(loss_for(logits, r, ref, 'DPO_clean', .1), expected))

    def test_real_masks_and_identical_positive_negative_prefix_coverage(self):
        primary = ROOT/'runs/verifier_robustness_clean_20261026'
        data_root = primary if (primary/'train_aug.jsonl').exists() else RUN
        for mode in ['clean', 'aug']:
            data = read_jsonl(data_root/f'train_{mode}.jsonl'); self.assertEqual(len(data), 640)
            for r in data:
                self.assertEqual(r['labels'][:-1], [-100]*len(r['prompt_ids']))
                self.assertEqual(r['labels'][-1], r['chosen_label_id'])
                self.assertEqual(r['input_ids'], r['prompt_ids']+[r['chosen_label_id']])
            for i in range(0, 640, 2):
                a, b = data[i:i+2]
                self.assertEqual(a['question_id'], b['question_id']); self.assertEqual(a['prefix'], b['prefix'])
                self.assertTrue(a['correct']); self.assertFalse(b['correct'])
            for i in range(0, 640, 8): self.assertEqual(sum(r['correct'] for r in data[i:i+8]), 4)

    def test_all_reject_counts_new_FN_instead_of_claiming_free_FP_repair(self):
        jobs = []; source = {}; scores = {name: {} for name in ['V0']+ARMS}
        for i, (truth, old_accept) in enumerate([(True, True), (True, False), (False, True), (False, False), (None, True), (None, False)]):
            key = str(i)
            jobs.append({'job_id': key, 'candidate_id': key, 'question_id': 'q', 'correct': truth,
                         'cohort': 'synthetic', 'view': 'G3', 'variant': 'original', 'prefix': ''})
            source[key] = {'question': 'q', 'text': 'body', 'gold_answer': '1', 'extracted_answer': '1' if truth else '2'}
            for name in scores:
                accepted = old_accept if name == 'V0' else False
                scores[name][key] = {'accepted': accepted, 'score': .8 if accepted else .2, 'margin': 1. if accepted else -1.}
        tables, _ = analyze(jobs, source, scores)
        for r in tables['repair_damage']:
            self.assertEqual(r['repaired_FP'], 1); self.assertEqual(r['new_FN'], 1)
            self.assertEqual(r['net_repaired'], 0)
            self.assertAlmostEqual(r['FPR_delta'], -.5); self.assertAlmostEqual(r['FNR_delta'], .5)
            self.assertAlmostEqual(r['BA_delta'], 0.)
            self.assertEqual(r['unknown_decisions_changed'], 1)
            self.assertEqual(r['net_repaired_all_lower'], -1); self.assertEqual(r['net_repaired_all_upper'], 1)


if __name__ == '__main__': unittest.main()
