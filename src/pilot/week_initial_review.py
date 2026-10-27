"""Evidence-bearing assistant review of20 frozen C answers, before new V reasons."""
import json
import math
import re

from .core import read_jsonl,write_json,write_jsonl
from .formal_common import sha,utc
from .week_final_common import RUN


def exact_checks():
    cars=[(r2,r4,w2,w4) for r2 in range(21) for r4 in range(21) for w2 in [4] for w4 in range(21)
          if r2+r4+w2+w4==20 and r2+r4==12 and r4+w4==15]
    triples=[(a,b,c) for a in range(1,10) for b in range(a,10) for c in range(b,10) if a*a+b*b+c*c==81]
    palindromes=[1010*a+101*b for a in range(1,10) for b in range(10)]
    def prime(n):return n>=2 and all(n%d for d in range(2,math.isqrt(n)+1))
    primes_lt50=[p for p in range(2,50) if prime(p)]
    first=next(n for n in range(1,3128) if not prime(n) and math.isqrt(n)**2!=n and all(n%p for p in primes_lt50))
    mul=lambda a,b:(a[0]*b[0]+3*a[1]*b[1],a[0]*b[1]+a[1]*b[0])
    cubes=[mul(mul(x,x),x) for x in [(1,1),(1,-1)]]
    assert cars==[(1,11,4,4)] and triples==[(1,4,8),(3,6,6),(4,4,7)]
    assert min(palindromes)==1010 and max(palindromes)==9999 and max(palindromes)-min(palindromes)==8989
    assert first==53*59==3127 and prime(53) and prime(59)
    assert cubes==[(10,6),(10,-6)] and 10+6*6*3==118
    return {'cars':{'solutions_r2_r4_w2_w4':cars,'answer':11},'three_squares':{'ordered_non_decreasing_roots':triples,'answer':3},
        'semi_palindrome':{'min':1010,'max':9999,'answer':8989},'smallest_composite_nonsquare':{'first_valid':first,'answer':3127,'53_fourth_power':53**4},
        'real_cube_roots':{'root_cubes_as_a_plus_b_sqrt3':cubes,'product_of_cube_roots':-2,'q':108,'p':10,'answer':118}}


def paragraph_spans(text):
    spans=[];start=0
    for separator in list(re.finditer(r'\n[ \t]*\n',text))+[None]:
        end=separator.start() if separator else len(text);chunk=text[start:end]
        left=len(chunk)-len(chunk.lstrip());right=len(chunk.rstrip())
        if right>left:spans.append((start+left,start+right))
        start=separator.end() if separator else len(text)
    return spans


def review_one(r):
    g=r['G_round'];q=r['question_id'];text=r['text'];quote=None;kind='none_seen';status='correct_initial';note=''
    if q.endswith('87652cb8-e3f0-4eef-872b-08f1606efdc2'):
        if g=='G1':note='红白互斥，白车20−12=8，四门白8−4=4，四门红15−4=11；逐段推导成立。'
        else:
            status='incorrect_initial';kind='constraint_or_set_modeling'
            if g=='G0':quote=r'12 + |W| - 4 = 20';note='把两门白车4辆当成红白交集。红白互斥，交集应为0；正确四门红车11。'
            elif g=='G2':quote=r'|R \cap W| = 4';note='明确把红白交集设为4，违反颜色互斥，后续白车12及答案7均不成立。'
            else:quote=r'|D_4 \cap R \cap W| = 4';note='此前已得红白交集0，却把四门红白三重交集设为4，随后声称15=11一致。door写成digit另记术语问题。'
    elif q.endswith('beffc63a-0f60-4bda-8094-ec6eca603add'):
        status='incorrect_initial';kind='missing_case'
        quote='There are no pairs'
        note='首次出现在c=6段的无解断言错误：(a,b)=(3,6)给9+36=45，且a≤b≤c。精确枚举三组(1,4,8),(3,6,6),(4,4,7)，应为3。'
    elif q.endswith('b62d6a00-8422-4836-aa09-d6db0186d28d'):
        note='abab=1010a+101b，a=1..9、b=0..9；最大9999、最小1010，差8989。四阶段该候选逐段均未见实质错误。'
    elif q.endswith('0c73160f-95ca-4cc6-a4c3-dd641342f0b5'):
        if g=='G0':
            status='incorrect_initial';kind='arithmetic_and_minimality_gap';quote=r'53^4 = 885849'
            note='该未撤回算术断言错误：53^4=7890481。此前53³为next smallest product属于被随后转向不同素数的探索；另最后最小性分类不完整。最终3127可独立证明正确，不把结果标签改错。'
        elif g=='G1':
            status='unknown_completeness';kind='minimality_justification_incomplete'
            note='53、59及乘积3127与非平方核对成立；书面最小性略去重复素因子与三因子分支。可补证53²为平方、≥三因子至少53³>3127；未见明确局部假命题，完整性待人工复核。'
        elif g=='G2':
            status='incorrect_initial';kind='square_constraint_ignored';quote=r'is \(53 \times 53 = 2809\)'
            note='最后把53²=2809认作不是平方的最小数，直接违反非平方条件；早期考虑53后被prime检查撤回，不将探索候选机械当首错。'
        else:
            status='incorrect_initial';kind='prime_constraint_ignored';quote=r'Therefore, the smallest positive integer that is neither prime nor square and has no prime factor less than 50 is \(53\).'
            note='排除了2809为平方后最终返回53；53本身为质数，违反非质数条件。应为53×59=3127。'
    elif q.endswith('96c3a3c6-22fd-4d06-9d7d-3af786201960'):
        if g=='G0':
            status='incorrect_initial';kind='real_domain_error';quote='are not real numbers'
            note='10±6√3都是实数，一正一负不妨碍实立方根。第一组也满足原方程，错误排除一根；最终较小根和118仍正确。'
        else:note='由实立方根和2及立方和20得乘积−2，根为1±√3；立方得10±6√3，较小根10−√108，p+q=118。所检查各步成立。'
    else:raise ValueError(q)
    if quote is not None:assert quote in text,(r['candidate_id'],quote)
    pos=text.index(quote) if quote else None
    return {'candidate_id':r['candidate_id'],'question_id':q,'G_round':g,'process_status_initial':status,
        'primary_error_type_initial':kind,'first_witnessed_error_quote':quote,'first_witnessed_error_start':pos,
        'first_witnessed_error_end':pos+len(quote) if quote else None,'evidence_initial':note,
        'independent_final_answer_correct':r['correct'],'annotation_source':'chat_assistant_initial_review_with_exact_checks',
        'new_V_rationales_seen_before_G_review':False,'human_review_status':'pending','human_label':None,'human_comment':None}


def main():
    directory=RUN/'initial_review';directory.mkdir(exist_ok=True)
    proof=exact_checks();rows=read_jsonl(RUN/'reason_inputs_C20.jsonl');reviews=[review_one(r) for r in rows];steps=[]
    for r,review in zip(rows,reviews):
        spans=paragraph_spans(r['text']);first=review['first_witnessed_error_start']
        for i,(a,b) in enumerate(spans):
            status='checked_no_local_error_observed'
            if first is not None:
                status='first_witnessed_error' if a<=first<b else 'not_reviewed_after_first_witnessed_error' if a>first else status
            steps.append({'candidate_id':r['candidate_id'],'question_id':r['question_id'],'G_round':r['G_round'],'step_id':i+1,
                'character_start':a,'character_end':b,'step_text':r['text'][a:b],'step_status_initial':status,
                'evidence_initial':review['evidence_initial'] if status=='first_witnessed_error' else None,
                'human_review_status':'pending','human_label':None,'human_comment':None})
        if first is not None:assert sum(s['candidate_id']==r['candidate_id'] and s['step_status_initial']=='first_witnessed_error' for s in steps)==1
    write_json(directory/'exact_math_checks.json',proof);write_jsonl(directory/'G_response_initial20.jsonl',reviews);write_jsonl(directory/'G_steps_initial.jsonl',steps)
    write_json(directory/'annotation_protocol.json',{'at':utc(),'raw_source_sha256':sha(RUN/'reason_inputs_C20.jsonl'),
        'answers':20,'paragraph_steps':len(steps),'unit':'original paragraph spans, exact character offsets; no rewriting',
        'first_error':'first witnessed substantive unretracted flaw, not proof all earlier reasoning flawless; abandoned exploration separated',
        'unknown_completeness':'no explicit false statement but proof coverage unclear, human review pending',
        'after_first_error':'not automatically incorrect; not reviewed after first witnessed error',
        'human_gold':False,'used_for_training':False,'new_V_rationales_seen':False})
    print('Initial G review:',len(reviews),'answers;',len(steps),'original paragraph steps')


if __name__=='__main__':main()
