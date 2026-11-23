"""Verify and export completed response files into Git-sized ZIPs; no DB needed."""
import argparse,base64,gzip,hashlib,json,zipfile
from pathlib import Path

def verify(path):
    e=json.loads(gzip.decompress(path.read_bytes()))
    body=base64.b64decode(e['body_base64'],validate=True)
    if hashlib.sha256(body).hexdigest()!=e['body_sha256']:raise ValueError('Hash mismatch: '+str(path))
    from collector import validate
    if validate(body,e['task'])!=e['coverage']:raise ValueError('Coverage mismatch: '+str(path))
    return e

def export(source,dest):
    source=Path(source).resolve();dest=Path(dest).resolve()
    if dest==source or source in dest.parents:raise ValueError('Export folder must be outside collection folder')
    dest.mkdir(parents=True,exist_ok=True)
    if any(dest.iterdir()):raise ValueError('Use an empty export folder')
    files=list(sorted((source/'responses').rglob('*.json.gz')))
    if not files:raise ValueError('No completed response files')
    manifest={'format_version':1,'parts':[],'responses':len(files),'note':'Partial/unavailable preserved; inspect status.json'}
    groups=[];group=[];size=0
    for p in files:
        verify(p)
        if p.stat().st_size>35*1024**2:raise ValueError('Single response too large')
        if group and size+p.stat().st_size>35*1024**2:groups.append(group);group=[];size=0
        group.append(p);size+=p.stat().st_size
    if group:groups.append(group)
    for i,group in enumerate(groups,1):
        name=f'meteo_part_{i:04d}.zip';out=dest/name
        with zipfile.ZipFile(out,'w',zipfile.ZIP_STORED) as z:
            for p in group:z.write(p,p.relative_to(source).as_posix())
            if i==1:
                for n in ['locations.json','status.json']:
                    if (source/n).exists():z.write(source/n,n)
        manifest['parts'].append({'file':name,'bytes':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest()})
    (dest/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--dest',required=True)
    a=p.parse_args();export(a.source,a.dest)
