"""Trusted rApp Framework on AWS: one stack, in the order the blueprint draws it.

  Agentic Network Framework   AgentCore Gateway (+ interceptors, Policy), Identity, Runtime; Bedrock (+ Guardrails); Strands agents
  SMO / twin                  EC2 host: MAVERIC twin, enforcement point, demo page (reached through SSM, no open ports)
  Evaluation and promotion    SageMaker Model Registry (admission gate), SageMaker training role, Bedrock Evaluations role
  Data foundation             Kinesis -> Firehose -> S3 -> Glue catalog -> Athena (decisions and twin telemetry)
  Cross-cutting               KMS (ledger signing), S3 Object Lock (ledger), IAM role per agent, CloudWatch, budget alarm

Context (cdk.json or -c key=value): region, modelProvider (bedrock | anthropic), modelId (Bedrock),
anthropicModelId (Claude API), useGuardrail, hostInstanceType, policyMode (ENFORCE | LOG_ONLY),
ledgerRetentionDays, deployRuntimes, budgetEmail, monthlyBudgetUsd.

modelProvider toggles where the agents' model runs, for every agent on the host and on AgentCore Runtime:
  bedrock    Claude on Amazon Bedrock (needs Bedrock model access), with Bedrock Guardrails
  anthropic  Claude through the Anthropic API; the key lives in Secrets Manager (set it with
             scripts/aws/set_anthropic_key.ps1). No Bedrock call anywhere unless useGuardrail is true;
             the SMO's own screening of untrusted text stays on either way.
"""

from pathlib import Path

from aws_cdk import (CfnOutput, Duration, RemovalPolicy, Stack, Tags, aws_athena as athena, aws_bedrock as bedrock,
                     aws_bedrockagentcore as agentcore, aws_budgets as budgets, aws_cognito as cognito, aws_ec2 as ec2,
                     aws_glue as glue, aws_iam as iam, aws_kinesis as kinesis, aws_kinesisfirehose as firehose,
                     aws_kms as kms, aws_lambda as lambda_, aws_logs as logs, aws_s3 as s3, aws_s3_assets as assets,
                     aws_sagemaker as sagemaker, aws_secretsmanager as secrets)
from constructs import Construct

from trusted_rapp_infra import policies, tools

INFRA = Path(__file__).resolve().parents[1]
PROJECT = INFRA.parent
MAVERIC = PROJECT.parent / "maveric-1.0.0" / "maveric-1.0.0"
RUNTIME_BUNDLE = INFRA / "build" / "runtime_bundle.zip"     # build_runtime_bundle.py
APP_BUNDLE = INFRA / "build" / "app_bundle.zip"             # build_host_bundle.py
MAVERIC_BUNDLE = INFRA / "build" / "maveric_bundle.zip"     # build_host_bundle.py
PEP_PORT = 8700

# agent key -> (rApp ID, factory prefix, runtime entry file in the bundle or None)
AGENTS = {
    "es": ("energy-saving-rl", "ES", "runtime_energy.py"),
    "lb": ("load-balancing-agent", "LB", "runtime_load_balancing.py"),
    "vendor": ("third-party-demo", "VENDOR", "runtime_vendor.py"),
    "orch": ("orchestrator-agent", "ORCH", "runtime_orchestrator.py"),
}
MODEL_GROUPS = {"energy-saver-ppo": "MAVERIC energy-saving PPO policy used by the energy agent's rl_recommend tool",
                "twin-bdt": "MAVERIC Bayesian digital twin (per-cell GP models) used for shadow runs and what_if"}


class TrustedRappStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kw):
        super().__init__(scope, cid, **kw)
        ctx = lambda k, d=None: self.node.try_get_context(k) if self.node.try_get_context(k) is not None else d
        provider = ctx("modelProvider", "bedrock")
        if provider not in ("bedrock", "anthropic"):
            raise ValueError(f"modelProvider must be bedrock or anthropic, not {provider!r}")
        model_id = (ctx("modelId", "us.anthropic.claude-opus-5-5") if provider == "bedrock"
                    else ctx("anthropicModelId", "claude-opus-5-5"))
        use_guardrail = bool(ctx("useGuardrail", provider == "bedrock"))
        Tags.of(self).add("project", "trusted-rapp")

        # ================= cross-cutting: network, keys, secrets, buckets =================
        vpc = ec2.Vpc(self, "Vpc", max_azs=2, nat_gateways=0, ip_addresses=ec2.IpAddresses.cidr("10.42.0.0/16"),
                      subnet_configuration=[ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC,
                                                                    cidr_mask=24)])
        vpc.add_gateway_endpoint("S3", service=ec2.GatewayVpcEndpointAwsService.S3)  # private S3 path (free)
        one_az = ec2.SubnetSelection(subnets=[vpc.public_subnets[0]])
        vpc.add_interface_endpoint("SecretsManager", service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
                                   subnets=one_az, private_dns_enabled=True)

        signing_key = kms.Key(self, "LedgerSigningKey", key_spec=kms.KeySpec.ECC_NIST_P256,
                              key_usage=kms.KeyUsage.SIGN_VERIFY, alias="alias/trusted-rapp-ledger",
                              description="Signs every decision record of the Trusted rApp ledger",
                              removal_policy=RemovalPolicy.RETAIN)  # audit evidence outlives the stack

        ledger_bucket = s3.Bucket(self, "LedgerBucket", object_lock_enabled=True, versioned=True,
                                  object_lock_default_retention=s3.ObjectLockRetention.governance(
                                      Duration.days(int(ctx("ledgerRetentionDays", 1)))),
                                  block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
                                  encryption=s3.BucketEncryption.S3_MANAGED, removal_policy=RemovalPolicy.RETAIN)
        lake = s3.Bucket(self, "DataLake", block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
                         encryption=s3.BucketEncryption.S3_MANAGED, removal_policy=RemovalPolicy.DESTROY,
                         auto_delete_objects=True)
        artifacts = s3.Bucket(self, "Artifacts", block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
                              encryption=s3.BucketEncryption.S3_MANAGED, removal_policy=RemovalPolicy.DESTROY,
                              auto_delete_objects=True, versioned=True,
                              cors=[s3.CorsRule(allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.PUT,
                                                                 s3.HttpMethods.POST, s3.HttpMethods.DELETE],
                                                allowed_origins=["*"], allowed_headers=["*"],
                                                exposed_headers=["Access-Control-Allow-Origin"])])  # Bedrock Evaluations

        gateway_secret = secrets.Secret(self, "GatewaySharedSecret", description="Lambda target -> SMO shared secret",
                                        generate_secret_string=secrets.SecretStringGenerator(
                                            exclude_punctuation=True, password_length=48))

        # ================= data foundation: Kinesis -> Firehose -> S3 -> Glue -> Athena =====
        stream = kinesis.Stream(self, "Events", stream_mode=kinesis.StreamMode.PROVISIONED, shard_count=1,
                                retention_period=Duration.hours(24), encryption=kinesis.StreamEncryption.MANAGED)
        fh_role = iam.Role(self, "FirehoseRole", assumed_by=iam.ServicePrincipal("firehose.amazonaws.com"))
        stream.grant_read(fh_role)
        lake.grant_read_write(fh_role)
        delivery = firehose.CfnDeliveryStream(
            self, "EventsToLake", delivery_stream_type="KinesisStreamAsSource",
            kinesis_stream_source_configuration={"kinesisStreamArn": stream.stream_arn, "roleArn": fh_role.role_arn},
            extended_s3_destination_configuration={
                "bucketArn": lake.bucket_arn, "roleArn": fh_role.role_arn,
                "prefix": "events/dt=!{timestamp:yyyy-MM-dd}/",
                "errorOutputPrefix": "errors/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/",
                "bufferingHints": {"intervalInSeconds": 60, "sizeInMBs": 5}, "compressionFormat": "UNCOMPRESSED"})
        delivery.node.add_dependency(fh_role)

        db = glue.CfnDatabase(self, "Catalog", catalog_id=self.account,
                              database_input={"name": "trusted_rapp", "description": "Trusted rApp decisions and telemetry"})
        cols = [("kind", "string"), ("event_time", "double"), ("seq", "bigint"), ("record_time", "string"),
                ("rapp_id", "string"), ("decision", "string"), ("policy_id", "string"), ("tier", "string"),
                ("reason", "string"), ("justification", "string"), ("assessment_class", "string"), ("hash", "string"),
                ("prev_hash", "string"), ("key_id", "string"), ("kpi_weak_coverage_pct", "double"),
                ("kpi_energy_saving_pct", "double"), ("kpi_max_cell_load", "double"), ("kpi_excess_ues", "double"),
                ("kpi_active_cells", "double"), ("payload", "string")]
        table = glue.CfnTable(self, "EventsTable", catalog_id=self.account, database_name="trusted_rapp", table_input={
            "name": "events", "tableType": "EXTERNAL_TABLE",
            "parameters": {"classification": "json", "projection.enabled": "true", "projection.dt.type": "date",
                           "projection.dt.format": "yyyy-MM-dd", "projection.dt.range": "2026-10-01,NOW",
                           "storage.location.template": f"s3://{lake.bucket_name}/events/dt=${{dt}}/"},
            "partitionKeys": [{"name": "dt", "type": "string"}],
            "storageDescriptor": {"columns": [{"name": n, "type": t} for n, t in cols],
                                  "location": f"s3://{lake.bucket_name}/events/",
                                  "inputFormat": "org.apache.hadoop.mapred.TextInputFormat",
                                  "outputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                                  "serdeInfo": {"serializationLibrary": "org.openx.data.jsonserde.JsonSerDe",
                                                "parameters": {"ignore.malformed.json": "true"}}}})
        table.add_resource_dependency(db)
        wg = athena.CfnWorkGroup(self, "Athena", name="trusted-rapp", recursive_delete_option=True,
                                 work_group_configuration={"enforceWorkGroupConfiguration": True,
                                                           "resultConfiguration": {"outputLocation": f"s3://{lake.bucket_name}/athena-results/"}})
        for qname, desc, sql in [
            ("decisions-by-class", "Every decision with its assessment class, newest first",
             "SELECT record_time, rapp_id, decision, policy_id, assessment_class, reason FROM trusted_rapp.events "
             "WHERE kind = 'decision' ORDER BY seq DESC LIMIT 200"),
            ("held-and-denied", "What the framework stopped or sent to a human, by agent and policy",
             "SELECT rapp_id, decision, policy_id, count(*) AS n FROM trusted_rapp.events WHERE kind = 'decision' "
             "AND decision IN ('deny', 'pending_approval', 'hold', 'reject') GROUP BY 1, 2, 3 ORDER BY n DESC"),
            ("kpi-timeline", "Twin KPIs after every executed change",
             "SELECT from_unixtime(event_time) AS t, kpi_max_cell_load, kpi_excess_ues, kpi_weak_coverage_pct, "
             "kpi_energy_saving_pct FROM trusted_rapp.events WHERE kind = 'twin' ORDER BY event_time")]:
            athena.CfnNamedQuery(self, f"Q{qname}", database="trusted_rapp", work_group=wg.name, name=qname,
                                 description=desc, query_string=sql).add_resource_dependency(wg)

        # ================= agents' model: Bedrock or the Anthropic API (modelProvider) ======
        model_env = {"AGENT_MODEL_PROVIDER": provider, "AGENT_MODEL_ID": model_id}
        anthropic_key = None
        if provider == "anthropic":
            # Created with a random placeholder; the operator puts the real key in (never in code or templates).
            anthropic_key = secrets.Secret(self, "AnthropicApiKey", description="Anthropic API key for the agents "
                                           "(set with scripts/aws/set_anthropic_key.ps1)",
                                           generate_secret_string=secrets.SecretStringGenerator(exclude_punctuation=True))
            model_env["ANTHROPIC_API_KEY_SECRET_ARN"] = anthropic_key.secret_arn

        # ================= Bedrock Guardrails (untrusted text and agents' model calls) =====
        guardrail = guardrail_version = None
        guardrail_env = {}  # agents' model calls (Bedrock only)
        if use_guardrail:
            guardrail = bedrock.CfnGuardrail(
                self, "Guardrail", name="trusted-rapp-untrusted-input",
                description="Prompt-attack screening of OSS alarm text and agent model calls",
                blocked_input_messaging="Blocked by the operator's guardrail: the input looked like an instruction to the agent.",
                blocked_outputs_messaging="Blocked by the operator's guardrail.",
                content_policy_config={"filtersConfig": [{"type": "PROMPT_ATTACK", "inputStrength": "HIGH",
                                                          "outputStrength": "NONE"}]})
            guardrail_version = bedrock.CfnGuardrailVersion(self, "GuardrailV1", description="v1",
                                                            guardrail_identifier=guardrail.attr_guardrail_id)
            if provider == "bedrock":
                guardrail_env = {"BEDROCK_GUARDRAIL_ID": guardrail.attr_guardrail_id,
                                 "BEDROCK_GUARDRAIL_VERSION": guardrail_version.attr_version}

        # ================= Identity: one Cognito app client per agent ======================
        pool = cognito.UserPool(self, "AgentIdentities", self_sign_up_enabled=False, removal_policy=RemovalPolicy.DESTROY)
        domain = pool.add_domain("Domain", cognito_domain=cognito.CognitoDomainOptions(
            domain_prefix=f"trusted-rapp-{self.account}"))
        smo_scope = cognito.ResourceServerScope(scope_name="tools", scope_description="Call SMO tools through the gateway")
        rs = pool.add_resource_server("Smo", identifier="smo", scopes=[smo_scope])
        token_url = domain.base_url() + "/oauth2/token"
        discovery_url = pool.user_pool_provider_url + "/.well-known/openid-configuration"
        scope_name = "smo/tools"
        clients, client_secrets, providers = {}, {}, {}
        for key, (rapp_id, prefix, _) in AGENTS.items():
            c = pool.add_client(f"{prefix}Client", user_pool_client_name=rapp_id, generate_secret=True,
                                access_token_validity=Duration.minutes(60),
                                o_auth=cognito.OAuthSettings(flows=cognito.OAuthFlows(client_credentials=True),
                                                             scopes=[cognito.OAuthScope.resource_server(rs, smo_scope)]))
            clients[key] = c.user_pool_client_id
            client_secrets[key] = secrets.Secret(self, f"{prefix}ClientSecret", description=f"{rapp_id} Cognito client secret",
                                                 secret_object_value={"client_secret": c.user_pool_client_secret})
            # AgentCore Identity: the agent on Runtime gets its token from here and never sees the secret
            providers[key] = agentcore.CfnOAuth2CredentialProvider(
                self, f"{prefix}Identity", name=f"trusted-rapp-{key}", credential_provider_vendor="CustomOauth2",
                oauth2_provider_config_input={"customOauth2ProviderConfig": {
                    "oauthDiscovery": {"discoveryUrl": discovery_url}, "clientId": c.user_pool_client_id,
                    "clientSecret": c.user_pool_client_secret.unsafe_unwrap()}})
            providers[key].node.add_dependency(domain)

        # ================= SMO host: MAVERIC twin + enforcement point + demo page ==========
        host_sg = ec2.SecurityGroup(self, "HostSg", vpc=vpc, description="Twin host: no inbound except the tool target",
                                    allow_all_outbound=True)
        target_sg = ec2.SecurityGroup(self, "TargetSg", vpc=vpc, description="Gateway tool target Lambda",
                                      allow_all_outbound=True)
        host_sg.add_ingress_rule(target_sg, ec2.Port.tcp(PEP_PORT), "AgentCore Gateway tool target to SMO /v1")  # EC2 rejects '>'

        host_role = iam.Role(self, "HostRole", assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
                             managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
                                               iam.ManagedPolicy.from_aws_managed_policy_name("CloudWatchAgentServerPolicy")])
        signing_key.grant(host_role, "kms:Sign", "kms:GetPublicKey", "kms:DescribeKey")  # never kms:Decrypt / key export
        ledger_bucket.grant_read_write(host_role)
        stream.grant_write(host_role)
        artifacts.grant_read_write(host_role)
        gateway_secret.grant_read(host_role)
        if anthropic_key:
            anthropic_key.grant_read(host_role)
        for s_ in client_secrets.values():
            s_.grant_read(host_role)  # agents hosted on the twin host for the demo page use client credentials
        host_role.add_to_policy(iam.PolicyStatement(actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                                                             "bedrock:Converse", "bedrock:ConverseStream", "bedrock:ApplyGuardrail"],
                                                    resources=["*"]))
        host_role.add_to_policy(iam.PolicyStatement(actions=["sagemaker:ListModelPackages", "sagemaker:DescribeModelPackage"],
                                                    resources=["*"]))

        for b in (APP_BUNDLE, MAVERIC_BUNDLE):
            if not b.exists():
                raise FileNotFoundError(f"{b.name} missing: run infra/build_host_bundle.py first")
        app_asset = assets.Asset(self, "AppCode", path=str(APP_BUNDLE))
        maveric_asset = assets.Asset(self, "MavericCode", path=str(MAVERIC_BUNDLE))
        app_asset.grant_read(host_role)
        maveric_asset.grant_read(host_role)

        # ================= AgentCore Gateway: interceptors, Lambda target, Policy ===========
        interceptor = lambda_.Function(
            self, "Interceptor", runtime=lambda_.Runtime.PYTHON_3_12, handler="handler.handler",
            code=lambda_.Code.from_asset(str(INFRA / "lambdas" / "interceptor")), timeout=Duration.seconds(30),
            memory_size=256, log_group=logs.LogGroup(self, "InterceptorLogs", retention=logs.RetentionDays.TWO_WEEKS,
                                                    removal_policy=RemovalPolicy.DESTROY),
            environment={"CLIENT_MAP": self.to_json_string({clients[k]: AGENTS[k][0] for k in AGENTS}),
                         **({"GUARDRAIL_ID": guardrail.attr_guardrail_id, "GUARDRAIL_VERSION": guardrail_version.attr_version}
                            if guardrail else {})})
        if guardrail:
            interceptor.add_to_role_policy(iam.PolicyStatement(actions=["bedrock:ApplyGuardrail"],
                                                               resources=[guardrail.attr_guardrail_arn]))

        gateway_role = iam.Role(self, "GatewayRole", assumed_by=iam.ServicePrincipal(
            "bedrock-agentcore.amazonaws.com", conditions={"StringEquals": {"aws:SourceAccount": self.account}}))
        gateway_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions",
                     "bedrock-agentcore:GetPolicyEngine"], resources=["*"]))
        interceptor.grant_invoke(gateway_role)

        engine = agentcore.CfnPolicyEngine(self, "PolicyEngine", name="trusted_rapp_smo",
                                           description="Agent scope at the gateway, generated from policies/scope.cedar")
        gateway = agentcore.CfnGateway(
            self, "Gateway", name="trusted-rapp-smo", protocol_type="MCP", role_arn=gateway_role.role_arn,
            description="SMO boundary for RAN agents", authorizer_type="CUSTOM_JWT",
            authorizer_configuration={"customJwtAuthorizer": {"discoveryUrl": discovery_url,
                                                              "allowedClients": [clients[k] for k in AGENTS]}},
            protocol_configuration={"mcp": {"instructions": "RAN configuration tools at the SMO boundary. Every call is "
                                            "checked against the caller's admitted scope, budget, risk tier and every "
                                            "agent's intent; denials return the policy ID."}},
            interceptor_configurations=[{"interceptionPoints": ["REQUEST", "RESPONSE"],
                                         "interceptor": {"lambda": {"arn": interceptor.function_arn}},
                                         "inputConfiguration": {"passRequestHeaders": True}}],
            policy_engine_configuration={"arn": engine.attr_policy_engine_arn, "mode": ctx("policyMode", "ENFORCE")})
        gateway.node.add_dependency(gateway_role)

        user_data = ec2.UserData.for_linux()
        host = ec2.Instance(self, "TwinHost", vpc=vpc, vpc_subnets=one_az, security_group=host_sg, role=host_role,
                            instance_type=ec2.InstanceType(ctx("hostInstanceType", "t3.xlarge")),
                            machine_image=ec2.MachineImage.latest_amazon_linux2023(), require_imdsv2=True,
                            user_data=user_data, user_data_causes_replacement=True,
                            block_devices=[ec2.BlockDevice(device_name="/dev/xvda", volume=ec2.BlockDeviceVolume.ebs(
                                40, volume_type=ec2.EbsDeviceVolumeType.GP3, encrypted=True))])

        target_fn = lambda_.Function(
            self, "ToolTarget", runtime=lambda_.Runtime.PYTHON_3_12, handler="handler.handler",
            code=lambda_.Code.from_asset(str(INFRA / "lambdas" / "tool_target")), timeout=Duration.seconds(120),
            memory_size=256, vpc=vpc, vpc_subnets=one_az, allow_public_subnet=True, security_groups=[target_sg],
            log_group=logs.LogGroup(self, "ToolTargetLogs", retention=logs.RetentionDays.TWO_WEEKS,
                                    removal_policy=RemovalPolicy.DESTROY),
            environment={"PEP_URL": f"http://{host.instance_private_ip}:{PEP_PORT}",
                         "GATEWAY_SECRET_ARN": gateway_secret.secret_arn})
        gateway_secret.grant_read(target_fn)
        # Separate policy (not the role's default one): the Gateway must not wait for the target Lambda,
        # which waits for the twin host, which waits for the runtimes, which wait for the Gateway URL.
        target_invoke = iam.Policy(self, "GatewayTargetInvoke", roles=[gateway_role], statements=[
            iam.PolicyStatement(actions=["lambda:InvokeFunction"], resources=[target_fn.function_arn,
                                                                              target_fn.function_arn + ":*"])])

        target = agentcore.CfnGatewayTarget(
            self, "SmoTools", name=tools.TARGET_NAME, gateway_identifier=gateway.attr_gateway_identifier,
            description="SMO tools, enforced by the Trusted rApp framework",
            target_configuration={"mcp": {"lambda": {"lambdaArn": target_fn.function_arn,
                                                     "toolSchema": {"inlinePayload": tools.tool_definitions()}}}},
            credential_provider_configurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}])
        target.node.add_dependency(target_fn, target_invoke)

        for name, mirrors, statement in policies.statements(clients, gateway.attr_gateway_arn):
            pol = agentcore.CfnPolicy(self, f"Policy{name}", name=name, policy_engine_id=engine.attr_policy_engine_id,
                                      description=f"mirrors {mirrors} in policies/scope.cedar",
                                      definition={"cedar": {"statement": statement}})
            pol.add_resource_dependency(target)  # the policy schema comes from the target's tools

        # ================= Evaluation and promotion: model registry, training, evaluation ===
        for g, desc in MODEL_GROUPS.items():
            sagemaker.CfnModelPackageGroup(self, f"Models{g}", model_package_group_name=f"trusted-rapp-{g}",
                                           model_package_group_description=desc)
        sm_role = iam.Role(self, "SageMakerRole", assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
                           managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerFullAccess")])
        artifacts.grant_read_write(sm_role)
        eval_role = iam.Role(self, "BedrockEvalRole", assumed_by=iam.ServicePrincipal(
            "bedrock.amazonaws.com", conditions={"StringEquals": {"aws:SourceAccount": self.account}}))
        artifacts.grant_read_write(eval_role)
        eval_role.add_to_policy(iam.PolicyStatement(actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                                                             "bedrock:CreateModelInvocationJob", "bedrock:GetInferenceProfile",
                                                             "bedrock:ListInferenceProfiles"], resources=["*"]))

        # ================= AgentCore Runtime: one runtime and one IAM role per agent ========
        runtime_arns = {}
        common_env = {"AGENTCORE_GATEWAY_URL": gateway.attr_gateway_url, **model_env, **guardrail_env,
                      "AWS_REGION": self.region, "COGNITO_SCOPE": scope_name,
                      "COGNITO_TOKEN_URL": token_url, "USE_OS_TRUSTSTORE": "0"}
        if ctx("deployRuntimes", True) and RUNTIME_BUNDLE.exists():
            bundle = assets.Asset(self, "RuntimeBundle", path=str(RUNTIME_BUNDLE))
            for key in ("lb", "es", "vendor", "orch"):  # orchestrator last: it calls the others
                rapp_id, prefix, entry = AGENTS[key]
                role = iam.Role(self, f"{prefix}RuntimeRole", role_name=None, assumed_by=iam.ServicePrincipal(
                    "bedrock-agentcore.amazonaws.com", conditions={"StringEquals": {"aws:SourceAccount": self.account}}))
                bundle.grant_read(role)
                if anthropic_key:
                    anthropic_key.grant_read(role)
                role.add_to_policy(iam.PolicyStatement(actions=[
                    "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:Converse",
                    "bedrock:ConverseStream", "bedrock:ApplyGuardrail"], resources=["*"]))
                role.add_to_policy(iam.PolicyStatement(actions=[
                    "bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    "bedrock-agentcore:GetWorkloadAccessTokenForUserId", "bedrock-agentcore:GetResourceOauth2Token"],
                    resources=["*"]))
                role.add_to_policy(iam.PolicyStatement(actions=["secretsmanager:GetSecretValue"], resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:bedrock-agentcore-identity!*"]))
                role.add_to_policy(iam.PolicyStatement(actions=[
                    "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams",
                    "logs:DescribeLogGroups", "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules", "xray:GetSamplingTargets", "cloudwatch:PutMetricData"], resources=["*"]))
                env = {**common_env, f"{prefix}_IDENTITY_PROVIDER": f"trusted-rapp-{key}"}
                if key == "orch":
                    env.update({"LB_RUNTIME_ARN": runtime_arns["lb"], "ES_RUNTIME_ARN": runtime_arns["es"]})
                    role.add_to_policy(iam.PolicyStatement(actions=["bedrock-agentcore:InvokeAgentRuntime"],
                                                           resources=[runtime_arns["lb"], runtime_arns["es"],
                                                                      runtime_arns["lb"] + "/*", runtime_arns["es"] + "/*"]))
                rt = agentcore.CfnRuntime(
                    self, f"{prefix}Runtime", agent_runtime_name=f"trusted_rapp_{key}", role_arn=role.role_arn,
                    description=f"{rapp_id} (Strands)", protocol_configuration="HTTP",
                    agent_runtime_artifact={"codeConfiguration": {
                        "code": {"s3": {"bucket": bundle.s3_bucket_name, "prefix": bundle.s3_object_key}},
                        "entryPoint": [entry], "runtime": "PYTHON_3_12"}},
                    network_configuration={"networkMode": "PUBLIC"}, environment_variables=env)
                rt.node.add_dependency(role)
                rt.node.add_dependency(providers[key])
                runtime_arns[key] = rt.attr_agent_runtime_arn
                CfnOutput(self, f"Out{prefix}RuntimeArn", key=f"{prefix}RuntimeArn", value=rt.attr_agent_runtime_arn)
            host_role.add_to_policy(iam.PolicyStatement(actions=["bedrock-agentcore:InvokeAgentRuntime"], resources=[
                a for arn in runtime_arns.values() for a in (arn, arn + "/*")]))

        # ================= host bootstrap (env file, code, install, systemd) ================
        env = {
            "BIND_HOST": "0.0.0.0", "AWS_REGION": self.region, "AWS_DEFAULT_REGION": self.region,
            "MAVERIC_ROOT": "/opt/maveric", "USE_OS_TRUSTSTORE": "0", "APPROVAL_TIMEOUT_S": "300",
            "LEDGER_KMS_KEY_ID": signing_key.key_arn, "LEDGER_BUCKET": ledger_bucket.bucket_name,
            "KINESIS_STREAM": stream.stream_name, "MODEL_REGISTRY_CHECK": "1",
            "GATEWAY_SECRET_ARN": gateway_secret.secret_arn, "AGENTCORE_GATEWAY_URL": gateway.attr_gateway_url,
            **model_env, **guardrail_env,
            "COGNITO_TOKEN_URL": token_url, "COGNITO_SCOPE": scope_name, "ARTIFACTS_BUCKET": artifacts.bucket_name,
            **{f"{AGENTS[k][1]}_COGNITO_CLIENT_ID": clients[k] for k in AGENTS},
            **{f"{AGENTS[k][1]}_CLIENT_SECRET_ARN": client_secrets[k].secret_arn for k in AGENTS},
            **{f"{AGENTS[k][1]}_RUNTIME_ARN": arn for k, arn in runtime_arns.items() if k != "orch"},
        }
        user_data.add_commands(
            "set -euxo pipefail",
            "dnf install -y unzip",
            "mkdir -p /opt/trusted-rapp /opt/maveric",
            f"aws s3 cp {app_asset.s3_object_url} /tmp/app.zip && unzip -oq /tmp/app.zip -d /opt/trusted-rapp",
            f"aws s3 cp {maveric_asset.s3_object_url} /tmp/maveric.zip && unzip -oq /tmp/maveric.zip -d /opt/maveric",
            "cat > /etc/trusted-rapp.env <<'EOF'",
            *[f"{k}={v}" for k, v in env.items()],
            "EOF",
            "bash /opt/trusted-rapp/infra/host/install.sh")

        # ================= budget alarm =====================================================
        if ctx("budgetEmail"):
            budgets.CfnBudget(self, "Budget", budget={
                "budgetType": "COST", "timeUnit": "MONTHLY", "budgetName": "trusted-rapp",
                "budgetLimit": {"amount": float(ctx("monthlyBudgetUsd", 100)), "unit": "USD"}},  # whole account
                notifications_with_subscribers=[{
                    "notification": {"notificationType": "ACTUAL", "comparisonOperator": "GREATER_THAN",
                                     "threshold": 80, "thresholdType": "PERCENTAGE"},
                    "subscribers": [{"subscriptionType": "EMAIL", "address": ctx("budgetEmail")}]}])

        # ================= outputs ==========================================================
        for k, v in {
            "GatewayUrl": gateway.attr_gateway_url, "GatewayId": gateway.attr_gateway_identifier,
            "PolicyEngineId": engine.attr_policy_engine_id, "TwinHostId": host.instance_id,
            "LedgerBucket": ledger_bucket.bucket_name, "LedgerKeyArn": signing_key.key_arn,
            "DataLakeBucket": lake.bucket_name, "ArtifactsBucket": artifacts.bucket_name,
            "EventStream": stream.stream_name, "AthenaWorkGroup": "trusted-rapp", "ModelProvider": f"{provider}:{model_id}",
            "CognitoTokenUrl": token_url, "CognitoScope": scope_name, "SageMakerRoleArn": sm_role.role_arn,
            "BedrockEvalRoleArn": eval_role.role_arn,
            "DemoPageTunnel": (f"aws ssm start-session --target {host.instance_id} --document-name "
                               f"AWS-StartPortForwardingSession --parameters portNumber={PEP_PORT},localPortNumber={PEP_PORT}"),
            **({"GuardrailId": guardrail.attr_guardrail_id} if guardrail else {}),
            **({"AnthropicKeySecretArn": anthropic_key.secret_arn} if anthropic_key else {}),
            **{f"{AGENTS[k][1]}ClientId": clients[k] for k in AGENTS},
            **{f"{AGENTS[k][1]}ClientSecretArn": client_secrets[k].secret_arn for k in AGENTS},
        }.items():
            CfnOutput(self, f"Out{k}", key=k, value=v)
