"""Verify immutable Claude exports; never write or stage either directory."""
from pathlib import Path
import hashlib
ROOT=Path('C:/Dev/V6Lite-dev')
DIRECTORY=ROOT/'docs/batch32_claude_evidence'
MANIFEST_SHA='9e56247d3a27aee3544065669e08257b1be623c9ceea916d0bb94fbb68412b7c'
LEGACY_SHA='80c4d9646e2708b11b3f1c135bfbdad3a5f5beaeda22b62e02c95861d5633e00'

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def verify_directory(directory,manifest_sha,count):
    manifest=directory/'SHA256SUMS.txt'
    assert digest(manifest)==manifest_sha,'read-only evidence manifest changed'
    files={}
    for line in manifest.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():continue
        expected,name=line.split(maxsplit=1);name=name.lstrip('*')
        assert Path(name).name==name and '/' not in name and '\\' not in name
        assert len(expected)==64 and name not in files
        assert digest(directory/name)==expected,('read-only input mismatch',name)
        files[name]=expected
    assert len(files)==count
    return {'manifest_sha256':manifest_sha,'files':files,'directory_read_only':True,'excluded_from_allowlist':True}

def verify_inputs():
    result=verify_directory(DIRECTORY,MANIFEST_SHA,3)
    result['legacy_reference']=verify_directory(ROOT/'docs/batch31_claude_evidence',LEGACY_SHA,6)
    return result

if __name__=='__main__':
    import json
    print(json.dumps(verify_inputs(),indent=2))
