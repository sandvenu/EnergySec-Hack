#!/usr/bin/env bash
# Rebuild the hackathon deck from the HCLTech template.
#   bash build.sh            (run from this folder; needs the project .venv and the pptx skill scripts)
set -euo pipefail
cd "$(dirname "$0")"
PY=../../.venv/Scripts/python.exe
SK="${PPTX_SKILL_SCRIPTS:-/c/Users/svenugopal/AppData/Roaming/Claude/local-agent-mode-sessions/skills-plugin/ffb34849-d981-4b9c-aafd-10b558000dcf/d5809e33-4c14-435a-ac68-f2de34e8aa47/skills/pptx/scripts}"
export PYTHONIOENCODING=utf-8

rm -rf work staged.pptx
$PY -c "import zipfile; zipfile.ZipFile('template.pptx').extractall('work')"
$PY "$SK/add_slide.py" work/ slide12.xml --after slide12.xml   # second three-box slide (community)
$PY reorder.py                                                    # new order; drops the PlugFest slide
$PY "$SK/clean.py" work/ > /dev/null
(cd work && ../$PY -c "
import os, zipfile
with zipfile.ZipFile('../staged.pptx', 'w', zipfile.ZIP_DEFLATED) as z:
    z.write('[Content_Types].xml')
    for root, _, files in os.walk('.'):
        for f in files:
            p = os.path.relpath(os.path.join(root, f), '.').replace(os.sep, '/')
            if p != '[Content_Types].xml':
                z.write(p, p)
")
$PY fill.py staged.pptx Trusted_rApp_Framework_TIP2026.pptx
$PY "$SK/office/validate.py" Trusted_rApp_Framework_TIP2026.pptx --original template.pptx
rm -rf work staged.pptx
