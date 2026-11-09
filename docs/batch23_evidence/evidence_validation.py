"""Validate raw batch 23 artifacts against their per-case receipts; no mutations."""
from pathlib import Path
import xml.etree.ElementTree as ET
from run_batch23_checks import ROOT,EV,sha

def validate_record(record,require_before=False,allow_legacy_process_metadata=False):
 label=record['label']
 assert label and Path(label).name==label
 assert sha(EV/(label+'.txt'))==record['stdout_sha256']
 assert sha(EV/(label+'.xml'))==record['junit_sha256']
 root=ET.parse(EV/(label+'.xml')).getroot()
 totals={name:sum(int(s.attrib.get(name,0)) for s in root.iter('testsuite')) for name in ('tests','failures','errors','skipped')}
 cases=[]
 for c in root.iter('testcase'):
  failure=c.find('failure');error=c.find('error');skip=c.find('skipped')
  item=failure if failure is not None else error if error is not None else skip
  cases.append({'classname':c.attrib.get('classname'),'name':c.attrib.get('name'),'outcome':'failure' if failure is not None else 'error' if error is not None else 'skipped' if skip is not None else 'passed','message':item.attrib.get('message','') if item is not None else ''})
 assert totals==record['totals'] and cases==record['cases'] and len(cases)==totals['tests']
 assert record['source_unchanged'] and record['worktree_unchanged']
 assert record['v6lite_runtime_processes']==0
 if 'uninspectable_python_processes' not in record:
  # These immutable historical reds recorded runtime_count but did not yet collect unknown_count.
  # Absence is never converted to zero and never accepted for final green/focused/full gates.
  legacy={'red_22a_run5':'25c0710fbea655e46eae52116748b62659b8693663a840c8b6377ec331014bad','review_red_candidate':'5659728c6bef3094df91b066bd3998e79d5ca1f991d031ccf54013aa151f6e63','review_red_22a':'56a6375a5d36aaa24068c8ca67e292ae104f3aacfb0d3875a1929b1b8b738838'}
  assert allow_legacy_process_metadata and not require_before and label in legacy
  assert sha(EV/(label+'.json'))==legacy[label],'legacy receipt changed'
 else:assert record['uninspectable_python_processes']==0
 if require_before:assert record['specs_unchanged'] is True
 for spec in record['specs']:
  path=Path(spec['path'])
  assert path.resolve().is_relative_to(ROOT.resolve()) and sha(path)==spec['sha256']
 return totals
