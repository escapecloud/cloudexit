# constants.py
REGION_CHOICES = [
    ('us-east-1', 'us-east-1 (N. Virginia)'),
    ('us-east-2', 'us-east-2 (Ohio)'),
    ('us-west-1', 'us-west-1 (N. California)'),
    ('us-west-2', 'us-west-2 (Oregon)'),
    ('af-south-1', 'af-south-1 (Cape Town)'),
    ('ap-east-1', 'ap-east-1 (Hong Kong)'),
    ('ap-south-1', 'ap-south-1 (Mumbai)'),
    ('ap-northeast-1', 'ap-northeast-1 (Tokyo)'),
    ('ap-northeast-2', 'ap-northeast-2 (Seoul)'),
    ('ap-northeast-3', 'ap-northeast-3 (Osaka)'),
    ('ap-southeast-1', 'ap-southeast-1 (Singapore)'),
    ('ap-southeast-2', 'ap-southeast-2 (Sydney)'),
    ('ca-central-1', 'ca-central-1 (Central)'),
    ('eu-central-1', 'eu-central-1 (Frankfurt)'),
    ('eu-west-1', 'eu-west-1 (Ireland)'),
    ('eu-west-2', 'eu-west-2 (London)'),
    ('eu-west-3', 'eu-west-3 (Paris)'),
    ('eu-south-1', 'eu-south-1 (Milan)'),
    ('eu-north-1', 'eu-north-1 (Stockholm)'),
    ('me-south-1', 'me-south-1 (Bahrain)'),
    ('sa-east-1', 'sa-east-1 (São Paulo)'),
]

REQUIRED_FIELDS_AZURE = ["clientId", "clientSecret", "tenantId", "subscriptionId", "resourceGroupName"]
REQUIRED_FIELDS_AWS = ["accessKey", "secretKey", "region"]
