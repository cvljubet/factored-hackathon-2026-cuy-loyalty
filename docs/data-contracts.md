# Data contracts

Generated from `data/pipelines/glue/contracts.py` (Pandera schemas built from the data dictionary and
`dq_rules.py`); regenerate with `python data/pipelines/glue/contracts.py > docs/data-contracts.md`.

Every silver table is validated against its contract on each run (`silver/_contract_report/`), and
rows breaking it get `dq_invalid_<kind>` flags. Types are the ones silver writes. Silver also adds
columns that aren't part of the contract: `dq_*` flags, `<column>_imputed`, derived columns.

## customers

Primary key: `customer_id`.
Table checks: `unique(document_number)`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `customer_id` | StringType() | yes |  |
| `document_number` | StringType() | yes |  |
| `document_type` | StringType() | yes |  |
| `first_name` | StringType() | yes |  |
| `last_name` | StringType() | yes |  |
| `date_of_birth` | DateType() | yes |  |
| `gender` | StringType() |  |  |
| `email` | StringType() |  |  |
| `mobile_phone` | StringType() |  |  |
| `landline_phone` | StringType() |  |  |
| `address` | StringType() |  |  |
| `city` | StringType() | yes |  |
| `state` | StringType() | yes |  |
| `country` | StringType() | yes | one of México, Colombia, Argentina |
| `postal_code` | StringType() |  |  |
| `detected_accent` | StringType() |  |  |
| `segment` | StringType() | yes | one of Premium, Plus, Basic, Student |
| `credit_score` | IntegerType() |  | 300 to 850 |
| `estimated_monthly_income` | DoubleType() |  | >= 0 |
| `occupation` | StringType() |  |  |
| `marital_status` | StringType() |  |  |
| `education_level` | StringType() |  |  |
| `registration_date` | TimestampType() | yes |  |
| `registration_branch_id` | StringType() | yes |  |
| `customer_status` | StringType() | yes | one of Active, Inactive, Suspended, Closed |
| `last_updated` | TimestampType() | yes |  |
| `accepts_marketing` | BooleanType() | yes |  |

## products

Primary key: `product_id`.
Table checks: `opening_date <= expiration_date`; `unique(product_number)`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `product_id` | StringType() | yes |  |
| `customer_id` | StringType() | yes |  |
| `product_type` | StringType() | yes | one of Cuenta Ahorro, Cuenta Corriente, Tarjeta Crédito, Tarjeta Débito, Préstamo Personal, Préstamo Hipotecario, Inversión, Seguro |
| `product_number` | StringType() | yes |  |
| `currency` | StringType() | yes | one of ARS, COP, MXN, USD |
| `current_balance` | DecimalType(20,8) | yes | >= 0 |
| `credit_limit` | DoubleType() |  | >= 0 |
| `interest_rate` | DoubleType() |  | 0 to 100 |
| `opening_date` | DateType() | yes |  |
| `expiration_date` | DateType() |  |  |
| `opening_branch_id` | StringType() | yes |  |
| `product_status` | StringType() | yes | one of Active, Blocked, Closed, Suspended |
| `opening_channel` | StringType() | yes | one of Branch, Web, App, Call Center |
| `has_linked_app` | BooleanType() | yes |  |
| `days_past_due` | IntegerType() |  | >= 0 |
| `last_transaction_date` | TimestampType() |  |  |
| `last_updated` | TimestampType() | yes |  |

## branches

Primary key: `branch_id`.
Table checks: `unique(branch_code)`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `branch_id` | StringType() | yes |  |
| `branch_code` | StringType() | yes |  |
| `branch_name` | StringType() | yes |  |
| `branch_type` | StringType() | yes |  |
| `address` | StringType() | yes |  |
| `city` | StringType() | yes |  |
| `state` | StringType() | yes |  |
| `country` | StringType() | yes | one of México, Colombia, Argentina |
| `postal_code` | StringType() |  |  |
| `geographic_zone` | StringType() | yes |  |
| `phone` | StringType() | yes |  |
| `email` | StringType() |  |  |
| `opening_time` | StringType() | yes |  |
| `closing_time` | StringType() | yes |  |
| `has_atms` | BooleanType() | yes |  |
| `atm_count` | IntegerType() |  | >= 0 |
| `has_teller_windows` | BooleanType() | yes |  |
| `teller_window_count` | IntegerType() |  | >= 0 |
| `latitude` | DoubleType() |  |  |
| `longitude` | DoubleType() |  |  |
| `branch_opening_date` | DateType() | yes |  |
| `branch_status` | StringType() | yes | one of Active, Temporarily Closed, Closed |

## service_agents

Primary key: `agent_id`.
Table checks: `unique(employee_code)`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `agent_id` | StringType() | yes |  |
| `employee_code` | StringType() | yes |  |
| `first_name` | StringType() | yes |  |
| `last_name` | StringType() | yes |  |
| `email` | StringType() | yes |  |
| `phone` | StringType() |  |  |
| `native_accent` | StringType() | yes |  |
| `country_of_origin` | StringType() | yes | one of México, Colombia, Argentina |
| `assigned_branch_id` | StringType() |  |  |
| `agent_type` | StringType() | yes |  |
| `experience_level` | StringType() | yes |  |
| `languages` | StringType() | yes |  |
| `specialty` | StringType() |  |  |
| `hire_date` | DateType() | yes |  |
| `avg_csat` | DoubleType() |  | 1 to 5 |
| `total_monthly_interactions` | IntegerType() |  | >= 0 |
| `agent_status` | StringType() | yes | one of Active, Vacation, Leave, Inactive |
| `work_shift` | StringType() | yes |  |

## marketing_campaigns

Primary key: `campaign_id`.
Table checks: `start_date <= end_date`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `campaign_id` | StringType() | yes |  |
| `campaign_name` | StringType() | yes |  |
| `description` | StringType() |  |  |
| `campaign_type` | StringType() | yes | one of Email, SMS, Push, WhatsApp, Voice, Mix |
| `campaign_objective` | StringType() | yes |  |
| `promoted_product` | StringType() |  |  |
| `target_segment` | StringType() |  | one of Premium, Plus, Basic, Student |
| `target_country` | StringType() |  | one of México, Colombia, Argentina |
| `start_date` | DateType() | yes |  |
| `end_date` | DateType() | yes |  |
| `budget` | DecimalType(20,8) |  | >= 0 |
| `campaign_status` | StringType() | yes | one of Planned, Active, Paused, Completed |
| `expected_conversion_rate` | DoubleType() |  | 0 to 100 |

## campaign_sends

Primary key: `send_id`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `send_id` | StringType() | yes |  |
| `send_date` | TimestampType() | yes | between 2023-06-17 and 2026-06-17, business day from 06:00:00 |
| `process_date` | DateType() | yes |  |
| `campaign_id` | StringType() | yes |  |
| `customer_id` | StringType() | yes |  |
| `send_channel` | StringType() | yes | one of Email, SMS, Push, WhatsApp, Voice |
| `template_used` | StringType() |  |  |
| `subject` | StringType() |  |  |
| `send_status` | StringType() | yes | one of Sent, Failed, Bounced, Blocked |
| `was_delivered` | BooleanType() | yes |  |
| `was_opened` | BooleanType() |  |  |
| `open_date` | TimestampType() |  |  |
| `was_clicked` | BooleanType() |  |  |
| `click_date` | TimestampType() |  |  |
| `click_count` | IntegerType() |  | >= 0 |
| `had_conversion` | BooleanType() | yes |  |
| `conversion_date` | TimestampType() |  |  |
| `conversion_value` | DecimalType(20,8) |  | >= 0 |
| `open_device` | StringType() |  |  |
| `open_country` | StringType() |  | one of México, Colombia, Argentina |
| `failure_reason` | StringType() |  |  |
| `send_cost` | DecimalType(20,8) |  | >= 0 |

## transactions

Primary key: `transaction_id`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `transaction_id` | StringType() | yes |  |
| `transaction_date` | TimestampType() | yes | between 2023-06-17 and 2026-06-17, business day from 06:00:00 |
| `process_date` | DateType() | yes |  |
| `product_id` | StringType() | yes |  |
| `customer_id` | StringType() | yes |  |
| `transaction_type` | StringType() | yes |  |
| `transaction_category` | StringType() |  |  |
| `amount` | DecimalType(20,8) | yes | >= 0 |
| `currency` | StringType() | yes | one of ARS, COP, MXN, USD |
| `amount_usd` | DoubleType() |  | >= 0 |
| `channel` | StringType() | yes | one of ATM, Branch, Web, App, POS, Transfer |
| `branch_id` | StringType() |  |  |
| `merchant_name` | StringType() |  |  |
| `merchant_category` | StringType() |  |  |
| `transaction_country` | StringType() | yes | one of México, Colombia, Argentina, USA, Spain, Brazil |
| `transaction_city` | StringType() |  |  |
| `transaction_status` | StringType() | yes | one of Approved, Declined, Pending, Reversed |
| `response_code` | StringType() |  |  |
| `is_fraud` | BooleanType() | yes |  |
| `fraud_score` | DoubleType() |  | 0 to 100 |
| `latitude` | DoubleType() |  |  |
| `longitude` | DoubleType() |  |  |

## call_center_interactions

Primary key: `interaction_id`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `interaction_id` | StringType() | yes |  |
| `interaction_date` | TimestampType() | yes | between 2023-06-17 and 2026-06-17, business day from 08:00:00 |
| `process_date` | DateType() | yes |  |
| `customer_id` | StringType() | yes |  |
| `agent_id` | StringType() |  |  |
| `interaction_type` | StringType() | yes |  |
| `channel` | StringType() | yes | one of Phone, Web Chat, WhatsApp, Email, App, Web |
| `contact_reason` | StringType() | yes |  |
| `duration_seconds` | IntegerType() |  | >= 0 |
| `wait_time_seconds` | IntegerType() |  | >= 0 |
| `was_resolved` | BooleanType() |  |  |
| `requires_followup` | BooleanType() | yes |  |
| `detected_sentiment` | StringType() |  |  |
| `sentiment_score` | DoubleType() |  | -1 to 1 |
| `customer_detected_accent` | StringType() |  |  |
| `agent_used_accent` | StringType() |  |  |
| `was_escalated` | BooleanType() | yes |  |
| `mentioned_products` | StringType() |  |  |
| `has_transcript` | BooleanType() | yes |  |
| `has_recording` | BooleanType() | yes |  |

## call_transcripts

Primary key: `transcript_id`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `transcript_id` | StringType() | yes |  |
| `interaction_id` | StringType() | yes |  |
| `process_date` | DateType() | yes |  |
| `customer_id` | StringType() | yes |  |
| `agent_id` | StringType() | yes |  |
| `full_text` | StringType() | yes |  |
| `customer_text` | StringType() |  |  |
| `agent_text` | StringType() |  |  |
| `detected_language` | StringType() | yes |  |
| `detected_accent` | StringType() |  |  |
| `accent_confidence` | DoubleType() |  | 0 to 1 |
| `detected_keywords` | StringType() |  |  |
| `mentioned_entities` | StringType() |  |  |
| `detected_intents` | StringType() |  |  |
| `main_topics` | StringType() |  |  |
| `transcription_model` | StringType() | yes |  |
| `audio_quality` | StringType() |  |  |
| `duration_seconds` | IntegerType() | yes | >= 0 |

## satisfaction_surveys

Primary key: `survey_id`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `survey_id` | StringType() | yes |  |
| `survey_date` | TimestampType() | yes | between 2023-06-17 and 2026-06-17 |
| `process_date` | DateType() | yes |  |
| `interaction_id` | StringType() |  |  |
| `customer_id` | StringType() | yes |  |
| `agent_id` | StringType() |  |  |
| `survey_type` | StringType() | yes | one of CSAT, NPS, CES |
| `send_channel` | StringType() | yes | one of Email, SMS, IVR, App, Web |
| `main_score` | IntegerType() | yes |  |
| `nps_category` | StringType() |  |  |
| `question_1_text` | StringType() |  |  |
| `question_1_response` | IntegerType() |  | 1 to 5 |
| `question_2_text` | StringType() |  |  |
| `question_2_response` | IntegerType() |  | 1 to 5 |
| `question_3_text` | StringType() |  |  |
| `question_3_response` | IntegerType() |  | 1 to 5 |
| `open_comments` | StringType() |  |  |
| `comment_sentiment` | StringType() |  |  |
| `response_time_hours` | DoubleType() |  | >= 0 |
| `campaign_response_rate` | DoubleType() |  | 0 to 100 |

## digital_events

Primary key: `event_id`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `event_id` | StringType() | yes |  |
| `event_date` | TimestampType() | yes | between 2023-06-17 and 2026-06-17, business day from 06:00:00 |
| `process_date` | DateType() | yes |  |
| `customer_id` | StringType() |  |  |
| `session_id` | StringType() | yes |  |
| `event_type` | StringType() | yes |  |
| `event_category` | StringType() | yes |  |
| `channel` | StringType() | yes | one of Android App, iOS App, Desktop Web, Mobile Web |
| `platform` | StringType() |  |  |
| `browser` | StringType() |  |  |
| `app_version` | StringType() |  |  |
| `page_url` | StringType() |  |  |
| `page_title` | StringType() |  |  |
| `action` | StringType() |  |  |
| `element_id` | StringType() |  |  |
| `product_id` | StringType() |  |  |
| `event_value` | DoubleType() |  | >= 0 |
| `duration_seconds` | IntegerType() |  | >= 0 |
| `ip_address` | StringType() |  |  |
| `ip_country` | StringType() |  | one of México, Colombia, Argentina |
| `ip_city` | StringType() |  |  |
| `is_mobile` | BooleanType() | yes |  |
| `referrer` | StringType() |  |  |
| `utm_source` | StringType() |  |  |
| `utm_medium` | StringType() |  |  |
| `utm_campaign` | StringType() |  |  |

## complaints

Primary key: `complaint_id`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `complaint_id` | StringType() | yes |  |
| `creation_date` | TimestampType() | yes | between 2023-06-17 and 2026-06-17, business day from 08:00:00 |
| `process_date` | DateType() | yes |  |
| `customer_id` | StringType() | yes |  |
| `case_type` | StringType() | yes | one of Complaint, Claim, Request, Suggestion |
| `category` | StringType() | yes |  |
| `subcategory` | StringType() |  |  |
| `reception_channel` | StringType() | yes | one of Call Center, Email, Web, App, Branch, Regulator |
| `affected_product_id` | StringType() |  |  |
| `related_branch_id` | StringType() |  |  |
| `origin_interaction_id` | StringType() |  |  |
| `description` | StringType() | yes |  |
| `claimed_amount` | DecimalType(20,8) |  | >= 0 |
| `currency` | StringType() |  | one of ARS, COP, MXN, USD |
| `priority` | StringType() | yes |  |
| `status` | StringType() | yes | one of Open, In Process, Escalated, Resolved, Closed, Rejected |
| `assigned_agent_id` | StringType() |  |  |
| `assignment_date` | TimestampType() |  |  |
| `first_response_date` | TimestampType() |  |  |
| `resolution_date` | TimestampType() |  |  |
| `closing_date` | TimestampType() |  |  |
| `sla_breached` | BooleanType() | yes |  |
| `resolution_days` | IntegerType() |  | >= 0 |
| `resolution` | StringType() |  |  |
| `compensation_granted` | DecimalType(20,8) |  | >= 0 |
| `resolution_satisfaction` | IntegerType() |  | 1 to 5 |
| `is_repeat_complainer` | BooleanType() | yes |  |

## daily_exchange_rates

Primary key: `date, source_currency, target_currency`.

| Column | Type | Required | Rule |
|---|---|---|---|
| `date` | DateType() | yes | between 2023-06-17 and 2026-06-17 |
| `source_currency` | StringType() | yes | one of ARS, COP, MXN, USD |
| `target_currency` | StringType() | yes | one of ARS, COP, MXN, USD |
| `exchange_rate` | DoubleType() | yes | >= 0 |
| `buy_rate` | DoubleType() |  | >= 0 |
| `sell_rate` | DoubleType() |  | >= 0 |
| `source` | StringType() |  |  |
