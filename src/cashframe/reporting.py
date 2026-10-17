"""Immutable local report revisions built from a completed job snapshot."""
import hashlib
import json
from pathlib import Path
import tempfile

from .data import read_json, sha256_file, write_json


def read_annotations(local, job_id):
    records=[]
    for name in ('proposed.jsonl','confirmed.jsonl'):
        path=Path(local)/'annotations'/name
        if not path.exists():continue
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                if not line.strip():continue
                item=json.loads(line)
                if item.get('job_id')==job_id:records.append(item)
    return sorted(records,key=lambda item:item.get('saved_at',''))


def report_files(job, folder, rules, annotations):
    if job['status']!='done':raise ValueError('Analysis is not complete')
    from .reports import generate_report
    material={'result':job['result'],'review':job.get('review'), 'annotations':annotations,
              'source_sha256':job.get('source_sha256'),'rules':rules}
    revision=hashlib.sha256(json.dumps(material,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    reports=Path(folder)/'reports';reports.mkdir(exist_ok=True)
    destination=reports/revision
    if not destination.exists():
        with tempfile.TemporaryDirectory(dir=reports,prefix='building-') as temporary:
            temporary=Path(temporary)
            artifacts=generate_report(job,temporary,version_snapshot=(job.get('review') or {}).get('version_snapshot'),
                                      rules=rules,annotations=annotations)
            write_json(temporary/'integrity.json',{'report.pdf':sha256_file(artifacts.pdf_path),
                                                 'passport.json':sha256_file(artifacts.passport_path)})
            # Publish the entire revision only after both artifacts have been written.
            temporary.rename(destination)
    integrity=read_json(destination/'integrity.json')
    for name in ('report.pdf','passport.json'):
        if sha256_file(destination/name)!=integrity[name]:
            raise ValueError('Stored report integrity mismatch')
    return destination,integrity
