data "azurerm_container_app_environment" "existing" {
  name                = var.hosting.name
  resource_group_name = var.hosting.resource_group_name
}

data "azurerm_user_assigned_identity" "existing" {
  name                = var.hosting.name
  resource_group_name = var.hosting.resource_group_name
}

data "azurerm_container_registry" "existing" {
  name                = var.registry.name
  resource_group_name = var.registry.resource_group_name
}

module "application" {
  source                       = "git::https://github.com/ai-platform-portfolio/terraform.git//modules/azure/container-app?ref=441f10efa69b2b67b6f5a6d19707493576ac5160"
  name                         = var.application.name
  resource_group_name          = var.hosting.resource_group_name
  container_app_environment_id = data.azurerm_container_app_environment.existing.id
  managed_identity_id          = data.azurerm_user_assigned_identity.existing.id
  image                        = var.image
  registry_server              = data.azurerm_container_registry.existing.login_server
  replicas                     = { min = var.application.min_replicas }
  external_ingress_enabled     = true

  custom_domain = {
    hostname            = var.application.hostname
    zone_name           = var.dns_zone.name
    resource_group_name = var.dns_zone.resource_group_name
  }
}

output "url" {
  description = "Catalog URL once DNS and the managed certificate are ready."
  value       = module.application.url
}
