"""Standalone diagnostic figure, using all raw lengths and labelled V counts."""
import json
import numpy as np
from .core import read_jsonl
from .dapo_common import RUN,REPORT

def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    m=json.loads((REPORT/'dapo100_metrics.json').read_text());g=read_jsonl(RUN/'G0_dapo100.jsonl')
    fig,axes=plt.subplots(1,2,figsize=(10,4))
    matrix=np.array([[m['TP'],m['FN']],[m['FP'],m['TN']]])
    axes[0].imshow(matrix,cmap='Blues');axes[0].set_xticks([0,1],['Accept','Reject'])
    axes[0].set_yticks([0,1],['Correct','Wrong']);axes[0].set_title(f"V0 on DAPO ({m['verified']} verified)")
    for (y,x),n in np.ndenumerate(matrix):axes[0].text(x,y,str(n),ha='center',va='center')
    bins=np.linspace(0,3200,33)
    axes[1].hist([[r['output_tokens'] for r in g if not r['truncated']],
                  [r['output_tokens'] for r in g if r['truncated']]],bins=bins,stacked=True,label=['EOS complete','Budget truncated'])
    axes[1].axvline(1536,color='gray',linestyle='--',label='Previous cap1536')
    axes[1].axvline(3072,color='red',linestyle=':',label='Current cap3072')
    axes[1].set_xlabel('Generated tokens');axes[1].set_ylabel('Candidates');axes[1].set_title('All400 responses; truncations stay unknown')
    axes[1].legend(fontsize=8);fig.tight_layout();fig.savefig(REPORT/'dapo_diagnostics.png',dpi=160);plt.close(fig)

if __name__=='__main__':main()
