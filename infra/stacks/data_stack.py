import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2, aws_rds as rds, aws_s3 as s3
from constructs import Construct


class DataStack(cdk.Stack):
    def __init__(self, scope: Construct, cid: str, **kw):
        super().__init__(scope, cid, **kw)
        self.vpc = ec2.Vpc(self, "Vpc", max_azs=2, nat_gateways=1)
        self.raw_bucket = s3.Bucket(self, "RawResponses",
                                    block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                                    lifecycle_rules=[s3.LifecycleRule(
                                        expiration=cdk.Duration.days(180))])
        cluster = rds.DatabaseCluster(
            self, "Db",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_16_4),
            writer=rds.ClusterInstance.serverless_v2("writer"),
            serverless_v2_min_capacity=0.5,
            serverless_v2_max_capacity=2,
            vpc=self.vpc,
            default_database_name="aeo",
        )
        self.db_secret = cluster.secret
        self.db_cluster = cluster
