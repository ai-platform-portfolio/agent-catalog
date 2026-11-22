terraform {
  required_version = ">= 1.9, < 2.0"

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "2.13.0"
    }
  }
}

variable "project_endpoint" {
  type    = string
  default = "https://foundry-management-portfolio-20261122-69909108.services.ai.azure.com/api/projects/management"

  validation {
    condition     = can(regex("^https://[a-z0-9-]+\\.services\\.ai\\.azure\\.com/api/projects/[a-zA-Z0-9_-]+/?$", var.project_endpoint))
    error_message = "Use the existing Foundry project’s HTTPS data-plane endpoint."
  }
}

variable "image" {
  type = string

  validation {
    condition     = can(regex("^localacrk8s\\.azurecr\\.io/agents/ai-platform-portfolio/agent-catalog/sre-local@sha256:[0-9a-f]{64}$", var.image))
    error_message = "Push the image to the sre-local ACR repository and use its sha256 digest."
  }
}

variable "source_revision" {
  type = string
}

variable "knowledge_manifest" {
  type = string
}

variable "gateway_connection" {
  description = "Public chart output for the temporary agent. Supply versionless secret references only."
  type = object({
    MODEL_BASE_URL                     = string
    MODEL_DEPLOYMENT_NAME              = string
    MODEL_API_KEY_SECRET_URI           = string
    CODING_MODEL_DEPLOYMENT_NAME       = string
    CODING_MODEL_API_KEY_SECRET_URI    = string
    EMBEDDING_MODEL_DEPLOYMENT_NAME    = string
    EMBEDDING_MODEL_API_KEY_SECRET_URI = string
    EMBEDDING_MODEL_VERSION            = string
  })

  validation {
    condition = alltrue([
      for uri in [var.gateway_connection.MODEL_API_KEY_SECRET_URI,
        var.gateway_connection.CODING_MODEL_API_KEY_SECRET_URI,
      var.gateway_connection.EMBEDDING_MODEL_API_KEY_SECRET_URI] :
      can(regex("^https://[a-z0-9-]+\\.vault\\.azure\\.net/secrets/[a-zA-Z0-9-]+$", uri))
    ])
    error_message = "Use versionless Key Vault secret URIs from the gateway chart."
  }
}

variable "traces_endpoint" {
  type    = string
  default = ""
}

locals {
  name     = "agent-catalog-sre-local"
  endpoint = trimsuffix(var.project_endpoint, "/")
}

resource "azapi_data_plane_resource" "sre" {
  type      = "Microsoft.Foundry/agents@v1"
  name      = local.name
  parent_id = trimprefix(local.endpoint, "https://")

  body = {
    name = local.name
    definition = {
      kind                    = "hosted"
      container_configuration = { image = var.image }
      cpu                     = "0.5"
      memory                  = "1Gi"
      protocol_versions       = [{ protocol = "invocations", version = "2.0.0" }]
      environment_variables = merge(var.gateway_connection, {
        MODEL_CONNECTION_MODE                              = "gateway"
        MODEL_AUTH_METHOD                                  = "api_key"
        MEMORY_STORE_NAME                                  = ""
        KNOWLEDGE_MANIFEST_JSON                            = var.knowledge_manifest
        DIAGNOSTICS_BACKEND                                = "provided"
        PYTHONNOUSERSITE                                   = "1"
        SOURCE_REVISION                                    = var.source_revision
        RELEASE_IMAGE                                      = var.image
        OTEL_EXPORTER_OTLP_TRACES_ENDPOINT                 = var.traces_endpoint
        OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = "false"
      })
    }
  }

  response_export_values = ["versions.latest.version", "instance_identity.principal_id"]
}

output "agent_version" {
  value = azapi_data_plane_resource.sre.output.versions.latest.version
}

output "runtime_principal_id" {
  value = azapi_data_plane_resource.sre.output.instance_identity.principal_id
}

output "invocations_endpoint" {
  value = "${local.endpoint}/agents/${local.name}/endpoint/protocols/invocations"
}
