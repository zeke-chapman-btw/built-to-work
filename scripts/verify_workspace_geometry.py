"""Validate matched browser measurements, not CSS source declarations.

Usage: python scripts/verify_workspace_geometry.py /path/to/capture-root
The root must contain before/ and after/ JSON sidecars from real browser captures.
"""
from pathlib import Path
import json
import sys

PAGES = ('tasks','participants','customers','recruiting','approvals','events','operations','reports','settings')
WIDTHS = (1920,1440,768,390,320)


def verify(root):
    failures, checks = [], []
    for width in WIDTHS:
        reference = json.loads((root/'after'/f'operations-{width}.json').read_text())
        for page in PAGES:
            name = f'{page}-{width}'
            before = json.loads((root/'before'/f'{name}.json').read_text())
            after = json.loads((root/'after'/f'{name}.json').read_text())
            local = []
            def require(condition, message):
                if not condition:
                    local.append(message)
            require(after['accepted'], 'screenshot dimensions do not match viewport')
            require(after['viewport']['width'] == width and after['viewport']['height']==900, 'wrong viewport')
            require(not after['pageOverflow'] and not after['mainOverflow'], 'horizontal page/main overflow')
            require(after['viewport']['htmlZoom']=='1' and after['viewport']['bodyZoom']=='1' and after['viewport']['visualScale']==1, 'unmatched zoom')
            require(after['sidebar']['visible']==reference['sidebar']['visible'], 'unmatched sidebar state')
            require(after['fonts']['status']=='loaded' and after['fonts']['jostReady'], 'font not ready')
            for key, fields in [('frame',('x','right')),('header',('x','right')),('titleBounds',('x','y'))]:
                for field in fields:
                    require(abs(after[key][field]-reference[key][field])<=1, f'{key}.{field} differs from Operations')
            require(after['titleStyle']==reference['titleStyle'], 'title typography differs from Operations')
            require(after['controlInventory']==before['controlInventory'], 'existing filter/control inventory changed')
            require(after['headerFormCount']==0, 'header action nested in filter form')
            if after['selection'] and after['selection'].get('display')!='none':
                require(abs(after['selection']['y']-after['header']['bottom']-16)<=1, 'selection transition differs from shared header gap')
            checks.append({'capture':name,'passed':not local})
            failures.extend(f'{name}: {x}' for x in local)
    return {'captures':len(checks),'passed':sum(x['passed'] for x in checks),'failures':failures,'checks':checks}


if __name__=='__main__':
    root = Path(sys.argv[1]).resolve()
    result = verify(root)
    (root/'evidence'/'GEOMETRY-VALIDATION.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='checks'}))
    raise SystemExit(bool(result['failures']))
