terraform {
  required_version = ">= 1.13.9, < 2.0"

  backend "azurerm" {
    use_azuread_auth = true
  }

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.13"
    }

    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}

  # CI supplies OIDC through ARM_*; local runs can use any supported identity.
  resource_provider_registrations = "none"
}

# AzureRM creates managed certificates but cannot bind their managedCertificates
# IDs through its custom-domain certificate argument. Use Azure's native API.
provider "azapi" {
  skip_provider_registration = true
}
