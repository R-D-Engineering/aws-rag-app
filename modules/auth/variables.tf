#Auth Module
variable "project_name" {
  description = "Name of the project"
  type        = string
}

variable "stage" {
  description = "Deployment stage (dev, staging, prod)"
  type        = string
}

variable "cognito_domain_prefix" {
  description = "Prefix for Cognito hosted UI domain (must not contain 'aws')"
  type        = string
  default     = "rag-app"
}