import sqlite3

from tests.make_smoke_dataset import TABLES
from tests.support.catalogue import build_catalogue
from tests.support.flow import RISKS

EXTRA_TABLES = """
CREATE TABLE resourcetype_data (
    id INTEGER PRIMARY KEY, resource_type INTEGER NOT NULL UNIQUE,
    data_category TEXT NOT NULL, strategy TEXT NOT NULL, params TEXT,
    status TEXT NOT NULL
);
CREATE TABLE egresspricing (
    id INTEGER PRIMARY KEY, csp INTEGER NOT NULL, component TEXT NOT NULL,
    zone TEXT NOT NULL DEFAULT 'zone1', tier_from REAL NOT NULL, tier_to REAL,
    price_per_unit REAL NOT NULL, currency TEXT NOT NULL DEFAULT 'USD',
    unit TEXT NOT NULL DEFAULT 'GiB', valid_from TEXT NOT NULL, valid_to TEXT
);
"""

# Terraform type to resource type code, for the state file fixtures
TF_CODES = {
    "aws_instance": "AWS.ec2.describe_instances.Reservations",
    "aws_s3_bucket": "AWS.s3.list_buckets.Buckets",
    "aws_lambda_function": "AWS.lambda.list_functions.Functions",
    "aws_sqs_queue": "AWS.sqs.list_queues.QueueUrls",
    "azurerm_virtual_machine": "Microsoft.Compute/virtualMachines",
    "azurerm_storage_account": "Microsoft.Storage/storageAccounts",
    "azurerm_mssql_database": "Microsoft.Sql/servers/databases",
}

NAMES = {
    "AWS.ec2.describe_instances.Reservations": "EC2 Instance",
    "AWS.s3.list_buckets.Buckets": "S3 Bucket",
    "AWS.ec2.describe_volumes.Volumes": "EBS Volume",
    "AWS.rds.describe_db_instances.DBInstances": "RDS Instance",
    "AWS.dynamodb.list_tables.TableNames": "DynamoDB Table",
    "AWS.lambda.list_functions.Functions": "Lambda Function",
    "AWS.eks.list_clusters.clusters": "EKS Cluster",
    "AWS.sqs.list_queues.QueueUrls": "SQS Queue",
    "AWS.sns.list_topics.Topics": "SNS Topic",
    "AWS.apigateway.get_rest_apis.items": "API Gateway",
    "AWS.acm.list_certificates.CertificateSummaryList": "Certificate Manager",
    "AWS.cloudwatch.describe_alarms.MetricAlarms": "CloudWatch Alarm",
    "AWS.backup.list_backup_plans.BackupPlansList": "Backup Plan",
    "Microsoft.Compute/virtualMachines": "Virtual Machine",
    "Microsoft.ContainerService/managedClusters": "Kubernetes Cluster",
    "Microsoft.Storage/storageAccounts": "Storage Account",
    "Microsoft.Sql/servers/databases": "SQL Database",
    "Microsoft.DocumentDB/databaseAccounts": "Cosmos DB Account",
    "Microsoft.DBforPostgreSQL/flexibleServers": "PostgreSQL Flexible Server",
    "Microsoft.RecoveryServices/vaults": "Recovery Services Vault",
    "Microsoft.ContainerRegistry/registries": "Container Registry",
    "Microsoft.Compute/disks": "Managed Disk",
    # Deliberately long, for the stress case
    "AWS.cloudformation.list_stacks.StackSummaries": (
        "Sample Infrastructure Stack Definition With An Unreasonably Long Name"
    ),
    "AWS.codeartifact.list_repositories.repositories": (
        "Sample Artifact Repository For Internally Built And Mirrored Packages"
    ),
    "AWS.cloudfront.list_distributions.DistributionList.Items": (
        "Sample Content Delivery Distribution For Static And Dynamic Content"
    ),
}

ICONS = {
    "AWS.ec2.describe_instances.Reservations": "/icons/aws/Compute/EC2.png",
    "AWS.s3.list_buckets.Buckets": "/icons/aws/Storage/Simple-Storage-Service.png",
    "AWS.ec2.describe_volumes.Volumes": "/icons/aws/Storage/Elastic-Block-Store.png",
    "AWS.rds.describe_db_instances.DBInstances": "/icons/aws/Database/RDS.png",
    "AWS.dynamodb.list_tables.TableNames": "/icons/aws/Database/DynamoDB.png",
    "AWS.lambda.list_functions.Functions": "/icons/aws/Compute/Lambda.png",
    "Microsoft.Compute/virtualMachines": (
        "/icons/azure/compute/10021-icon-service-Virtual-Machine.png"
    ),
    "Microsoft.Storage/storageAccounts": (
        "/icons/azure/storage/10086-icon-service-Storage-Accounts.png"
    ),
    "Microsoft.Sql/servers/databases": (
        "/icons/azure/databases/10130-icon-service-SQL-Database.png"
    ),
    "Microsoft.Compute/disks": "/icons/azure/compute/10032-icon-service-Disks.png",
}

# The data a type carries, for the egress estimate
CATEGORIES = {
    "AWS.s3.list_buckets.Buckets": "object",
    "AWS.ec2.describe_volumes.Volumes": "block",
    "AWS.rds.describe_db_instances.DBInstances": "database",
    "AWS.dynamodb.list_tables.TableNames": "database",
    "Microsoft.Storage/storageAccounts": "object",
    "Microsoft.Compute/disks": "block",
    "Microsoft.Sql/servers/databases": "database",
    "Microsoft.DocumentDB/databaseAccounts": "database",
    "Microsoft.RecoveryServices/vaults": "backup",
}

# Types that are not in the inventory catalogue but still carry data
INACTIVE = {"Microsoft.Compute/disks"}

# (alternatives, of them with a support plan) per type; the rest has enough
ALTERNATIVES = {
    "AWS.ec2.describe_volumes.Volumes": (0, 0),
    "AWS.dynamodb.list_tables.TableNames": (0, 0),
    "AWS.s3.list_buckets.Buckets": (2, 1),
    "AWS.rds.describe_db_instances.DBInstances": (1, 0),
    "AWS.eks.list_clusters.clusters": (4, 2),
    "Microsoft.ContainerRegistry/registries": (0, 0),
    "Microsoft.DocumentDB/databaseAccounts": (2, 2),
    "Microsoft.Compute/virtualMachines": (5, 4),
    # More than the page shows at first, so the rest is collapsed
    "AWS.ec2.describe_instances.Reservations": (12, 10),
}
DEFAULT_ALTERNATIVES = (3, 3)

LADDERS = {
    1: (0.087, 0.083, 0.07, 0.05),
    2: (0.09, 0.085, 0.07, 0.05),
}


def placeholder_name(code):
    if code in NAMES:
        return NAMES[code]
    service = code.split(".")[1] if code.startswith("AWS.") else code.split("/")[0]
    return f"Sample {service} Resource"


def provider_of(code):
    return 2 if code.startswith("AWS.") else 1


def _pricing_rows():
    rows = []
    for csp, (first, second, third, fourth) in LADDERS.items():
        tiers = (
            (0, 100, 0.0),
            (100, 10240, first),
            (10240, 51200, second),
            (51200, 153600, third),
            (153600, None, fourth),
        )
        for lower, upper, price in tiers:
            rows.append((csp, "internet_egress", lower, upper, price))
        rows.append((csp, "archive_retrieval", 0, None, 0.02))
    return rows


def build_sample_catalogue(path, scenarios):
    # The codes the scenarios use, and the ones the state files map from
    codes = set(TF_CODES.values())
    for scenario in scenarios:
        codes |= scenario.type_codes()
    codes = sorted(codes)
    tf_codes = {code: tf for tf, code in TF_CODES.items()}

    build_catalogue(path, version="2026-11-02")
    conn = sqlite3.connect(path)
    conn.executescript(TABLES)
    conn.executescript(EXTRA_TABLES)
    conn.execute("DELETE FROM resourcetype")
    conn.executemany("INSERT INTO risk VALUES (?, ?, ?, ?)", RISKS)

    ids = {}
    for index, code in enumerate(codes, start=100):
        ids[code] = index
        conn.execute(
            "INSERT INTO resourcetype (id, csp, code, name, icon, status, tf_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                index,
                provider_of(code),
                code,
                placeholder_name(code),
                ICONS.get(code, ""),
                "f" if code in INACTIVE else "t",
                tf_codes.get(code),
            ),
        )
        if code in CATEGORIES:
            conn.execute(
                "INSERT INTO resourcetype_data (resource_type, data_category, "
                "strategy, params, status) VALUES (?, ?, 'sample', '{}', 't')",
                (index, CATEGORIES[code]),
            )

    conn.execute(
        "INSERT INTO alternativetechnologyorganization (id, name, url, "
        "stability_tier, country_code, years_in_business) "
        "VALUES (1, 'Sample Vendor', NULL, 3, 'DE', 12)"
    )
    technology = 0
    for code, resource_type in ids.items():
        total, supported = ALTERNATIVES.get(code, DEFAULT_ALTERNATIVES)
        for position in range(total):
            technology += 1
            conn.execute(
                "INSERT INTO alternativetechnology (id, product_name, "
                "product_description, product_url, open_source, support_plan, "
                "status, organization_id) VALUES (?, ?, '', NULL, ?, ?, 't', ?)",
                (
                    technology,
                    f"Sample Technology {technology}",
                    "t" if position % 2 else "f",
                    "t" if position < supported else "f",
                    # Some have no recorded vendor
                    1 if technology % 4 else None,
                ),
            )
            for strategy in (1, 2, 3):
                conn.execute(
                    "INSERT INTO alternative (resource_type, "
                    "alternative_technology, strategy_type) VALUES (?, ?, ?)",
                    (resource_type, technology, strategy),
                )

    conn.executemany(
        "INSERT INTO egresspricing (csp, component, zone, tier_from, tier_to, "
        "price_per_unit, currency, unit, valid_from, valid_to) "
        "VALUES (?, ?, 'zone1', ?, ?, ?, 'USD', 'GiB', '2026-07-01', NULL)",
        _pricing_rows(),
    )
    conn.commit()
    conn.close()
    return ids
