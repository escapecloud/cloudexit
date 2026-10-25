BLOCKS = {
    "language_name": "English",
    "pdf_subject": "{document_id} · Basic assessment collected by cloudexit {version}",
    "ev_language": "Language",
    "ev_dash": "–",
    "ev_document_information_heading": "Document Information",
    "ev_assessment_scope_heading": "Assessment Scope",
    "ev_provenance_heading": "Provenance",
    "ev_executive_report": "Executive Summary",
    "ev_egress_report": "Egress & Data Landscape",
    "ev_document_id": "Document ID",
    "ev_document_type": "Document Type",
    "ev_companion_document": "Companion Document",
    "ev_collected_by": "Collected by",
    "ev_collected_by_value": "cloudexit {version}",
    "ev_collected_at": "Collected on",
    "ev_generated_at": "Generated at",
    "ev_cloud_provider": "Cloud Provider",
    "ev_account_id": "Account ID",
    "ev_tenant_id": "Tenant ID",
    "ev_subscription_id": "Subscription ID",
    "ev_resource_group": "Resource Group",
    "ev_region": "Region",
    "ev_file": "State File",
    "ev_state": "State",
    "ev_subscriptions": "Subscriptions",
    "ev_resource_groups": "Resource Groups",
    "ev_regions": "Regions",
    "ev_not_determinable": "Not determinable from the state",
    "ev_source_label": "Source",
    "ev_source_live": "Provider APIs",
    "ev_source_tfstate": "Terraform State",
    "ev_source_value": "{source} (data as of {completed})",
    "ev_catalogue_label": "Data Catalogue",
    "ev_catalogue_value": "{version} · (sha256 {sha})",
    "ev_catalogue_unknown": "Unknown (the dataset records no version)",
    "ev_run_id": "Run ID",
    "ev_platform_label": "Platform",
    "ev_platform_none": "Self-collected data: no platform evaluated this report.",
    "ev_platform_uploaded": "Uploaded to the platform",
    "ev_stored_footnote": (
        "This document was generated when run {run} completed and is stored "
        "unchanged. Later runs are reported in their own documents."
    ),
    "cover_title_executive_summary": "Executive Summary",
    "cover_title_egress": "Egress & Data Landscape",
    "cover_doc_id": "Document ID",
    "cover_edition": "Edition",
    "cover_edition_value": "Community",
    "cover_exit_strategy": "Exit Strategy",
    "cover_run": "Assessment Run",
    "cover_run_value": "{run} · data as of {completed}",
    "cover_generated": "Generated",
    "cover_collected_with": "Collected by cloudexit {version} on {date}",
    "footer_page_of": "Page {page} of {total}",
    "footer_edition": "Community Edition",
    "strategy_name_repatriation": "Repatriation to On-Premises",
    "strategy_name_hybrid_cloud": "Hybrid Cloud Adoption",
    "strategy_name_alternate_cloud": "Migration to Alternate Cloud",
    "strategy_def_repatriation": (
        "moving all resources and data back to a data centre or on-premises "
        "environment"
    ),
    "strategy_def_hybrid_cloud": (
        "a mixture of on-premises and alternative cloud deployment models"
    ),
    "strategy_def_alternate_cloud": (
        "transitioning to a different cloud provider from the current one"
    ),
    "scope_counts": "{resources} of {types}",
    "scope_resources_one": "1 resource",
    "scope_resources_many": "{n} resources",
    "scope_types_one": "1 type",
    "scope_types_many": "{n} types",
    "scope_regions_one": "1 region",
    "scope_regions_many": "{n} regions",
    "scope_boundary_azure": (
        "This assessment covers {counts} in Microsoft Azure resource group "
        "{resource_group} ({regions}). Resources outside this resource "
        "group are not assessed."
    ),
    "scope_boundary_aws": (
        "This assessment covers {counts} in AWS region {region}. Other "
        "regions are not assessed."
    ),
    "scope_boundary_tfstate": (
        "This assessment covers {counts} in the Terraform state file {file} "
        "({provider}, {regions}). Resources that Terraform does not manage "
        "are not assessed."
    ),
    "scope_strategy": (
        "The scope is assessed for the {strategy} exit strategy: {definition}."
    ),
    "scope_strategy_plain": "The scope is assessed for the {strategy} exit strategy.",
    "es_heading_introduction": "Introduction",
    "es_heading_purpose": "Purpose",
    "es_heading_scope": "Scope",
    "es_heading_method": "Method",
    "es_heading_limitations": "Limitations",
    "es_purpose_block": (
        "This Executive Summary gives decision-makers an overview of the "
        "cloud exit assessment: the cloud resources in use and their costs, "
        "the exit risks identified and the available alternative "
        "technologies for the defined scope and the selected exit strategy."
    ),
    "es_method_lead": (
        "The assessment was collected and evaluated locally with cloudexit. "
        "It combines automated discovery, structured data "
        "processing and rule-based evaluation, so that results are "
        "consistent and repeatable across cloud providers."
    ),
    "es_method_phases_lead": (
        "Once the scope is defined, the assessment runs three phases:"
    ),
    "es_method_phases": [
        (
            "Discovery",
            "identifies the active resources in the scope and classifies them by type, region and cost.",
        ),
        (
            "Risk assessment",
            "evaluates exit risks by comparing the discovered resource types with the availability and enterprise support of alternative technologies.",
        ),
        (
            "Reporting",
            "compiles the results into this Executive Summary.",
        ),
    ],
    "es_method_catalogue": (
        "Risks and alternatives are taken from the data catalogue version "
        "recorded on page 2, so the results of this run can be reproduced."
    ),
    "err_limitations": (
        "This report gives directional guidance on the selected exit "
        "strategy and its risks. It is not a migration roadmap and not an "
        "audit. Business or technical circumstances specific to the "
        "organisation may require further validation."
    ),
    "err_limitations_data": (
        "Results reflect the data of the run shown on page 2. The analysis "
        "assumes that the discovered resources are correctly configured and "
        "accessible. Dependencies outside the defined scope, such as "
        "on-premises systems or other cloud accounts, are not covered; "
        "results are indicative, not exhaustive."
    ),
    "bs_heading": "Brief Summary",
    "bs_lead": (
        "This summary condenses the findings of this report for {scope} "
        "({provider}), assessed for the {strategy} exit strategy."
    ),
    "bs_scope_tfstate": "the Terraform state file",
    "bs_outlook_good": (
        "No critical or high risks were found, and every resource type has "
        "at least one alternative."
    ),
    "bs_outlook_attention": "{parts} need attention before an exit.",
    "bs_outlook_attention_one": "{parts} needs attention before an exit.",
    "bs_part_risks_one": "1 critical or high risk",
    "bs_part_risks_many": "{n} critical or high risks",
    "bs_part_hotspots_one": "1 resource type without an alternative",
    "bs_part_hotspots_many": "{n} resource types without an alternative",
    "bs_and": " and ",
    "bs_more": " and {k} more",
    "bs_stat_costs": "Costs, 6 Months",
    "bs_findings_heading": "Key Findings",
    "bs_th_area": "Area",
    "bs_th_finding": "Finding",
    "bs_th_details": "Details",
    "bs_area_risks": "Risks",
    "bs_area_alternatives": "Alternatives",
    "bs_area_costs": "Costs",
    "bs_area_footprint": "Footprint",
    "bs_find_risks_none": "No exit risks were identified.",
    "bs_find_risks_one": "<b>1</b> unique exit risk, {severity}: <b>{code}</b> {name}.",
    "bs_find_risks_many": "<b>{n}</b> unique exit risks: {counts}. Most severe: <b>{code}</b> {name} ({severity}).",
    "bs_find_risks_no_severe": "No critical or high risks.",
    "bs_find_alts_all": "All <b>{n}</b> resource types have at least one alternative (<b>{total}</b> in total); no lock-in hot-spots.",
    "bs_find_alts_all_one": "The only resource type has at least one alternative (<b>{total}</b> in total); no lock-in hot-spots.",
    "bs_find_alts_some": "<b>{a}</b> of <b>{n}</b> resource types have at least one alternative (<b>{total}</b> in total).",
    "bs_find_alts_some_one": "<b>1</b> of <b>{n}</b> resource types has at least one alternative (<b>{total}</b> in total).",
    "bs_find_alts_none": "No resource type has an alternative in the data catalogue: {types}.",
    "bs_find_alts_missing": "No alternative in the data catalogue: {types}.",
    "bs_find_costs": "<b>{total}</b> over six months ({average} per month on average).",
    "bs_find_costs_latest": "The latest month ({month}) was <b>{latest}</b>, {change} on {previous}.",
    "bs_find_costs_latest_only": "The latest month ({month}) was <b>{latest}</b>.",
    "bs_find_costs_rising": "Costs have risen for {k} consecutive months.",
    "bs_find_costs_falling": "Costs have fallen for {k} consecutive months.",
    "bs_find_footprint": "{resources} of {types} in {regions}.",
    "bs_count_resources_one": "<b>1</b> resource",
    "bs_count_resources_many": "<b>{n}</b> resources",
    "bs_count_types_one": "<b>1</b> type",
    "bs_count_types_many": "<b>{n}</b> types",
    "bs_count_regions_one": "<b>1</b> region",
    "bs_count_regions_many": "<b>{n}</b> regions",
    "bs_caption": "Amber marker = critical or high risks or resource types without an alternative. All figures are taken from the sections of this report and refer to run {version} (data as of {completed}); the summary adds no findings of its own.",
    "es_heading_costs_overview": "Costs Overview",
    "tbl_cost_no_data": "No cost data available for this assessment.",
    "cost_stat_total": "Total, 6 Months",
    "cost_stat_average": "Monthly Average",
    "cost_stat_latest": "Latest Month ({month})",
    "cost_stat_change": "Change vs. {month}",
    "cost_th_month": "Month",
    "cost_th_cost": "Cost ({currency})",
    "cost_th_change": "vs. Previous Month",
    "cost_total": "Total (6 months)",
    "cost_caption": "Source: provider billing data for the scope, currency {currency}. – = no cost in the previous month.",
    "cost_mixed_currency": "The cost data contains more than one currency and is not totalled.",
    "cost_provisional": "provisional",
    "cost_caption_provisional": "Provisional = the month was still accruing when the data was collected.",
    "cost_tfstate": "Terraform state files hold no billing data, so there are no costs to show for this assessment.",
    "es_cost_note": "Costs are shown for context. They do not affect the risk evaluation and are not an estimate of exit costs.",
    "es_costs_egress_note": (
        "A separate Egress & Data Landscape ({document_id}) estimates the data "
        "held in scope and the cost of moving it out."
    ),
    "es_heading_resource_inventory": "Resource Inventory",
    "es_res_lead": (
        "Cloud resources discovered in the defined scope. Each resource type "
        "appears once; regions are counted, not repeated."
    ),
    "tbl_res_no_resources": (
        "There are no resources to be found in the resource inventory."
    ),
    "res_stat_resources": "Resources",
    "res_stat_types": "Resource Types",
    "res_stat_regions": "Regions",
    "res_th_resource_type": "Resource Type",
    "res_th_regions": "Regions",
    "res_th_resources": "Resources",
    "res_th_share": "Share",
    "res_total": "Total",
    "es_res_caption": (
        "Share = resources of the type as a share of all resources in scope. "
        "Region names as reported by the provider."
    ),
    "es_heading_risk_inventory": "Risk Inventory",
    "es_risk_lead": (
        "Unique exit risks identified in the defined scope, ordered by "
        "severity. Risk codes are stable across assessments, so a finding can "
        "be followed from one run to the next."
    ),
    "tbl_risk_no_risks": "There are no risks identified in this assessment.",
    "risk_sev_critical": "Critical",
    "risk_sev_high": "High",
    "risk_sev_medium": "Medium",
    "risk_sev_low": "Low",
    "risk_stat_unique": "Unique Risks",
    "risk_stat_critical_high": "Critical + High",
    "risk_stat_affected": "Resource Types Affected",
    "risk_stat_affected_value": "{a} of {n}",
    "risk_stat_scope_wide": "Scope-wide Risks",
    "risk_bar_caption": (
        "Unique risks by severity · colour, pip count and label carry the "
        "same meaning"
    ),
    "risk_th_code": "Code",
    "risk_th_risk": "Risk",
    "risk_th_severity": "Severity",
    "risk_th_affected": "Affected Types",
    "risk_scope_wide": "Scope-wide",
    "risk_group": "{severity} · {n}",
    "es_risk_caption": (
        "Affected Types = number of discovered resource types the risk applies "
        "to. Severity levels: see the Risk Severity Levels chapter."
    ),
    "es_heading_alternative_technologies": "Alternative Technologies",
    "es_alttech_lead": (
        "Replacement options for the discovered resource types under the "
        "{strategy} exit strategy. Rows without an alternative are potential "
        "lock-in hot-spots and are listed first."
    ),
    "alt_stat_types": "Types with Alternatives",
    "alt_stat_types_value": "{a} of {n}",
    "alt_stat_alternatives": "Alternatives",
    "alt_stat_hotspots": "Lock-in Hot-spots",
    "alt_th_resource_type": "Resource Type",
    "alt_th_resources": "Resources",
    "alt_th_alternatives": "Alternatives",
    "alt_th_examples": "Examples",
    "alt_hotspot_short": "<b>No alternative in the data catalogue</b> — lock-in hot-spot",
    "alt_more": "+{k} more",
    "alt_flag_open_source": "Open source",
    "alt_flag_support_plan": "Enterprise support",
    "alt_no_resources": "No resources were discovered in this assessment, so there are no alternatives to show.",
    "alt_empty_set": "No alternative technologies were found for the discovered resource types.",
    "es_alttech_caption": "Examples are listed alphabetically; this report does not rank the alternatives. Open source and enterprise support as recorded in the data catalogue.",
    "es_heading_severity": "Risk Severity Levels",
    "es_severity_lead": (
        "Severity expresses the expected impact of a risk on the effort and "
        "complexity of an exit under the selected exit strategy. It is "
        "defined for each risk in the risk catalogue and is the same in "
        "every assessment in which the risk occurs. Where a risk depends on "
        "a threshold, the threshold is stated in the risk description (for "
        "example ‘>30 resources’)."
    ),
    "es_severity_note": (
        "Severities and thresholds are expert-based benchmarks; they are not "
        "derived from an industry standard. Severity is not a probability "
        "and does not mean that an exit is impossible."
    ),
    "es_severity_th_meaning": "Meaning",
    "es_severity_critical": (
        "Major expected impact on the exit: large parts of the scope are "
        "affected, or a significant redesign, rebuild or specialist effort "
        "is likely. Should be addressed early in exit planning."
    ),
    "es_severity_high": (
        "Significant additional effort, cost or time for the exit; a "
        "specific mitigation should be planned."
    ),
    "es_severity_medium": (
        "Moderate additional effort or complexity; manageable within normal "
        "exit planning."
    ),
    "es_severity_low": (
        "Minor expected impact; usually handled during regular migration work."
    ),
    "es_heading_glossary": "Glossary",
    "es_glossary_block": "Abbreviations and terms used in this summary.",
    "glossary": [
        {"id": "aws", "term": "AWS", "definition": "Amazon Web Services"},
        {
            "id": "data_catalogue",
            "term": "Data catalogue",
            "definition": "The catalogue of resource types, exit risks and alternative technologies used for this assessment; its version is recorded on page 2.",
        },
        {
            "id": "hotspot",
            "term": "Lock-in hot-spot",
            "definition": "A discovered resource type for which the data catalogue lists no alternative.",
        },
        {
            "id": "run",
            "term": "Run",
            "definition": "One completed assessment of the defined scope, identified by the run ID on page 2.",
        },
    ],
    "fa_heading": "What the Full Assessment Adds",
    "fa_lead": (
        "This Executive Summary is the Basic assessment. It was collected and "
        "evaluated locally and covers the resource inventory, "
        "costs, exit risks and alternative technologies of the defined scope. "
        "The full assessment on a platform adds:"
    ),
    "fa_items": [
        (
            "Exit Readiness Grade",
            "a letter grade from A to F that shows how ready the defined scope is for an exit.",
        ),
        (
            "Vendor Lock-in Score",
            "a rating of how easily each resource type can be replaced, in terms of skills, technology and operations.",
        ),
        (
            "Scored Alternatives",
            "the alternative technologies for each resource type, rated and ranked.",
        ),
    ],
    "fa_how_heading": "How to Get Them",
    "fa_how": (
        "Connect cloudexit to a platform with an API key and run the "
        "assessment again; the results are uploaded and evaluated there. For "
        "example:"
    ),
    "fa_command": "cloudexit aws --host HOST --api-key-file KEY_FILE",
    "fa_sites": "Platforms: {escapecloud} · {exitcloud}",
    "fa_heading_uploaded": "The Full Assessment",
    "fa_uploaded": (
        "This assessment was uploaded. The Exit Readiness Grade, the Vendor "
        "Lock-in Score, scored alternatives and the Egress & Data Landscape "
        "are available in its reports on the platform."
    ),
    "fa_uploaded_link": "Open the reports: {link}",
    "fa_uploaded_id": "Assessment ID: {assessment_id}",
    "eg_heading_introduction": "Introduction",
    "eg_heading_purpose": "Purpose",
    "eg_heading_scope": "Scope",
    "eg_heading_limitations": "Limitations",
    "eg_purpose_block": (
        "This document quantifies the data held by the data-bearing resources "
        "in the defined scope and estimates the one-time fee for moving that "
        "data out of the provider at list prices. The fee is one component of "
        "the cost of an exit; it is shown early so that it can be planned "
        "rather than discovered during migration. It accompanies the "
        "Executive Summary of the same run."
    ),
    "eg_scope_azure": (
        "The estimate covers the data-bearing resources in Microsoft Azure "
        "resource group {resource_group}. Resources outside this resource "
        "group are not included."
    ),
    "eg_scope_aws": (
        "The estimate covers the data-bearing resources in AWS region "
        "{region}. Other regions are not included."
    ),
    "eg_scope_plain": (
        "The estimate covers the data-bearing resources in the defined scope."
    ),
    "eg_limitations_block": (
        "Sizes are collected from provider metrics and, for some resource "
        "types, represent provisioned rather than used capacity. Resources "
        "that cannot be sized through the provider APIs are listed but "
        "excluded from the fee estimate. Fees are calculated from public list "
        "prices at the time of the run and exclude request and transaction "
        "charges, cross-region traffic, negotiated discounts and any "
        "committed-use agreements already in place."
    ),
    "eg_limitations_scope": (
        "The estimate covers data transfer fees only. It is not an estimate "
        "of the full cost of an exit, which also includes migration effort, "
        "parallel operation and contract terms. Figures are estimates and can "
        "be incomplete in some environments."
    ),
    "eg_limitations_data": (
        "Figures reflect the data of the run shown on page 2 and may change "
        "as the environment changes."
    ),
    "eg_heading_coverage": "Scope of the Estimation",
    "eg_coverage_intro": (
        "The estimate covers the following data-bearing resource types. Each "
        "note states how the size is measured, or why a type is not sized."
    ),
    "eg_th_resource_type": "Resource Type",
    "eg_th_measured": "How the Size Is Measured",
    "eg_coverage_notes_azure": [
        ("Storage Accounts", "Capacity metrics can lag by up to roughly 48 hours"),
        (
            "Managed Disks and Point-in-Time Copies",
            "Provisioned size; counted as an upper bound",
        ),
        ("SQL Databases", "Used space from database metrics"),
        ("Cosmos DB Accounts", "Used space from database metrics"),
        ("PostgreSQL / MySQL Flexible Servers", "Used space from database metrics"),
        ("Recovery Services Vaults", "Not sized; recovery points cannot be exported"),
    ],
    "eg_coverage_notes_aws": [
        ("S3 Buckets", "Metrics can lag by up to roughly 48 hours"),
        (
            "EBS Volumes and Point-in-Time Copies",
            "Upper bound; copies can over-count shared blocks",
        ),
        ("RDS Instances", "Aurora is not sized"),
        ("DynamoDB Tables", ""),
        ("AWS Backup Vaults", "Not sized; recovery points cannot be exported"),
    ],
    "eg_heading_landscape": "Data Landscape",
    "eg_no_data_notice": (
        "No data-bearing resources were identified in the defined scope, so "
        "there is no data to show and no egress fee to estimate."
    ),
    "eg_land_lead": (
        "The defined scope holds {total} of data in {sized} of {resources} "
        "data-bearing resources."
    ),
    "eg_land_lead_one": (
        "The defined scope holds {total} of data in its only data-bearing " "resource."
    ),
    "eg_land_top": "{type} holds the largest share ({size}, {share}).",
    "eg_land_unsized_one": "1 resource could not be sized and is not in the totals.",
    "eg_land_unsized_many": (
        "{count} resources could not be sized and are not in the totals."
    ),
    "eg_land_none_sized": (
        "None of the {resources} data-bearing resources could be sized."
    ),
    "eg_land_none_sized_one": "The data-bearing resource could not be sized.",
    "eg_st_volume": "Data Volume",
    "eg_st_sized": "Resources Sized",
    "eg_st_sized_value": "{sized} of {resources}",
    "eg_st_not_sized": "Not Sized",
    "eg_st_largest": "Largest Type",
    "eg_th_resources": "Resources",
    "eg_th_size_gib": "Size (GiB)",
    "eg_th_share": "Share of Volume",
    "eg_total": "Total",
    "eg_land_not_sized": "Not sized: {types}.",
    "eg_land_not_sized_one": "{type} (1 resource)",
    "eg_land_not_sized_many": "{type} ({count} resources)",
    "eg_land_caption": (
        "All sizes in GiB (1 GiB = 1,073,741,824 bytes). Some resource types "
        "report provisioned rather than used capacity; see Scope of the "
        "Estimation."
    ),
    "eg_land_source_note": (
        "Data-bearing resources are collected separately from the resource "
        "inventory of the Executive Summary and can include types it does "
        "not list, such as managed disks."
    ),
    "eg_heading_costs": "Estimated Costs",
    "eg_cost_lead_free": (
        "The one-time egress fee for the sized data is estimated at {fee}: it "
        "stays within the provider's free monthly internet egress allowance."
    ),
    "eg_cost_headroom": (
        "The sized data takes up {used} of the {allowance} monthly free tier."
    ),
    "eg_cost_lead_paid": (
        "Moving the sized data ({total}) out of the provider is estimated to "
        "cost {fee} once, at list prices."
    ),
    "eg_cost_lead_allowance": (
        "The first {allowance} per month are free; the remaining {billable} "
        "are billed at tiered rates (see the Appendix)."
    ),
    "eg_cost_lead_top": "{type} accounts for the largest share ({share}).",
    "eg_cost_lead_none": (
        "No fee can be estimated because none of the data-bearing resources "
        "could be sized."
    ),
    "eg_cost_unsized_one": "1 resource could not be sized and is not included.",
    "eg_cost_unsized_many": (
        "{count} resources could not be sized and are not included."
    ),
    "eg_st_fee": "Estimated Fee (USD)",
    "eg_st_allowance": "Free Allowance / Month",
    "eg_st_billable": "Billable Volume",
    "eg_th_fee": "Estimated Fee (USD)",
    "eg_cost_caption": (
        "Amounts in USD: the provider publishes egress list prices in USD and "
        "they are not converted. Internet egress is billed on the whole "
        "volume, so the fee of each resource type is its share of the total, "
        "allocated by size. Excludes request and transaction charges, "
        "cross-region traffic and negotiated discounts. A + means that "
        "resources without a size are left out, so the figure is a lower "
        "bound."
    ),
    "eg_heading_pricing": "Appendix – Egress Prices",
    "eg_th_component": "Component",
    "eg_th_tier": "Tier",
    "eg_th_price": "Price (USD)",
    "eg_th_valid_from": "Valid From",
    "eg_tier_all_volumes": "all volumes",
    "eg_tier_first": "first {to} {unit} / month",
    "eg_tier_over": "over {from_} {unit}",
    "eg_tier_range": "{from_} – {to} {unit}",
    "eg_price_free": "Free",
    "eg_component_internet_egress": "Internet egress",
    "eg_component_archive_retrieval": "Archive retrieval",
    "eg_pricing_intro": (
        "The estimate uses the following {provider} list prices (zone 1 – "
        "US/Europe, public internet routing), valid from {valid}."
    ),
    "eg_pricing_intro_mixed": (
        "The estimate uses the following {provider} list prices (zone 1 – "
        "US/Europe, public internet routing)."
    ),
    "eg_pricing_units": (
        "Prices per GB as published by {provider} (1 GB = 1,000,000,000 "
        "bytes). Sizes in this document are in GiB (1,073,741,824 bytes); "
        "1 GiB = 1.074 GB."
    ),
    "eg_pricing_units_gib": "Prices per GiB as published by {provider}.",
    "eg_provider_fallback": "the cloud provider",
    "eg_pricing_no_archive": (
        "Archive retrieval pricing was not available for this provider at the "
        "time of the run; rehydration fees are not included in the estimate."
    ),
    "eg_pricing_note": (
        "List prices at the time of the run; they exclude request and "
        "transaction charges and cross-region traffic."
    ),
    "eg_pricing_source": "Source: {link}",
    "eg_pricing_source_component": "Source ({component}): {link}",
    "eg_heading_glossary": "Glossary",
    "eg_glossary": [
        {
            "id": "egress",
            "term": "Egress",
            "definition": "Data that leaves a cloud provider's network, for example to the internet, an on-premises site or another provider.",
        },
        {
            "id": "free_tier",
            "term": "Free tier",
            "definition": "The monthly volume of internet egress that a provider does not charge for.",
        },
        {
            "id": "gib",
            "term": "GiB",
            "definition": "Gibibyte, 1,073,741,824 bytes. Sizes in this document are in GiB.",
        },
        {
            "id": "list_price",
            "term": "List price",
            "definition": "The price a provider publishes, before any negotiated discount or commitment.",
        },
        {
            "id": "switching",
            "term": "Switching charges",
            "definition": "Fees a provider charges for moving data or workloads to another provider, including data egress.",
        },
    ],
    "eg_heading_next_steps": "Next Steps",
    "eg_next_lead": (
        "Use this estimate together with the Executive Summary of the same "
        "run ({executive_id}) when planning an exit."
    ),
    "eg_next_platforms": (
        "Connected ({exitcloud_url}) and Enterprise ({book_demo_url}) combine "
        "this estimate with the Exit Readiness Report and version history."
    ),
    "eg_next_uploaded": (
        "This run was uploaded. The platform combines this estimate with the "
        "Exit Readiness Report and version history."
    ),
    "notices_egress": (
        "Egress figures are estimates based on the provider's public list "
        "prices at the time of the run. Actual invoices can differ: "
        "negotiated discounts and commitments, request charges, cross-region "
        "traffic and private connections are not included. Under the EU Data "
        "Act, providers may charge only reduced, cost-based fees for "
        "switching, including data egress, until 12 January 2027, and no "
        "switching charges after that date. Several providers already waive "
        "egress fees on request for customers who leave. This estimate "
        "assumes no waiver."
    ),
    "notices_heading": "Legal Notices",
    "notices_disclaimer_heading": "Disclaimer",
    "notices_disclaimer": (
        "EscapeCloud Community Edition - This report is provided 'As Is,' "
        "without any warranty of any kind. EscapeCloud makes no warranty that "
        "the information contained in this report is complete or error-free."
    ),
    "notices_generated": (
        "This report was generated by cloudexit from data collected with the "
        "organisation's own credentials. Its content was not reviewed or "
        "verified by EscapeCloud."
    ),
    "notices_purpose": (
        "It is provided for information and planning purposes. It is not "
        "legal, financial or investment advice, an audit or an exit plan, and "
        "it does not replace independent technical validation. The "
        "organisation remains responsible for its own exit planning."
    ),
    "notices_figures": (
        "Costs are taken from the provider's billing data for the scope. "
        "Results reflect the data of the run shown on page 2 and may change as "
        "the environment changes. Alternatives are entries from the data "
        "catalogue, not recommendations."
    ),
    "notices_trademarks_heading": "Trademarks",
    "notices_trademark_own": (
        "EscapeCloud® is a registered trademark in the European Union and the "
        "United States. The AGPL-3.0 licence applies solely to the cloudexit "
        "software, not to the EscapeCloud name, logo, or brand assets. You may "
        "not use EscapeCloud trademarks in connection with modified versions or "
        "downstream distributions in any manner that implies endorsement, "
        "affiliation, or origin with EscapeCloud."
    ),
    "notices_trademark_others": (
        "Other product and company names and logos are trademarks of their "
        "respective owners; naming them does not imply endorsement or "
        "affiliation."
    ),
    "notices_icons_aws": (
        "AWS service icons are taken from the AWS Architecture Icons "
        "({icons_url}); their use is subject to the terms published there and "
        "in the AWS Trademark Guidelines ({guidelines_url}). Amazon Web "
        "Services and AWS are trademarks of Amazon.com, Inc. or its "
        "affiliates. This report is not endorsed by or affiliated with Amazon "
        "Web Services."
    ),
    "notices_icons_azure": (
        "Azure service icons are taken from the Azure Architecture Icons "
        "({icons_url}); their use is subject to Microsoft's icon terms "
        "published there. Microsoft and Azure are trademarks of the Microsoft "
        "group of companies. This report is not endorsed by or affiliated with "
        "Microsoft."
    ),
    "notices_software_heading": "Software",
    "notices_software": (
        "cloudexit is free software, licensed under the GNU Affero General "
        "Public License v3.0 (AGPL-3.0). Source code: {repository_url}. "
        "Licence: {licence_url}. The provider icons are not covered by this "
        "licence."
    ),
    "notices_copyright": "© 2024–{year} EscapeCloud",
    "html_help": "Help",
    "html_help_aria": "Help: how to read this report",
    "html_logo_link": "EscapeCloud website (opens in a new tab)",
    "html_chip_self": "Self-collected",
    "html_chip_self_title": "Collected and evaluated on this machine; not uploaded",
    "html_chip_uploaded": "Uploaded",
    "html_chip_uploaded_title": "Collected on this machine and uploaded to the platform",
    "html_download": "Download",
    "html_download_summary": "Executive Summary (PDF)",
    "html_download_egress": "Egress & Data Landscape (PDF)",
    "html_open_platform": "Open on the platform",
    "html_nav_label": "Sections",
    "html_nav_overview": "Overview",
    "html_nav_scores": "Scores",
    "html_nav_risks": "Risks",
    "html_nav_about": "About This Report",
    "html_lock_label": "Light and Enterprise",
    "html_scope_label": "Scope",
    "html_run_label": "Run",
    "html_run_small": "run {run} · {source}",
    "html_run_live": "provider APIs",
    "html_run_tfstate": "Terraform state",
    "html_scope_no_resources": "no resources found",
    "html_scope_serial": "serial {serial}",
    "html_catalogue_sha": "sha256 {sha}",
    "html_risks_label": "Unique Risks",
    "html_sev_aria": "Risks by severity: {counts}",
    "html_costs_label": "Costs, Last 6 Months",
    "html_costs_label_short": "Costs",
    "html_costs_average": "{amount} per month on average",
    "html_costs_none": "no costs in the last six months",
    "html_costs_change": "{latest} vs. {previous}",
    "html_costs_aria": "Monthly costs: {parts}",
    "html_costs_aria_part": "{month} {amount}",
    "html_costs_aria_provisional": "{month} {amount} provisional",
    "html_costs_provisional": (
        "* {month} is provisional: the month was still accruing. Costs are "
        "context only; they are not an exit-cost estimate."
    ),
    "html_costs_context": "Costs are context only; they are not an exit-cost estimate.",
    "html_costs_tfstate_hint": (
        "Run cloudexit against the provider APIs to include the last six "
        "months of costs."
    ),
    "html_months": [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ],
    "html_scores_grade": "Exit Readiness Grade",
    "html_scores_lock": "Vendor Lock-in Score",
    "html_scores_pill": "Light & Enterprise",
    "html_scores_scale": "of 100",
    "html_scores_bands": ["F", "E", "D", "C", "B", "A"],
    "html_scores_dims": ["Human", "Technology", "Operational"],
    "html_scores_dim_value": "{dash} / 5",
    "html_scores_grade_text": (
        "A grade from A to F for how ready this scope is for an exit, computed "
        "from the risks in this report."
    ),
    "html_scores_lock_text": (
        "How easily each resource type can be replaced, in skills, technology "
        "and operations, with the {alternatives} rated and ranked."
    ),
    "html_scores_lock_text_plain": (
        "How easily each resource type can be replaced, in skills, technology "
        "and operations, with the alternatives rated and ranked."
    ),
    "html_scores_on_platform": "For this run it is on the platform.",
    "html_platform_not_uploaded": "Not uploaded: {reason}",
    "html_hint_title": "EscapeCloud Light & Enterprise",
    "html_hint_text": (
        "EscapeCloud's scoring engine offers several scoring methods to assess "
        "how feasible an exit is, and tracks the continuously changing vendor "
        "landscape of alternative technologies."
    ),
    "html_site_exitcloud": "exitcloud.io",
    "html_site_escapecloud": "escapecloud.io",
    "html_risks_caption": "{count} · {scope} · ordered by severity",
    "html_risks_caption_only_count": "{count} · ordered by severity",
    "html_risks_count_one": "1 unique risk",
    "html_risks_count_many": "{n} unique risks",
    "html_risks_wide_sole": "applies to the scope as a whole",
    "html_risks_wide_all": "all apply to the scope as a whole",
    "html_risks_wide_one": "1 applies to the scope as a whole",
    "html_risks_wide_many": "{n} apply to the scope as a whole",
    "html_risks_none_caption": "No exit risks",
    "html_risks_th_affected": "Affected Resource Types",
    "html_risks_more": "+{n} more",
    "html_risks_footnote": (
        "Risk codes are stable across runs. Severity levels and their meaning: "
        "{link}."
    ),
    "html_risks_empty_scope": (
        "With no resources in the scope, no risk from the data catalogue applies."
    ),
    "html_risks_empty_clean": (
        "Every resource type has enough alternatives with enterprise support, "
        "and the size of the scope is below the thresholds."
    ),
    "html_inv_caption": "{resources} · {types} · {regions}",
    "html_inv_regions": "{regions} ({shares})",
    "html_inv_not_managed": "resources that Terraform does not manage are not assessed",
    "html_inv_th_locations": "Locations",
    "html_inv_empty_title": "No resources were found in the defined scope.",
    "html_inv_empty_resource_group": (
        "Check that the resource group {scope} is the intended scope and that "
        "the credentials can read it. Resource types not in the data catalogue "
        "are not counted."
    ),
    "html_inv_empty_region": (
        "Check that the region {scope} is the intended scope and that the "
        "credentials can read it. Resource types not in the data catalogue are "
        "not counted."
    ),
    "html_inv_empty_tfstate": (
        "Check that the state file {scope} holds resources of this provider. "
        "Resource types not in the data catalogue are not counted."
    ),
    "html_alt_caption": "{alternatives} for {with_alternatives} of {types} resource types",
    "html_alt_caption_one_type": "{alternatives} for {with_alternatives} of 1 resource type",
    "html_alt_n_one": "1 alternative",
    "html_alt_n_many": "{n} alternatives",
    "html_alt_search": "Search alternatives",
    "html_filter_os": "Open Source",
    "html_filter_sp": "Enterprise Support",
    "html_filter_eu": "European Vendors",
    "html_alt_th_name": "Alternative",
    "html_alt_meta": "{resources} · {alternatives}",
    "html_alt_meta_none": "{resources} · no alternative in the data catalogue",
    "html_alt_more_one": "Show 1 more alternative",
    "html_alt_more_many": "Show {n} more alternatives",
    "html_alt_no_match": "No alternative matches the search and filters.",
    "html_alt_footnote": (
        "Alternatives are listed alphabetically from the data catalogue "
        "({version}); this report doesn't rank them. Open source and "
        "enterprise support as recorded in the data catalogue. Country = "
        "vendor's registered country."
    ),
    "html_alt_empty_title": "No resource types to list alternatives for.",
    "html_alt_empty_text": (
        "Alternatives appear here for each resource type found in the scope."
    ),
    "html_yes": "Yes",
    "html_no": "No",
    "html_eg_caption": (
        "Data held by the data-bearing resources, and the one-off fee to "
        "transfer it out at list prices · also as PDF {document_id}"
    ),
    "html_eg_kpi_fee": "Estimated Transfer Fee",
    "html_eg_kpi_headroom": "Free-Tier Headroom",
    "html_eg_fee_free": "within the monthly free tier · USD list prices",
    "html_eg_fee_paid": "at list prices · USD",
    "html_eg_types_sized": "{sized} of {types} types sized",
    "html_eg_headroom_left": "of {limit} per month left",
    "html_eg_headroom_aria": "{share} of the free tier used",
    "html_eg_billable_caption": "no free tier in the prices used",
    "html_eg_not_sized_caption": "listed, not in the fee",
    "html_eg_not_sized": "not sized",
    "html_eg_vol_aria": "Share of data volume: {parts}",
    "html_eg_vol_part": "{type} {share}",
    "html_eg_vol_other": "Other types",
    "html_eg_vol_unsized": "{types} not sized",
    "html_eg_units_gb": (
        "Data is shown in GiB; {provider} prices egress per GB (1 GiB = 1.074 "
        "GB), so {gib} GiB is {gb} GB of the {limit} monthly free tier."
    ),
    "html_eg_units_gb_plain": (
        "Data is shown in GiB; {provider} prices egress per GB (1 GiB = 1.074 GB)."
    ),
    "html_eg_units_gib": "Data is shown in GiB; {provider} prices egress per GiB.",
    "html_eg_amounts": "Amounts in USD list prices; not converted.",
    "html_eg_amounts_costs": (
        "Amounts in USD list prices; not converted and not added to the costs "
        "above ({currency}, monthly)."
    ),
    "html_eg_scope_note": (
        "Network transfer fees only: request charges, cross-region traffic, "
        "private connections and negotiated discounts are not included. Some "
        "types report provisioned rather than used capacity. Not an estimate "
        "of the full cost of an exit."
    ),
    "html_eg_sized_summary": "What is sized, and how",
    "html_eg_prices_summary": "Prices used ({provider}, zone 1, public internet routing)",
    "html_about_caption": (
        "Document, scope and provenance · method, limitations, severity levels "
        "and terms"
    ),
    "html_about_document": "Document",
    "html_about_purpose": "Purpose and Method",
    "html_about_phases_meta": "{n} phases",
    "html_about_levels_meta": "{n} levels",
    "html_about_terms_meta": "{n} terms",
    "html_method_lead": (
        "The assessment was collected and evaluated locally with cloudexit. "
        "Once the scope is defined, it runs three phases:"
    ),
    "html_method_discovery_tfstate": (
        "reads the resources managed in the Terraform state and classifies "
        "them by type and region; a state file holds no costs."
    ),
    "html_method_catalogue": (
        "Risks and alternatives are taken from the data catalogue version shown "
        "above, so the results of this run can be reproduced."
    ),
    "html_limitations_data": (
        "The analysis assumes that the discovered resources are correctly "
        "configured and accessible. Dependencies outside the defined scope, "
        "such as on-premises systems or other cloud accounts, are not covered; "
        "results are indicative, not exhaustive."
    ),
    "html_glossary": [
        {
            "id": "aws",
            "term": "AWS",
            "definition": "Amazon Web Services",
        },
        {
            "id": "data_catalogue",
            "term": "Data catalogue",
            "definition": "The catalogue of resource types, exit risks and alternative technologies used for this assessment.",
        },
        {
            "id": "hotspot",
            "term": "Lock-in hot-spot",
            "definition": "A discovered resource type for which the data catalogue lists no alternative.",
        },
        {
            "id": "run",
            "term": "Run",
            "definition": "One completed assessment of the defined scope, identified by its run ID.",
        },
        {
            "id": "egress",
            "term": "Egress",
            "definition": "Data transferred out of the provider's network, which the provider bills per unit of volume.",
        },
        {
            "id": "free_tier",
            "term": "Free tier",
            "definition": "The volume of internet egress per month that the provider does not bill.",
        },
        {
            "id": "gib_gb",
            "term": "GiB / GB",
            "definition": "Gibibyte (2³⁰ bytes) and gigabyte (10⁹ bytes); 1 GiB = 1.074 GB.",
        },
    ],
    "html_notice_advice": (
        "It is not legal, financial or investment advice, an audit or an exit plan."
    ),
    "html_label_aws_icons": "AWS Architecture Icons",
    "html_label_aws_guidelines": "AWS Trademark Guidelines",
    "html_label_azure_icons": "Azure Architecture Icons",
    "html_label_repository": "github.com/escapecloud/cloudexit",
    "html_figures_heading": "Figures",
    "html_figures": (
        "Costs come from the provider's billing data for the scope. "
        "Alternatives are entries from the data catalogue, not recommendations."
    ),
    "html_figures_egress": (
        "Costs come from the provider's billing data for the scope; egress "
        "prices are public list prices at the time of the run. Alternatives "
        "are entries from the data catalogue, not recommendations."
    ),
    "html_figures_tfstate": (
        "Resources are read from the Terraform state file; a state file holds "
        "no billing data, so this report shows no costs. Alternatives are "
        "entries from the data catalogue, not recommendations."
    ),
    "html_software": (
        "cloudexit is free software, licensed under the GNU Affero General "
        "Public License v3.0 (AGPL-3.0). Source code and issues: "
        "{repository_link}. The provider icons are not covered by this "
        "licence. Fonts: IBM Plex, SIL Open Font License 1.1. Guided tour: "
        "driver.js, MIT License, included in this report's assets."
    ),
    "html_footer_ids": "{document_id} · cloudexit {version}",
    "html_tour_next": "Next",
    "html_tour_back": "Back",
    "html_tour_done": "Done",
    "html_tour_progress": "{{current}} of {{total}}",
    "html_tour_sections_title": "Sections",
    "html_tour_sections": (
        "Jump to any part of the report; the section in view is highlighted."
    ),
    "html_tour_sections_lock": (
        "Scores, marked with a lock, are available in EscapeCloud Light & "
        "Enterprise."
    ),
    "html_tour_report_title": "This Report",
    "html_tour_report": "The assessed scope and its cloud provider.",
    "html_tour_report_platform": (
        "This run was uploaded: Open on the platform shows its full results."
    ),
    "html_tour_download_both": (
        "Download has the same results as PDF: the Executive Summary and the "
        "Egress & Data Landscape."
    ),
    "html_tour_download_both_platform": (
        "Download has the Executive Summary and the Egress & Data Landscape as " "PDF."
    ),
    "html_tour_download_one": "Download has the Executive Summary as PDF.",
    "html_tour_overview_title": "Overview",
    "html_tour_overview": (
        "Scope, unique risks by severity and the last six months of costs. The "
        "current month is provisional while it is still accruing; costs are "
        "context, not an exit-cost estimate."
    ),
    "html_tour_overview_tfstate": (
        "Scope and unique risks by severity. A Terraform state holds no "
        "billing data, so there are no costs; resources that Terraform does "
        "not manage are not assessed."
    ),
    "html_tour_scores_title": "Exit Readiness Grade & Vendor Lock-in Score",
    "html_tour_scores_available": "Available in EscapeCloud Light & Enterprise.",
    "html_tour_scores_platform": (
        "This run was uploaded. Its Exit Readiness Grade and Vendor Lock-in "
        "Score are on the platform: use Open on the platform. This local "
        "report never shows platform scores."
    ),
    "html_tour_risks_title": "Risks",
    "html_tour_risks": (
        "Exit risks found in the scope, ordered by severity. Each risk has a "
        "stable code, so a finding can be followed from one run to the next. "
        "Open a row for its description."
    ),
    "html_tour_inventory_title": "Resource Inventory",
    "html_tour_inventory": (
        "One row per resource type, with its regions and its share of all "
        "resources in the scope."
    ),
    "html_tour_alternatives_title": "Alternative Technologies",
    "html_tour_alternatives": (
        "Replacement options from the data catalogue for each resource type, "
        "listed alphabetically and not ranked. Search, or filter by open "
        "source, enterprise support or European vendors."
    ),
    "html_tour_egress_title": "Egress & Data Landscape",
    "html_tour_egress": (
        "Data held in the scope and the one-off fee to transfer it out at "
        "public list prices. Data is shown in GiB; the provider prices per GB. "
        "Types marked 'not sized' are listed but not in the fee."
    ),
    "html_tour_about_title": "About This Report",
    "html_tour_about": (
        "Document ID, scope and provenance. The run ID and the data catalogue "
        "version make the result reproducible; method, limitations, severity "
        "levels and terms are below."
    ),
}

# Labels and headings that follow English Title Case, for the wording test.
TITLE_CASE_KEYS = (
    "ev_document_information_heading",
    "ev_assessment_scope_heading",
    "ev_provenance_heading",
    "ev_document_id",
    "ev_document_type",
    "ev_collected_by",
    "ev_collected_at",
    "ev_generated_at",
    "ev_cloud_provider",
    "ev_catalogue_label",
    "ev_run_id",
    "ev_platform_label",
    "cover_title_executive_summary",
    "cover_title_egress",
    "ev_egress_report",
    "ev_companion_document",
    "eg_heading_introduction",
    "eg_heading_purpose",
    "eg_heading_scope",
    "eg_heading_limitations",
    "eg_heading_coverage",
    "eg_heading_landscape",
    "eg_heading_costs",
    "eg_heading_pricing",
    "eg_heading_glossary",
    "eg_heading_next_steps",
    "eg_th_resource_type",
    "eg_th_measured",
    "eg_th_resources",
    "eg_th_size_gib",
    "eg_th_share",
    "eg_th_fee",
    "eg_th_component",
    "eg_th_tier",
    "eg_th_price",
    "eg_th_valid_from",
    "eg_total",
    "eg_st_volume",
    "eg_st_sized",
    "eg_st_not_sized",
    "eg_st_largest",
    "eg_st_fee",
    "eg_st_allowance",
    "eg_st_billable",
    "cover_doc_id",
    "cover_edition",
    "cover_edition_value",
    "footer_edition",
    "notices_heading",
    "notices_disclaimer_heading",
    "notices_trademarks_heading",
    "notices_software_heading",
    "cover_exit_strategy",
    "cover_run",
    "es_heading_introduction",
    "es_heading_purpose",
    "es_heading_scope",
    "es_heading_method",
    "es_heading_limitations",
    "bs_heading",
    "bs_findings_heading",
    "bs_th_area",
    "bs_th_finding",
    "bs_th_details",
    "bs_area_risks",
    "bs_area_alternatives",
    "bs_area_costs",
    "bs_area_footprint",
    "bs_stat_costs",
    "es_heading_costs_overview",
    "cost_stat_total",
    "cost_stat_average",
    "cost_stat_latest",
    "cost_stat_change",
    "cost_th_month",
    "cost_th_cost",
    "cost_th_change",
    "es_heading_resource_inventory",
    "res_stat_resources",
    "res_stat_types",
    "res_stat_regions",
    "res_th_resource_type",
    "res_th_regions",
    "res_th_resources",
    "res_th_share",
    "res_total",
    "es_heading_risk_inventory",
    "risk_stat_unique",
    "risk_stat_critical_high",
    "risk_stat_affected",
    "risk_stat_scope_wide",
    "risk_th_code",
    "risk_th_risk",
    "risk_th_severity",
    "risk_th_affected",
    "es_heading_alternative_technologies",
    "alt_stat_types",
    "alt_stat_alternatives",
    "alt_stat_hotspots",
    "alt_th_resource_type",
    "alt_th_resources",
    "alt_th_alternatives",
    "alt_th_examples",
    "es_heading_severity",
    "es_severity_th_meaning",
    "es_heading_glossary",
    "fa_heading",
    "fa_how_heading",
    "fa_heading_uploaded",
    "html_download",
    "html_download_summary",
    "html_download_egress",
    "html_nav_overview",
    "html_nav_scores",
    "html_nav_risks",
    "html_nav_about",
    "html_scope_label",
    "html_risks_label",
    "html_costs_label",
    "html_costs_label_short",
    "html_scores_grade",
    "html_scores_lock",
    "html_scores_pill",
    "html_risks_th_affected",
    "html_inv_th_locations",
    "html_filter_os",
    "html_filter_sp",
    "html_filter_eu",
    "html_alt_th_name",
    "html_eg_kpi_fee",
    "html_eg_kpi_headroom",
    "html_about_document",
    "html_about_purpose",
    "html_figures_heading",
    "html_tour_sections_title",
    "html_tour_report_title",
    "html_tour_overview_title",
    "html_tour_scores_title",
    "html_tour_risks_title",
    "html_tour_inventory_title",
    "html_tour_alternatives_title",
    "html_tour_egress_title",
    "html_tour_about_title",
    "html_nav_label",
    "html_run_label",
    "html_help",
)
