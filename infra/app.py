"""CDK entrypoint. Imports use the `infra.stacks` absolute form so they work
both when run via `uv run python infra/app.py` (after inserting the repo root
into sys.path) and when imported by pytest with `pythonpath = ["."]`.

Deviation from brief: brief used bare `from stacks...` imports; replaced with
`from infra.stacks...` so pytest and CDK share identical import paths.
"""
import sys
from pathlib import Path

# Ensure the repo root is on sys.path so `from infra.stacks...` resolves
# whether the file is run as `python infra/app.py` or via `uv run`.
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import aws_cdk as cdk

from infra.stacks.api_stack import ApiStack
from infra.stacks.data_stack import DataStack
from infra.stacks.pipeline_stack import PipelineStack

app = cdk.App()
data = DataStack(app, "AeoData")
ApiStack(app, "AeoApi", vpc=data.vpc, db_secret=data.db_secret)
PipelineStack(app, "AeoPipeline", vpc=data.vpc, raw_bucket=data.raw_bucket, db_secret=data.db_secret)
app.synth()
