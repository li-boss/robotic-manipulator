"""Create a conservative split-planning sheet without inventing capture metadata."""
import csv
import json
from pathlib import Path


def sequence_window(num: int) -> str:
    for start, end in ((193,251),(263,315),(316,369),(370,424),(425,440)):
        if start <= num <= end:
            return f"{start}-{end}"
    return "other"


def run(source: Path, audit: Path, output: Path) -> dict:
    images=sorted(source.glob("*.jpg"),key=lambda p:int(p.stem))
    if len(images)!=206:
        raise ValueError(f"expected 206 original images, got {len(images)}")
    data=json.loads(audit.read_text(encoding="utf-8"))
    if data["image_count"]!=206 or data["exact_duplicate_groups"]:
        raise ValueError("source audit changed; inspect before grouping")
    parent={p.name:p.name for p in images}
    def find(x):
        while parent[x]!=x:
            parent[x]=parent[parent[x]]
            x=parent[x]
        return x
    def union(a,b):
        a,b=find(a),find(b)
        if a!=b: parent[b]=a
    for row in data["near_duplicate_candidates_first_50"]:
        if row["a"] not in parent or row["b"] not in parent:
            raise ValueError("near-duplicate candidate outside images")
        union(row["a"],row["b"])
    groups={}
    for name in parent:
        root=find(name)
        groups.setdefault(root,[]).append(name)
    clusters={}
    for names in groups.values():
        if len(names)>1:
            label="candidate_"+ "_".join(sorted((Path(n).stem for n in names),key=int))
            for name in names: clusters[name]=label
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.writer(f)
        writer.writerow(("filename","rough_sequence_for_review_only","near_duplicate_candidate_cluster",
                         "capture_session_to_verify","scene_group_to_verify","proposed_split_blank_until_review","notes"))
        for p in images:
            writer.writerow((p.name,sequence_window(int(p.stem)),clusters.get(p.name,""),
                             "","","","candidate pairs require visual review; adjacent scene frames stay together"))
    return {"images":len(images),"candidate_pairs":data["near_duplicate_candidate_count"],
            "connected_candidate_clusters":len(set(clusters.values())),
            "split_assigned":0,"output":str(output)}


if __name__=="__main__":
    from argparse import ArgumentParser
    parser=ArgumentParser()
    parser.add_argument("--source",type=Path,default=Path("data/a_blocks/annotation_work"))
    parser.add_argument("--audit",type=Path,default=Path("docs/a/数据审查/数据检查.json"))
    parser.add_argument("--output",type=Path,default=Path("docs/a/数据审查/分组候选与近重复.csv"))
    args=parser.parse_args()
    print(json.dumps(run(args.source,args.audit,args.output),ensure_ascii=False,indent=2))
