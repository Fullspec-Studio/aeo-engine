import aws_cdk as cdk
from aws_cdk import aws_apigateway as apigw, aws_iam as iam, aws_lambda as lam
from constructs import Construct

from infra.stacks.pipeline_stack import make_lambda  # shared factory


class ApiStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, vpc, db_secret, **kw):
        super().__init__(scope, cid, **kw)
        fn = make_lambda(self, "Ingest", "aeo.ingestion.handler.handler", vpc, db_secret)
        # seed_prompts admin action calls Bedrock to generate prompts
        fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:Converse"],
            resources=["*"]))
        api = apigw.RestApi(self, "AeoApi")
        catalog = api.root.add_resource("catalog")
        catalog.add_method("POST", apigw.LambdaIntegration(fn),
                           api_key_required=True)
        plan = api.add_usage_plan("Plan", throttle=apigw.ThrottleSettings(rate_limit=5, burst_limit=10))
        key = api.add_api_key("ConnectorKey")
        plan.add_api_key(key)
        plan.add_api_stage(stage=api.deployment_stage)
