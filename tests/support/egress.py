from cloudexit.report.egress import _summarize_totals


def price_rows(csp):
    return [
        {
            "id": 0,
            "csp": csp,
            "component": "internet_egress",
            "zone": "zone1",
            "tier_from": lower,
            "tier_to": upper,
            "price_per_unit": price,
            "currency": "USD",
            "unit": "GiB",
            "valid_from": "2026-07-01",
            "valid_to": None,
        }
        for lower, upper, price in ((0, 100, 0.0), (100, None, 0.09))
    ]


def empty_estimate(csp=2):
    # An estimate of a scope with no data-bearing resources: enough for a
    # report to be written, without a database or a pricing dataset
    totals = {
        "known_size_bytes": 0,
        "archive_tier_bytes": 0,
        "resources_discovered": 0,
        "resources_with_unknown_size": 0,
    }
    pricing = price_rows(csp)
    return {
        "meta": {
            "name": "Stub",
            "cloud_service_provider": csp,
            "exit_strategy": 1,
            "assessment_type": 1,
            "timestamp": "2026-11-12 09:30:00 UTC",
        },
        "rows": [],
        "totals": totals,
        "pricing": pricing,
        "total_fee": 0.0,
        "type_groups": [],
        "summary": _summarize_totals(totals, 0.0, pricing, csp),
    }
