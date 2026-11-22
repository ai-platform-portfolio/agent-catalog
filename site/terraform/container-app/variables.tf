variable "hosting" {
  description = "Existing Container Apps environment and its same-named managed identity."
  type = object({
    name                = string
    resource_group_name = string
  })
}

variable "application" {
  description = "Application name, hostname and warm replica intent."
  type = object({
    name         = string
    hostname     = string
    min_replicas = number
  })
}

variable "dns_zone" {
  description = "Existing public DNS zone where the catalog owns its hostname records."
  type = object({
    name                = string
    resource_group_name = string
  })
}

variable "registry" {
  description = "Existing ACR used by the shared Docker workflow."
  type = object({
    name                = string
    resource_group_name = string
  })
}

variable "image" {
  description = "Published container image pinned by digest; supplied by the caller workflow."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9.-]+/[a-z0-9/._-]+@sha256:[a-f0-9]{64}$", var.image))
    error_message = "Supply a published registry/repository@sha256:digest image reference."
  }
}
