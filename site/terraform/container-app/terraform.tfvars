hosting = {
  name                = "portfolio-hosting"
  resource_group_name = "rg-portfolio-hosting"
}

application = {
  name         = "agent-catalog"
  hostname     = "agent-catalog.dev.michaelalinks.com"
  min_replicas = 1
}

dns_zone = {
  name                = "dev.michaelalinks.com"
  resource_group_name = "kubernetes"
}

registry = {
  name                = "localacrk8s"
  resource_group_name = "kubernetes"
}
