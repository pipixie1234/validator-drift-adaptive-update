"""Literal evidence from actual C rationale generations; assistant, not human gold."""
import json

from .core import ROOT,read_jsonl,write_json,write_jsonl
from .formal_common import sha,utc
from .week_final_common import RUN


# Indices follow the frozen20-answer cohort, not model outcome ranking.
V0_REVIEWS=[
 ('valid','the error is in assuming |R ∩ W| = 4.','V0先误称代数移项错，随即明确撤回并定位把两门白车当红白交集的建模错误；重算白8、四门白4、四门红11，足以否定7。',True,True,True),
 ('valid','(3,6,6): 9 + 36 + 36 = 81','补出原回答漏掉的(3,6,6)，与原有两组不同，至少3组即足以否定最终2；试探的无效组合均明确排除，不当成已接受的错误。',True,True,True),
 ('valid','The candidate\'s reasoning and arithmetic are correct.','开头incorrectly与末尾冲突，但后文明示1010有效并明确确认候选正确，按自我纠正规则不机械保留开场试探为最终主张。完整检查abab取值范围、两端点及差8989。',True,True,True),
 ('valid','Any smaller composite number with only primes ≥ 50 would either be a square','独立检查53、59及3127非平方。两个素因子排除53²；三个以上因子至少53³>3127，故原理由对更小数的排除可由所列最小素数界直接补全。不要求接受理由证明G每句都对。',True,True,True),
 ('invalid','All steps are mathematically valid','理由算出两实根并核对立方根之和，确实支持最终118；但它又断言候选所有步骤有效，和G0明确把实数10±6√3说成非实数并排除一根冲突。按事实/对应性标准无效，不能因最终判对而忽略这个错误概括。',True,False,True),
 ('valid','15 - 4 = 11 red cars are 4-door','独立重算8辆白车、4辆四门白、11辆四门红，对应原G1正确推导且支持接受11。',True,True,True),
 ('valid','3² + 6² + 6² = 9 + 36 + 36 = 81','发现缺失的(3,6,6)，该反例即可说明最终计数2错误；其它尝试明示无效。',True,True,True),
 ('valid','The difference is $ 9999 - 1010 = 8989 $','开头说incorrectly后，明示1010正确并在结尾确认候选推理算术正确，按最后明确纠正评估。端点范围与差足以支持答案。',True,True,True),
 ('valid','Since the number must be *neither prime nor square*, we must avoid squares.','先列2809并明确因平方排除，再确认3127满足限制；最后更小composite语句需结合此前非平方限定理解，措辞不严谨留给人工重点复核。核心最小乘积界正确。',True,True,True),
 ('invalid','The values $p = 10$, $q = 108$ are integers','开头不等式主张随后由Thus等式明确纠正；实际只核对6√3=√108及10+108，未检查10−6√3为何是原方程的较小根。局部格式转换不能充分支持整体最终答案接受，标支持不足，非把已纠正等式算错。',True,True,False),
 ('valid','4-door red = 15 - 4 = 11','重新正确建模并核对红白交集不能为4，得答案11，足以否定候选7。',True,True,True),
 ('valid','(3,6,6) is valid and not listed.','给出9+36+36=81的缺失合法组，足以否定最终2，无效试探均明确排除。',True,True,True),
 ('valid','The candidate\'s reasoning and arithmetic are correct.','与同类G1理由相同，首句批评在后文明确纠正；最终论证完整支持1010、9999与8989。',True,True,True),
 ('valid','Since 53 × 53 is a square, it is invalid.','候选最终2809=53²违反非平方，单此证据即可支持拒绝；独立正确数3127也已算出。',True,True,True),
 ('invalid','The algebra is correct, and the expression is properly simplified.','等式开场错误随后纠正；余下只检验根式转换与p+q，未核对原方程或较小根选择。泛称代数正确不足以补足这些关键检查，理由支持不足。',True,True,False),
 ('valid','5 = (12 − x) + 4 − 0 → 5 = 16 − x → x = 11','正确识别2门红白交集为0，修正方程并得11，直接对应候选把交集设4后得7的错误，足以支持拒绝。',True,True,True),
 ('valid','(3,6,6): 3² + 6² + 6² = 9 + 36 + 36 = 81','补出被漏合法组；至少三种即可否定最终2，不必为拒绝2穷尽所有更大计数。',True,True,True),
 ('valid','The smallest possible value is when $ a = 1 $, $ b = 0 $','精确检查四位abab取值域、最小1010、最大9999和差8989，支持接受。',True,True,True),
 ('valid','53 is a prime number','最终53本身为质数，直接违反非质数限制，足以拒绝。后段更小composite措辞省略非平方限定，与前文明确排除2809有张力，人工应核查措辞；不影响有效核心反证。',True,True,True),
 ('valid','The values of $x$ are correctly computed as $(1 \\pm \\sqrt{3})^3$','理由核对变量替换、二次方程所得1±√3及其立方和较小根，和原G3推导对应；精确复算得10±6√3以及118。',True,True,True),
]


def main():
    sources=read_jsonl(RUN/'reason_inputs_C20.jsonl');annotations=[]
    for v in ['V0','Vrand','Vtarget']:
        generations=read_jsonl(RUN/'rationales'/(v+'.jsonl'))
        idx={r['candidate_id']:r for r in generations};assert len(idx)==20
        if v=='V0':reviews=V0_REVIEWS
        else:
            # All40 actual outputs were inspected by the chat assistant:
            # each is literally VERDICT: B, with no substantive reason.
            assert all(s['rationale_full_text']=='VERDICT: B' and s['rationale_body']=='' and not s['truncated'] for s in generations)
            reviews=[('not_provided','VERDICT: B','已逐条查看真实输出：只有VERDICT: B，未提供任何计算、事实核对或拒绝依据。理由记未提供；判定正确性另按独立结果标签计算，不把无理由记作数学错误。',None,None,None)]*20
        assert len(reviews)==20
        for source,review in zip(sources,reviews):
            s=idx[source['candidate_id']];validity,quote,note,math_ok,corresponds,support=review
            assert quote in s['rationale_full_text'],(v,source['candidate_id'],quote)
            annotations.append({'candidate_id':source['candidate_id'],'V':v,'question_id':source['question_id'],
                'rationale_validity_initial':validity,'evidence_quote':quote,'evidence_initial':note,
                'mathematical_check_sound_initial':math_ok,'G_text_correspondence_initial':corresponds,
                'final_answer_verdict_supported_initial':support,'annotation_source':'chat_assistant_initial_review_with_exact_checks',
                'rationale_source_sha256':sha(RUN/'rationales'/(v+'.jsonl')),
                'annotation_at':utc(),'human_review_status':'pending','human_label':None,'human_comment':None})
    write_jsonl(RUN/'initial_review/rationale_reviews_initial60.jsonl',annotations)
    write_json(RUN/'initial_review/rationale_review_protocol.json',{
        'at':utc(),'source':'actual generated V text, reviewed by chat assistant; not human gold',
        'cases':'fixed20 C answers; same answers for all V; selection not prevalence sample',
        'valid':'sound facts/math plus text correspondence plus sufficient final-answer verdict support',
        'invalid':'false unretracted facts/math or text mismatch or insufficient final-answer support',
        'unverifiable':'ambiguous/incomplete evidence prevents defensible label; retain unknown',
        'not_provided':'no substantive reason, including standalone A/B',
        'self_correction':'explicit later correction governs; abandoned trials not automatically treated as final false claims',
        'imprecise_scope':'read such/composite in explicit preceding neither-prime-nor-square context; record ambiguity for human review',
        'final_answer_vs_process':'rejecting correct answer for process flaw alone invalid; accepting answer need not prove every G step',
        'generated_judgment_vs_original_score':'new public-rationale protocol differs from original A/B scoring; do not identify hidden causes',
        'all_labels_initial_only':True,'used_for_training':False,'reviewed_records':len(annotations),
        'code_sha256':sha(ROOT/'src/pilot/week_reason_initial_review.py')})
    print('Assistant rationale reviews:',len(annotations),flush=True)


if __name__=='__main__':main()
