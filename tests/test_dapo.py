import unittest
from pilot.dapo_common import near_family, available_output_tokens
from pilot.dapo_prepare import family_groups
from pilot.day2_protocol import label_response
from pilot.dapo_evaluate import gate_result,abstain_on_source_issue
from pilot.day2_evaluate import stats

class DAPOProtocolTests(unittest.TestCase):
    def test_native_context(self):
        self.assertEqual(available_output_tokens(1024),3072)
        self.assertEqual(available_output_tokens(4000),96)
        with self.assertRaises(ValueError):available_output_tokens(4096)

    def test_truncation_is_never_natural_error(self):
        for gold in ['34','35']:
            self.assertIsNone(label_response(r'Answer: \boxed{34}',True,gold,'gsm8k')['correct'])

    def test_numeric_wrong_is_exact(self):
        self.assertIs(label_response(r'\boxed{35}',False,'34','gsm8k')['correct'],False)
        self.assertIs(label_response(r'\boxed{34.0}',False,'34','gsm8k')['correct'],True)
        self.assertIsNone(label_response(r'\boxed{perhaps 34}',False,'34','gsm8k')['correct'])

    def test_reformulated_family(self):
        q='Let x and y be positive integers such that x squared plus y squared equals twenty five. Find the smallest possible value of x.'
        transformed=q+' The original answer has the form a plus b. Find a plus b.'
        self.assertTrue(near_family(q,transformed))
        groups,_=family_groups([q,transformed,'What is seven minus five?'],['a','b','c'])
        self.assertEqual(groups[0],groups[1]);self.assertNotEqual(groups[0],groups[2])

    def test_source_uuid_and_exact_duplicates_group(self):
        roots,_=family_groups(['What is x?','Different wording','what  is X?'],['same','same','other'])
        self.assertEqual(len(set(roots)),1)

    def test_truncation_blocks_pool_creation(self):
        gate={'minimum_verifiable_coverage':.9,'minimum_correct':40,'minimum_natural_errors':40,
              'minimum_error_questions':20,'minimum_correct_questions':20,'maximum_truncation_fraction':.02}
        metrics={'verifiable_coverage':.95,'correct':200,'natural_errors':180,'error_questions':60,
                 'correct_questions':70,'truncated_count':9,'total_candidates':400,
                 'V_scoring_status_counts':{'ok':400},'seconds_per_verified_natural_error':10}
        checks,passed=gate_result(metrics,gate,30)
        self.assertFalse(checks['length']);self.assertFalse(passed)
        metrics['truncated_count']=8
        self.assertTrue(gate_result(metrics,gate,30)[1])

    def test_cost_gate_includes_no_error_case(self):
        gate={'minimum_verifiable_coverage':.9,'minimum_correct':40,'minimum_natural_errors':40,
              'minimum_error_questions':20,'minimum_correct_questions':20,'maximum_truncation_fraction':.02}
        metrics={'verifiable_coverage':1,'correct':400,'natural_errors':0,'error_questions':0,
                 'correct_questions':100,'truncated_count':0,'total_candidates':400,
                 'V_scoring_status_counts':{'ok':400},'seconds_per_verified_natural_error':None}
        checks,passed=gate_result(metrics,gate,30)
        self.assertFalse(checks['lower_cost_per_error']);self.assertFalse(passed)

    def test_bad_source_retains_denominator_without_false_natural_errors(self):
        rows=[{'question_id':'bad-source','correct':False,'source_gold_correct':False,
               'verification_status':'verified','source_issue':{'category':'missing_plot'},
               'accepted':True} for _ in range(4)]
        for row in rows:abstain_on_source_issue(row)
        m=stats(rows)
        self.assertEqual((m['questions'],m['total_candidates'],m['unknown']),(1,4,4))
        self.assertEqual((m['natural_errors'],m['error_questions'],m['FP']),(0,0,0))
        self.assertTrue(all(r['source_gold_correct'] is False for r in rows))

    def test_source_issue_does_not_hide_truncation(self):
        row={'source_issue':{'category':'missing_target'},'correct':None,
             'verification_status':'truncated','source_gold_correct':None}
        self.assertEqual(abstain_on_source_issue(row)['verification_status'],'truncated')

if __name__=='__main__':unittest.main()
