"""Evidence-bearing assistant initial review, never human-gold labels."""
import json
from .core import read_jsonl, write_json, write_jsonl
from .formal_common import sha, utc
from .explanation_probes import DEST, SUSPECT

# Reviewed independently of V0's acceptance and proxy score. Primary type is the first
# witnessed substantive flaw, not a claim to exhaust all flaws or repair the proof.
NOTES = [
('G0','87652cb8-e3f0-4eef-872b-08f1606efdc2','constraint_or_modeling',
 '12 + |W| - 4 = 20', '把“2门且白色4辆”当作红白交集；红白互斥，白色应为8辆。4门红色=12-(5-4)=11。', 'exact_reference_checked'),
('G0','880e1d8a-f7b8-4a43-aabe-bf33c20cbef5','constraint_or_modeling',
 'A = \\{1001, 7, 11, 13\\}', '所构造集合含7、11、13，直接违反最小元素1001的约束；只证明乘积为平方不能证明该集合可行。', 'final_reference_not_independently_certified'),
('G0','f6f97b6f-e2c4-46f8-9892-4dc50d2a8594','invalid_algebra_or_equivalence',
 '-2\\sqrt{2} = -2\\sqrt{2}', '代入sqrt(2)后左端为-2sqrt(2)，原方程右端却是8sqrt(2)，被改写为恒等式。该候选根不成立。', 'local_flaw_exact_checked'),
('G0','7952579d-de7a-455c-b334-86da1c0b6c6f','invalid_algebra_or_equivalence',
 'right side is also', '右侧49/z+50/y的通分分母是yz，并非固定2450；之后再次清分母也漏掉49、50因子。原式分子因式分解x(x-99)(99x-4901)，根和14702/99，答案14801。', 'exact_reference_checked'),
('G0','24d751b7-4429-4605-9754-d1451911fb26','constraint_or_modeling',
 'This is not possible since', '声称y^3不能为负，违背整数y可以为负的定义；末尾声称(4,-1)、(-4,1)为解也代入不成立。更重要的是源参考6有误，完整解对应x和为1，整题另列。', 'reference_suspect_exact_proof'),
('G0','0849d010-7bdf-4a5e-9e02-e9d158abc769','arithmetic_or_base_conversion',
 'a_3 = 11_2 + 2_2', '首步没有把11_2读成11_3再加2；正确a3=6，题面已给示例。精确按位进制递推至2013得23097。', 'exact_reference_checked'),
('G1','a029a0ed-fcc4-42e4-a4c2-147e90454a23','constraint_or_modeling',
 'z = 2000y - 1', '邻项关系为a_n=a_(n-1)*a_(n+1)-1，因此第四项z=(y+1)/2000；原文错误写成2000y-1。', 'local_flaw_exact_checked'),
('G1','07496eb4-77ce-42bc-835f-944494dec8d2','unsupported_generalization_or_cases',
 'must be a common multiple', '没有从切割拼接几何约束推出边数必须为p、q的公倍数；随后把p=2当正多边形，与自身p,q>2冲突。此审查不声称已完成该几何题全解。', 'final_reference_not_independently_certified'),
('G1','beffc63a-0f60-4bda-8094-ec6eca603add','unsupported_generalization_or_cases',
 'There are no pairs of positive integers', 'c=6时漏掉a=3,b=6：9+36=45；因此第三种分解是(3,6,6)，穷举正整数a≤b≤c≤9共3种。', 'exact_reference_checked'),
('G1','9e335405-0f66-4468-8735-d5689a55e4f6','constraint_or_modeling',
 'there are 10 choices for the second letter', '末位固定T，不能在中间再次用T；固定首尾并排除S后中间依次9、8、7种，2*9*8*7=1008。', 'exact_reference_checked'),
('G1','c0b0ebef-74c1-4242-b633-951258174827','arithmetic_or_base_conversion',
 '= 2916', '原文a6计算应为3*(768+192+48+12+3+1)=3072而非2916；后续容斥也错误。正确N=3*4^6-6*3^6+3*2^6=8106，余106。', 'exact_reference_checked'),
('G1','1ad950da-ca52-4596-bb29-9547df5040a7','invalid_algebra_or_equivalence',
 'For the exponents to be equal', '由同底幂相等直接推出指数相等时漏掉底数x=1；x=0涉及0^0。正实根为1和9/4，和13/4，对应17。', 'exact_reference_checked'),
('G2','39dff675-ec1a-41d8-9495-4c30a0a71fab','invalid_algebra_or_equivalence',
 'without the', '用w=z²后直接删去原式-16r^5*z一次项；r非零，所以这一步不是等价变换。无需先解完整四次方程即可定位错误。', 'local_flaw_exact_checked'),
('G2','24d751b7-4429-4605-9754-d1451911fb26','invalid_algebra_or_equivalence',
 '-3y^2 + 3y + 61', 'd=-1代入后左端应为-3y²+3y-1，原文把常数写成61，造出伪解(-1,0),(0,1)。参考6本身也错，完整整数解x=-5,6，和1。', 'reference_suspect_exact_proof'),
('G2','0849d010-7bdf-4a5e-9e02-e9d158abc769','arithmetic_or_base_conversion',
 'a_3 = 6 = 2', '虽算对初项，却断言n=3时6=2*(3)-2；6≠4，归纳基步即不成立。后续错把2k-2表示成22_(k-1)，实际22_(k-1)=2k。', 'exact_reference_checked'),
('G2','1b18887a-8661-48fd-b0b3-88303c02cd2c','constraint_or_modeling',
 'A(r) \\approx \\pi r^2', '把格点计数用圆面积近似后用于精确积分和整数系数识别。正确交换非负求和与积分得π*(sum_m exp(-m²))²=π*(2g-1)²，答案5。', 'analytic_reference_checked'),
('G2','1b3309c1-ed72-4632-98a5-f7bf4cf993d3','constraint_or_modeling',
 'such that their sum is 168', '题目允许正负号，原文却缩成四数全正相加为168；还把含重复元素的多重集合情形当四元素集合计数。此审查未独立认证761474这个精确计数。', 'final_reference_not_independently_certified'),
('G2','47df193e-95fa-41ef-8edc-b7e455a0b0ea','constraint_or_modeling',
 'base \\(s\\) and the height 1', '已知1是DE与BZ的垂距，其对应平行四边形底应为DE长度而非正方形边s。等面积推出AE=2s/3、DE=s*sqrt(13)/3，因此s²=13。', 'analytic_reference_checked')]


def main():
    blind = read_jsonl(DEST/'semantic_review_blind18.jsonl')
    byid = {r['candidate_id']: r for r in blind}
    result = []
    for g, uid, kind, quote, evidence, status in NOTES:
        cid = f'dapo:train:{uid}:formal:{g}:0'
        r = byid[cid]
        assert quote in r['text'], (cid, quote)
        result.append({**r, 'primary_error_type_initial': kind, 'literal_evidence': quote,
                       'explanation_initial': evidence, 'verification_scope': status,
                       'reference_suspect': r['question_id'] == SUSPECT,
                       'annotation_source': 'chat_assistant_initial_review_with_exact_computation_where_available',
                       'human_review_status': 'pending', 'human_error_type': None, 'human_comment': None,
                       'V0_score_seen_for_typing': False})
    assert len(result) == len(blind) == 18
    write_jsonl(DEST/'semantic_review_initial18.jsonl', result)
    write_json(DEST/'semantic_review_manifest.json', {'created_at': utc(), 'source_blind_sha256': sha(DEST/'semantic_review_blind18.jsonl'),
               'review_count': 18, 'sampling': '6 wrong candidate0 answers per G in C; deterministic hash; no V-based selection',
               'primary_type': 'First witnessed substantive flaw; not necessarily the only flaw or a complete correct solution',
               'gold_status': 'assistant initial annotations; human columns empty pending review',
               'population_error_type_prevalence_or_FPR_estimated': False,
               'exclusion_note': 'Two reviewed answers concern the same newly suspect source-reference question; excluded from main95-question population decomposition.'})
    from collections import Counter
    counts = {g: dict(Counter(r['primary_error_type_initial'] for r in result if r['G_round'] == g)) for g in ['G0', 'G1', 'G2']}
    write_json(DEST/'semantic_review_counts.json', counts)
    print(json.dumps({'reviewed': len(result), 'counts_for_sample_only': counts}, ensure_ascii=False))


if __name__ == '__main__': main()
