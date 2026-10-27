"""Second-day answer protocol. Does not modify day1 semantics or artifacts."""
import contextlib
import functools
import re
import signal
from fractions import Fraction
from .core import ROOT, normalize_number

VERSION='boxed_v2_20261020'

@contextlib.contextmanager
def bounded(seconds=5):
    def stop(signum,frame):raise TimeoutError('symbolic parser/equivalence timeout')
    old=signal.signal(signal.SIGALRM,stop);old_timer=signal.setitimer(signal.ITIMER_REAL,seconds)
    try:yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,*old_timer);signal.signal(signal.SIGALRM,old)

def boxes(text):
    out=[]
    for match in re.finditer(r'\\(?:boxed|fbox)\s*\{',text):
        begin=match.end();end=begin;depth=1
        while end<len(text) and depth:
            if text[end]=='{':depth+=1
            elif text[end]=='}':depth-=1
            end+=1
        if depth:raise ValueError('unbalanced_box')
        out.append({'value':text[begin:end-1],'start':match.start(),'end':end})
    return out

UNITS=r'(?:dollars?|euros?|cents?|pounds?|kilograms?|kg|grams?|cm|mm|meters?|metres?|feet|foot|inches|inch|miles?|hours?|minutes?|seconds?|days?|weeks?|years?|degrees?|percent|students?|people|pieces|marbles|pages|cats|balls|points|sheets|pots|circles)'

def clean_value(value,task):
    v=value.strip().replace('−','-').replace('–','-')
    v=v.replace(r'\left','').replace(r'\right','').replace(r'\dfrac',r'\frac').replace(r'\tfrac',r'\frac')
    v=v.replace(r'\,','').replace(r'\!','').replace(r'\;','').replace(r'\:','').replace(r'\ ','')
    v=v.replace(r'\$','').strip('$ ')
    v=re.sub(r'\\(?:text|mathrm|mbox)\{\s*('+UNITS+r')\s*\}', '',v,flags=re.I)
    v=re.sub(r'\^\s*\{?\\circ\}?','',v)
    # Percent answers retain the requested percentage number, not a conversion to fraction-of-one.
    v=re.sub(r'(?:\\%|%)\s*$','',v)
    return v.strip()

def numeric(value,task='gsm8k'):
    v=clean_value(value,task)
    v=re.sub(r'\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}',r'\1/\2',v)
    return normalize_number(v)

def split_top(text):
    depth=0;parts=[];begin=0
    for i,char in enumerate(text):
        if char in '({[':depth+=1
        elif char in ')}]':depth-=1
        elif char==',' and depth==0:parts.append(text[begin:i]);begin=i+1
    parts.append(text[begin:]);return parts

ALLOWED={'frac','sqrt','pi','infty','cdot','times','div','sin','cos','tan','log','ln','exp','lvert','rvert','vert','lfloor','rfloor','lceil','rceil','arcsin','arccos','arctan'}

@functools.lru_cache(maxsize=4096)
def parse_math(value):
    import sympy as s
    v=clean_value(value,'math')
    if len(v)>350:raise ValueError('expression_too_long')
    try:return ('scalar',s.Rational(numeric(v,'math')))
    except (ValueError,ZeroDivisionError):pass
    # Thousand grouping only when entire expression is a numeric literal; otherwise commas are structural.
    if v.startswith(r'\{') and v.endswith(r'\}'):
        elements=[]
        for part in split_top(v[2:-2]):
            parsed=parse_math(part)
            if not any(compare_parsed(parsed,previous) is True for previous in elements):elements.append(parsed)
        return ('set',tuple(elements))
    if len(v)>1 and v[0]=='(' and v[-1]==')' and len(split_top(v[1:-1]))>1:
        return ('tuple',tuple(parse_math(x) for x in split_top(v[1:-1])))
    if len(split_top(v))>1:return ('list',tuple(parse_math(x) for x in split_top(v)))
    assignment=re.fullmatch(r'([a-zA-Z])\s*=\s*(.+)',v)
    if assignment:v=assignment[2]
    commands=re.findall(r'\\([A-Za-z]+)',v)
    if any(c not in ALLOWED for c in commands):raise ValueError('unsupported_latex_command')
    if not re.fullmatch(r'[a-zA-Z0-9\\{}()\[\]+*/^_=.,!\s|\-]+',v):raise ValueError('unsupported_characters')
    plain=re.sub(r'\\[A-Za-z]+','',v)
    if re.search(r'[A-Za-z]{2,}',plain) or '__' in v or '=' in v:raise ValueError('unsupported_identifier_or_equation')
    if re.search(r'\d{7,}',v):raise ValueError('excessive_numeric_literal')
    if re.search(r'\^\s*\{?\s*\d{3,}',v):raise ValueError('excessive_exponent')
    from sympy.parsing.latex import parse_latex
    pi_symbol=None
    if r'\pi' in v:
        used=set(re.sub(r'\\[A-Za-z]+','',v));pi_symbol=next((letter for letter in 'PQRSTUVWXYZABCDEFGHIJKLMNO' if letter not in used),None)
        if pi_symbol is None:raise ValueError('no_safe_pi_placeholder')
        v=v.replace(r'\pi',pi_symbol)
    with bounded():expr=parse_latex(v,backend='lark')
    if pi_symbol and isinstance(expr,s.Expr):expr=expr.subs(s.Symbol(pi_symbol),s.pi)
    # MATH final-answer convention: standalone i denotes the imaginary unit.
    if isinstance(expr,s.Expr):expr=expr.subs(s.Symbol('i'),s.I)
    if not isinstance(expr,s.Expr):raise ValueError('ambiguous_or_nonscalar_parse')
    if expr.has(s.zoo,s.nan):raise ValueError('undefined_expression')
    if s.count_ops(expr)>120:raise ValueError('excessive_expression_complexity')
    return ('scalar',expr)

def compare_parsed(a,b):
    import sympy as s
    if a[0]!=b[0]:return None
    if a[0] in ['tuple','list']:
        if len(a[1])!=len(b[1]):return False
        comparisons=[compare_parsed(x,y) for x,y in zip(a[1],b[1])]
        return False if False in comparisons else None if None in comparisons else True
    if a[0]=='set':
        if len(a[1])!=len(b[1]):return False
        unused=list(b[1])
        for value in a[1]:
            matches=[compare_parsed(value,other) for other in unused]
            if True in matches:unused.pop(matches.index(True))
            elif None in matches:return None
            else:return False
        return True
    with bounded():
        delta=s.simplify(a[1]-b[1])
        if delta==0:return True
        if delta.free_symbols:
            try:
                if s.cancel(delta)!=0 and s.cancel(delta).is_rational_function(*sorted(delta.free_symbols,key=str)):return False
            except Exception:pass
            return None
        zero=delta.is_zero
        return True if zero is True else False if zero is False else None

def compare(value,gold,task):
    try:
        if task=='gsm8k':return numeric(value)==numeric(gold), 'exact_rational'
        a=parse_math(value);b=parse_math(gold)
        result=compare_parsed(a,b)
        return result,'symbolic_exact' if result is not None else 'equivalence_undecidable'
    except (Exception,) as e:return None, type(e).__name__+':'+str(e).splitlines()[0][:140]

def extract(text,truncated=False,task='gsm8k',reference=False):
    if truncated:return {'status':'truncated','value':None,'format':None}
    try:found=boxes(text)
    except ValueError as e:return {'status':str(e),'value':None,'format':None}
    hashes=list(re.finditer(r'(?m)^\s*####\s*([^\n]+)\s*$',text))
    # Markdown reasoning headings are not explicit final-answer assertions.
    hashes=[h for h in hashes if not re.match(r'(?i)^(?:step|case|solution|analysis|reasoning)\b',h[1].strip()) and not re.fullmatch(r'(?i)(?:final\s+answer|answer|conclusion)\s*:?',h[1].strip())]
    if not found:
        if len(hashes)==1:return {'status':'extracted','value':hashes[0][1].strip(),'format':'hash'}
        return {'status':'missing_explicit_answer','value':None,'format':None}
    if reference:return {'status':'extracted','value':found[-1]['value'],'format':'boxed_reference_last'}
    values=[f['value'] for f in found]
    if len(values)>1:
        comparisons=[compare(v,values[-1],task)[0] for v in values[:-1]]
        if not all(c is True for c in comparisons):return {'status':'ambiguous_boxes','value':None,'format':'boxed_multiple'}
    if hashes:
        if not all(compare(h[1],values[-1],task)[0] is True for h in hashes):return {'status':'conflicting_explicit_answers','value':None,'format':'mixed'}
    # Descriptive suffixes are allowed, but a later equation, box, code block or numerical math expression is unresolved.
    tail=text[found[-1]['end']:]
    tail=re.sub(r'\\[\]\)]|\$', '',tail).strip()
    # Hyphenated units such as "5-ounce" describe an object, not a replacement answer.
    tail_without_units=re.sub(r'\d+(?:\.\d+)?-(?:ounce|pound|inch|foot|year|day|hour)s?','UNIT',tail,flags=re.I)
    if re.search(r'\b(?:but|however|instead|alternatively|actually|correction|or)\b|answer\s+(?:is|should)',tail_without_units,re.I):
        return {'status':'conflicting_trailing_statement','value':None,'format':'boxed'}
    if any(x in tail_without_units for x in ['=','```',r'\boxed',r'\frac']) or re.search(r'\d',tail_without_units):
        return {'status':'math_after_final_answer','value':None,'format':'boxed'}
    return {'status':'extracted','value':values[-1],'format':'boxed_single' if len(values)==1 else 'boxed_repeated_equivalent','trailing_description':bool(tail.strip(' .。!！'))}

def label_response(text,truncated,gold,task):
    e=extract(text,truncated,task)
    if e['value'] is None:return {**e,'correct':None,'comparison':'not_extracted'}
    correct,method=compare(e['value'],gold,task)
    return {**e,'correct':correct,'comparison':method,'status':'verified' if correct is not None else 'equivalence_unresolved'}
