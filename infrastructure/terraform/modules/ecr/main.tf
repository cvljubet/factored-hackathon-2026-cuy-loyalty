# Container registry for one service image (e.g. the backend API). Images are
# scanned for known vulnerabilities on push, and only the newest ones are kept.
resource "aws_ecr_repository" "this" {
  name                 = "${var.name_prefix}-${var.repository_name}"
  image_tag_mutability = var.image_tag_mutability
  force_delete         = var.force_delete

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the ${var.keep_images} most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.keep_images
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
