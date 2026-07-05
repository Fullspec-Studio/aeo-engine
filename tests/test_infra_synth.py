import aws_cdk as cdk
from aws_cdk.assertions import Template

from infra.stacks.data_stack import DataStack
from infra.stacks.pipeline_stack import PipelineStack


def _synth():
    app = cdk.App()
    data = DataStack(app, "TestData")
    pipe = PipelineStack(app, "TestPipe", vpc=data.vpc, raw_bucket=data.raw_bucket,
                         db_secret=data.db_secret)
    return Template.from_stack(data), Template.from_stack(pipe)


def test_data_stack_has_serverless_v2_and_bucket():
    data, _ = _synth()
    data.resource_count_is("AWS::S3::Bucket", 1)
    data.has_resource_properties("AWS::RDS::DBCluster",
                                 {"ServerlessV2ScalingConfiguration": {"MinCapacity": 0.5}})


def test_pipeline_has_state_machine_scheduler_and_five_lambdas():
    _, pipe = _synth()
    pipe.resource_count_is("AWS::StepFunctions::StateMachine", 1)
    pipe.resource_count_is("AWS::Scheduler::Schedule", 1)
    pipe.resource_count_is("AWS::Lambda::Function", 5)
