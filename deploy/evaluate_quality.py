"""Evaluate the final-review guard on private JSONL drafts; no tools or QQ sends.

Input rows: id, question, draft, evidence (list of tool/result dicts), optional context.
Output contains sensitive conversation data: keep outside the public repository.
This is a review replay, not a claim to measure full-agent accuracy.
"""
import argparse
import json
import os
from pathlib import Path
import time
from qqhermes.quality import request_review,review_reasons,task_text,accepted_revision


def evaluate(source,destination):
    target=Path(destination)
    with target.open('x') as output:
        target.chmod(0o600)
        for line in Path(source).read_text().splitlines():
            row=json.loads(line);start=time.monotonic()
            snapshot={'question':task_text(row['question']),'context':row.get('context',''),'previous':row.get('previous',[]),
                      'evidence':row.get('evidence',[]),'model':'deepseek-flash'}
            try:
                result=request_review(snapshot,row['draft'])
                value={'id':row['id'],'original':row['draft'],'review':result,'accepted':accepted_revision(result,row['draft'],snapshot) is not None,'elapsed_seconds':round(time.monotonic()-start,2)}
            except Exception as exc:value={'id':row['id'],'error':type(exc).__name__}
            output.write(json.dumps(value,ensure_ascii=False)+'\n');output.flush()
            print(json.dumps({k:v for k,v in value.items() if k not in ('original','review')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('destination')
    args=p.parse_args();evaluate(args.source,args.destination)
