import aws_cdk as cdk
from aws_cdk import (aws_ec2 as ec2, aws_lambda as lam, aws_scheduler as scheduler,
                     aws_iam as iam, aws_stepfunctions as sfn,
                     aws_stepfunctions_tasks as tasks)
from constructs import Construct


def make_lambda(scope, name: str, handler: str, vpc, db_secret) -> lam.Function:
    fn = lam.Function(
        scope, name,
        runtime=lam.Runtime.PYTHON_3_12,
        handler=handler,
        code=lam.Code.from_asset(
            ".", bundling=cdk.BundlingOptions(
                image=lam.Runtime.PYTHON_3_12.bundling_image,
                command=["bash", "-c",
                         "pip install . -t /asset-output && cp -r src/aeo /asset-output/"])),
        timeout=cdk.Duration.minutes(10),
        memory_size=512,
        vpc=vpc,
        environment={"AEO_DSN_SECRET_ARN": db_secret.secret_arn},
    )
    db_secret.grant_read(fn)
    return fn


class PipelineStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, *, vpc, raw_bucket, db_secret, **kw):
        super().__init__(scope, cid, **kw)

        names = ["PlanRun", "QueryEngines", "Analyze", "DiagnoseAndDraft", "Persist"]
        handlers = {n: make_lambda(self, n, f"aeo.pipeline.handlers.{h}", vpc, db_secret)
                    for n, h in zip(names, ["plan_run", "query_engines", "analyze",
                                            "diagnose_and_draft", "persist"])}
        for fn in handlers.values():
            raw_bucket.grant_read_write(fn)
            fn.add_to_role_policy(iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:Converse",
                         "comprehend:DetectSentiment", "comprehend:DetectEntities"],
                resources=["*"]))
            fn.add_environment("AEO_RAW_BUCKET", raw_bucket.bucket_name)

        plan = tasks.LambdaInvoke(self, "Plan", lambda_function=handlers["PlanRun"],
                                  payload_response_only=True)
        per_prompt = (tasks.LambdaInvoke(self, "Query", lambda_function=handlers["QueryEngines"],
                                         payload_response_only=True)
                      .next(tasks.LambdaInvoke(self, "Judge", lambda_function=handlers["Analyze"],
                                               payload_response_only=True))
                      .next(tasks.LambdaInvoke(self, "Diagnose",
                                               lambda_function=handlers["DiagnoseAndDraft"],
                                               payload_response_only=True)))
        fan_out = sfn.Map(self, "PerPrompt", items_path="$.batches",
                          max_concurrency=5,
                          item_selector={"run_id.$": "$.run_id", "store_id.$": "$.store_id",
                                         "samples_per_prompt.$": "$.samples_per_prompt",
                                         "prompt_id.$": "$$.Map.Item.Value.prompt_id",
                                         "prompt_text.$": "$$.Map.Item.Value.prompt_text"},
                          result_path="$.items")
        fan_out.item_processor(per_prompt)
        persist = tasks.LambdaInvoke(self, "PersistResults", lambda_function=handlers["Persist"],
                                     payload_response_only=True)
        fail = sfn.Fail(self, "RunFailed")
        definition = plan.next(fan_out).next(persist)
        plan.add_catch(fail, errors=["States.ALL"])

        sm = sfn.StateMachine(self, "RunMachine",
                              definition_body=sfn.DefinitionBody.from_chainable(definition))

        role = iam.Role(self, "SchedulerRole",
                        assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
        sm.grant_start_execution(role)
        scheduler.CfnSchedule(
            self, "WeeklyRun",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression="rate(7 days)",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=sm.state_machine_arn, role_arn=role.role_arn,
                input='{"store_key": "demo-outdoor-store"}'))
