# Values whose first and last four characters appear nowhere in a normal report
AWS_DETAILS = {
    "accessKey": "QXZaAKIDLEAKCHECKACCESSKEYZXQa",
    "secretKey": "QXZbSECRETLEAKCHECKSECRETKEYZXQb",
    "sessionToken": "QXZcSESSIONTOKENLEAKCHECKZXQc",
    "region": "eu-central-1",
    "accountId": "123456789012",
}
AZURE_DETAILS = {
    "tenantId": "aaaaaaaa-bbbb-cccc-dddd-000000000001",
    "clientId": "QXZdCLIENTIDLEAKCHECKVALUEZXQd",
    "clientSecret": "QXZeCLIENTSECRETLEAKCHECKVALUEZXQe",
    "subscriptionId": "aaaaaaaa-bbbb-cccc-dddd-000000000002",
    "resourceGroupName": "rg-leakcheck",
}
SECRET_KEYS = ("accessKey", "secretKey", "sessionToken", "clientId", "clientSecret")


def leaked_fragments(details):
    # Each credential value, and the pieces a masked copy would still show
    found = {}
    for key in SECRET_KEYS:
        value = details.get(key)
        if value:
            found[f"{key}"] = value
            found[f"{key}[:4]"] = value[:4]
            found[f"{key}[-4:]"] = value[-4:]
    return found
