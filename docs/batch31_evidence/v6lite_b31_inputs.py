"""Verify the operator's immutable Claude export; never write or stage it."""
from pathlib import Path
import hashlib
ROOT=Path('C:/Dev/V6Lite-dev')
DIRECTORY=ROOT/'docs/batch31_claude_evidence'
MANIFEST_SHA='80c4d9646e2708b11b3f1c135bfbdad3a5f5beaeda22b62e02c95861d5633e00'
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def verify_inputs():
    manifest=DIRECTORY/'SHA256SUMS.txt'
    assert digest(manifest)==MANIFEST_SHA,'read-only evidence manifest changed'
    files={}
    for line in manifest.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():continue
        expected,name=line.split(maxsplit=1);name=name.lstrip('*')
        assert Path(name).name==name and '/' not in name and '\\' not in name
        assert len(expected)==64 and name not in files
        assert digest(DIRECTORY/name)==expected,('read-only input mismatch',name)
        files[name]=expected
    assert len(files)==6
    return {'manifest_sha256':MANIFEST_SHA,'files':files,'directory_read_only':True,'excluded_from_allowlist':True}
if __name__=='__main__':
    import json
    print(json.dumps(verify_inputs(),indent=2))
