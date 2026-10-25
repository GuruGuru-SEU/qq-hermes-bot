"""Validate image-owned dependencies before declaring this bundled plugin install-free.

The source manifest retains dependencies for standalone plugin installs. In this
immutable Docker image the base lock already supplies them, so asking Hermes PM
to resolve an extra plugin member would falsely report a different dependency set.
"""
from importlib.metadata import version
import json
from pathlib import Path
from utils import fast_safe_load
from packaging.requirements import Requirement

path=Path('/opt/qq-hermes-bot/plugin.yaml')
manifest=fast_safe_load(path.read_text())
for text in manifest.get('python_dependencies',[]):
    requirement=Requirement(text)
    installed=version(requirement.name)
    if installed not in requirement.specifier:
        raise RuntimeError(f'Bundled dependency mismatch: {requirement.name} {installed} does not satisfy {requirement.specifier}')
manifest.pop('python_dependencies',None)
path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
print('Bundled plugin dependency versions verified against source manifest')
