import unittest
from pilot.day3_protocol import select_feedback,risk_summary,formal_generation_settings
from pilot.day3_finish import excel_literal

class Day3PolicyTests(unittest.TestCase):
    def rows(self):
        rows=[];scores={}
        for q in ['bad','good']:
            for i in range(4):
                cid=f'{q}:{i}'
                rows.append({'question_id':q,'candidate_id':cid,'candidate_index':i,
                    'truncated':False,'prompt_tokens':100,'output_tokens':200,'correct':i%2==0})
                scores[cid]={'score':.9}
        return rows,scores

    def test_whole_question_excluded(self):
        rows,scores=self.rows();picked=select_feedback(rows,scores,bad_ids={'bad'})
        self.assertEqual(len(picked),1);self.assertEqual(picked[0]['question_id'],'good')

    def test_truth_cannot_change_feedback_selection(self):
        rows,scores=self.rows();first=select_feedback(rows,scores)
        changed=[{**r,'correct':not r['correct'],'gold_answer':'unrelated'} for r in rows]
        second=select_feedback(changed,scores)
        self.assertEqual([r['candidate_id'] for r in first],[r['candidate_id'] for r in second])

    def test_no_truncated_or_over_context_training_rows(self):
        rows,scores=self.rows()
        for r in rows:r['truncated']=r['candidate_index']!=0;r['output_tokens']=4096
        self.assertEqual(select_feedback(rows,scores),[])

    def test_unknown_accepted_risk_is_bounded(self):
        rows=[{'question_id':'q','correct':x,'accepted':True} for x in [True,False,None,None]]
        m=risk_summary(rows)
        self.assertEqual(m['accepted_error_fraction'],.5)
        self.assertEqual(m['accepted_error_lower_bound_all'],.25)
        self.assertEqual(m['accepted_error_upper_bound_all'],.75)

    def test_frozen_length_rule_all_rounds(self):
        for prompt,output in [(100,3840),(342,3754),(1024,3072)]:
            settings=formal_generation_settings(prompt)
            self.assertEqual(settings['max_new_tokens'],output)
            self.assertLessEqual(prompt+settings['max_new_tokens'],4096)
        with self.assertRaises(ValueError):formal_generation_settings(1025)

    def test_excel_illegal_control_is_visible_not_dropped(self):
        self.assertEqual(excel_literal('form \x0crac{m}{n}'),'form \\u000crac{m}{n}')
        self.assertEqual(excel_literal('line1\nline2\tvalue'),'line1\nline2\tvalue')

if __name__=='__main__':unittest.main()
