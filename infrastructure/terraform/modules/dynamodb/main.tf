# One on-demand table with a generic string PK / SK, so several record types share it
# (e.g. PK = CUST#<id>, SK = PROFILE | TXN#<ts>#<id>). Encrypted at rest with the
# AWS owned key (DynamoDB's default); no streams and no secondary indexes.
resource "aws_dynamodb_table" "this" {
  name                        = "${var.name_prefix}-${var.table_name}"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "PK"
  range_key                   = "SK"
  deletion_protection_enabled = var.deletion_protection

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  # Items whose ttl_attribute (epoch seconds) is in the past are deleted by DynamoDB.
  dynamic "ttl" {
    for_each = var.ttl_attribute == "" ? [] : [var.ttl_attribute]
    content {
      attribute_name = ttl.value
      enabled        = true
    }
  }
}
