import concurrent.futures,json,re,time,urllib.request,urllib.parse
from pathlib import Path
filename='pyarrow-26.0.0-cp311-cp311-manylinux_2_28_x86_64.whl'
sources={
 'aliyun':'https://mirrors.aliyun.com/pypi/simple/pyarrow/',
 'tuna':'https://pypi.tuna.tsinghua.edu.cn/simple/pyarrow/',
 'nju':'https://mirrors.nju.edu.cn/pypi/web/simple/pyarrow/',
 'sjtu':'https://mirror.sjtu.edu.cn/pypi/web/simple/pyarrow/',
 'huawei':'https://repo.huaweicloud.com/repository/pypi/simple/pyarrow/',
 'tencent':'https://mirrors.cloud.tencent.com/pypi/simple/pyarrow/',
}
def probe(item):
 name,url=item;begin=time.perf_counter()
 try:
  with urllib.request.urlopen(url,timeout=12) as f:page=f.read().decode()
  links=re.findall(r'href=[\"\']([^\"\']+)',page)
  link=next(link for link in links if filename in link)
  target=urllib.parse.urljoin(url,link).split('#')[0]
  req=urllib.request.Request(target,headers={'Range':'bytes=0-2097151'})
  start=time.perf_counter();size=0
  with urllib.request.urlopen(req,timeout=12) as f:
   first=time.perf_counter();status=f.status
   while size<2097152:
    chunk=f.read(min(131072,2097152-size))
    if not chunk:break
    size+=len(chunk)
  elapsed=time.perf_counter()-start
  return {'source':name,'index':url,'artifact_url':target,'http_status':status,'bytes':size,'ttfb_seconds':round(first-start,3),'transfer_seconds':round(elapsed,3),'mib_per_second':round(size/1048576/elapsed,3),'whole_probe_seconds':round(time.perf_counter()-begin,3)}
 except Exception as e:return {'source':name,'index':url,'error':repr(e),'whole_probe_seconds':round(time.perf_counter()-begin,3)}
results=[]
with concurrent.futures.ThreadPoolExecutor(6) as pool:
 futures=[pool.submit(probe,item) for item in sources.items()]
 for f in concurrent.futures.as_completed(futures):
  row=f.result();results.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
path=Path(__file__).resolve().parents[1]/'reports/network_mirror_comparison.json'
path.write_text(json.dumps({'artifact':filename,'parallel_probes':True,'range_limit_bytes':2097152,'results':results},ensure_ascii=False,indent=2))
