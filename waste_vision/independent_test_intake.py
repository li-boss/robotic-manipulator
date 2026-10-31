"""Prepare and seal a new, human-reviewed independent test batch without adding it to training."""
from __future__ import annotations
import argparse,csv,hashlib,json,shutil
from pathlib import Path
from PIL import Image
from waste_vision.label_check import check

CLASSES=("block_blue","block_green","block_red","block_yellow")
SCENES={"single","multi","center","edge","angle","occluded","dim","bright","empty"}
FORMAL=Path('data/a_blocks/formal_v1_20261022')
CLASSES_PATH=Path('configs/a_confirmed_classes.txt')

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write_json(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def prepare(source:Path,workspace:Path):
    if workspace.exists():raise FileExistsError(workspace)
    files=sorted(source.glob('*.jpg'))
    if not files:raise ValueError(f'no JPG photos in {source}')
    old={sha(p) for split in ('train','val') for p in (FORMAL/'images'/split).glob('*.jpg')}
    seen=set();records=[]
    for p in files:
        with Image.open(p) as im:
            im.verify()
        with Image.open(p) as im:
            if im.size!=(640,480):raise ValueError(f'GUI requires raw 640x480 JPG: {p}')
        digest=sha(p)
        if digest in old or digest in seen:raise ValueError(f'duplicate old/train/val or intake photo: {p}')
        seen.add(digest);records.append({'filename':p.name,'review_status':'pending','image_sha256':digest})
    (workspace/'images'/'test').mkdir(parents=True)
    (workspace/'labels'/'test').mkdir(parents=True)
    for p in files:shutil.copy2(p,workspace/'images'/'test'/p.name)
    manifest={'purpose':'new independent test intake; NEVER auto-add to train or val',
              'source_dir':str((workspace/'images'/'test').resolve()),
              'reviewed_labels_dir':str((workspace/'labels'/'test').resolve()),
              'suggestions_dir':None,'allow_verified_empty':True,'classes':CLASSES,'images':records}
    write_json(workspace/'manifest.json',manifest)
    write_json(workspace/'verified_empty.json',[])
    with (workspace/'capture_info.csv').open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=('file','capture_group','scene_tags','notes'))
        writer.writeheader()
        for p in files:writer.writerow({'file':p.name,'capture_group':'','scene_tags':'','notes':''})
    return {'photos':len(files),'workspace':str(workspace.resolve()),'status':'pending human annotation'}

def seal(workspace:Path,formal:Path):
    output=workspace/'sealed_dataset'
    if output.exists():raise FileExistsError(output)
    intake=json.loads((workspace/'manifest.json').read_text(encoding='utf-8'))
    if tuple(intake['classes'])!=CLASSES or intake.get('allow_verified_empty') is not True:
        raise ValueError('not an independent-test annotation workspace')
    names={r['filename'] for r in intake['images']}
    pending = json.loads((workspace/'needs_recheck.json').read_text(encoding='utf-8')) if (workspace/'needs_recheck.json').exists() else []
    if not names or pending:
        raise ValueError('empty workspace or pending human recheck')
    images=workspace/'images'/'test';labels=workspace/'labels'/'test'
    checked=check(images,labels,CLASSES_PATH)
    if not checked['ready_for_training'] or checked['images']!=len(names):
        raise ValueError(f'test annotations incomplete/invalid: {checked}')
    verified=set(json.loads((workspace/'verified_empty.json').read_text(encoding='utf-8')))
    if not verified<=names:raise ValueError('verified_empty contains unknown filename')
    for record in intake['images']:
        if sha(images/record['filename'])!=record['image_sha256']:
            raise ValueError(f'intake image changed: {record["filename"]}')
    with (workspace/'capture_info.csv').open(encoding='utf-8-sig',newline='') as stream:
        infos=list(csv.DictReader(stream))
    if len(infos)!=len(names) or {r['file'] for r in infos}!=names:
        raise ValueError('capture_info.csv must have exactly one row per image')
    info_by_name={r['file']:r for r in infos}
    old_manifest=json.loads((formal/'split_manifest.json').read_text(encoding='utf-8'))
    if tuple(old_manifest['classes'])!=CLASSES:raise ValueError('formal class order changed')
    old_groups={r['scene_proxy'] for r in old_manifest['files'].values()}
    rows={'train':[],'val':[],'test':[]}
    for name,rec in old_manifest['files'].items():
        split=rec['split'];image=formal/'images'/split/name;label=formal/'labels'/split/(Path(name).stem+'.txt')
        if sha(image)!=rec['image_sha256'] or sha(label)!=rec['label_sha256']:
            raise ValueError(f'formal data changed: {name}')
        count=len(label.read_text(encoding='utf-8').splitlines())
        rows[split].append({'file':name,'capture_group':rec['scene_proxy'],
                            'image_sha256':rec['image_sha256'],'label_sha256':rec['label_sha256'],
                            'scene_tags':['single' if count==1 else 'multi'],'empty_verified':False})
    for name in sorted(names):
        info=info_by_name[name];group=info['capture_group'].strip()
        tags=[t.strip() for t in info['scene_tags'].split(';') if t.strip()]
        if not group or group in old_groups or not tags or any(t not in SCENES for t in tags):
            raise ValueError(f'fill valid capture_group and semicolon-separated scene_tags for {name}')
        image=images/name;label=labels/(Path(name).stem+'.txt')
        count=len(label.read_text(encoding='utf-8').splitlines());is_empty=count==0
        if is_empty != (name in verified) or is_empty != ('empty' in tags):
            raise ValueError(f'empty-scene human confirmation/tags disagree: {name}')
        if not is_empty and not (('single' in tags and count==1) or ('multi' in tags and count>=2)):
            raise ValueError(f'single/multi scene tag disagrees with label count: {name}')
        rows['test'].append({'file':name,'capture_group':group,'image_sha256':sha(image),
                             'label_sha256':sha(label),'scene_tags':tags,'empty_verified':is_empty})
    # Only after all checks pass do we create the sealed copy.
    for split in ('train','val','test'):
        (output/'images'/split).mkdir(parents=True)
        (output/'labels'/split).mkdir(parents=True)
        for rec in rows[split]:
            name=rec['file'];base=formal if split!='test' else workspace
            shutil.copy2(base/'images'/split/name,output/'images'/split/name)
            shutil.copy2(base/'labels'/split/(Path(name).stem+'.txt'),output/'labels'/split/(Path(name).stem+'.txt'))
    manifest={'purpose':'independent_test','sealed':True,'classes':CLASSES,'splits':rows,
              'warning':'test photos stay outside the baseline train/val split; if used to tune, acquire another final holdout'}
    write_json(output/'independent_manifest.json',manifest)
    yaml=f'path: "{output.resolve().as_posix()}"\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n'
    yaml+=''.join(f'  {i}: {name}\n' for i,name in enumerate(CLASSES))
    (output/'data.yaml').write_text(yaml,encoding='utf-8')
    write_json(output/'threshold_lock.json',{'selected_on':'val','confidence':0.25,'nms_iou':0.5,
        'selection_notes':'Baseline operating point used for old-collection validation and real HTTP check; fixed before this new test batch; not optimized on test.'})
    return {'test_images':len(rows['test']),'verified_empty':len(verified),'sealed_dataset':str(output.resolve())}

if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('--source',type=Path,required=True);a.add_argument('--workspace',type=Path,required=True)
    b=sub.add_parser('seal');b.add_argument('--workspace',type=Path,required=True);b.add_argument('--formal',type=Path,default=FORMAL)
    x=p.parse_args();result=prepare(x.source,x.workspace) if x.command=='prepare' else seal(x.workspace,x.formal)
    print(json.dumps(result,ensure_ascii=False,indent=2))
