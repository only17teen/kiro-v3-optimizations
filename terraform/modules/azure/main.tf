terraform {
  required_version = ">= 1.5.0"
  
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.75"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 2.45"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.23"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.11"
    }
  }
}

# =============================================================================
# Variables
# =============================================================================
variable "subscription_id" {
  description = "Azure subscription ID"
  type        = string
}

variable "resource_group_name" {
  description = "Resource group name"
  type        = string
  default     = "kiro-v3-rg"
}

variable "location" {
  description = "Azure region"
  type        = string
  default     = "West US 2"
}

variable "cluster_name" {
  description = "AKS cluster name"
  type        = string
  default     = "kiro-v3-aks"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

variable "node_vm_size" {
  description = "VM size for GPU nodes (NC series for NVIDIA GPUs)"
  type        = string
  default     = "Standard_NC6s_v3"  # 1x V100 GPU, 6 vCPUs, 112GB RAM
}

variable "node_count" {
  description = "Initial node count for GPU pool"
  type        = number
  default     = 3
}

variable "min_node_count" {
  description = "Minimum node count for autoscaling"
  type        = number
  default     = 2
}

variable "max_node_count" {
  description = "Maximum node count for autoscaling"
  type        = number
  default     = 20
}

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.29"
}

variable "enable_gpu_operator" {
  description = "Enable NVIDIA GPU Operator"
  type        = bool
  default     = true
}

# =============================================================================
# Provider
# =============================================================================
provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
  subscription_id = var.subscription_id
}

provider "azuread" {}

# =============================================================================
# Data Sources
# =============================================================================
data "azurerm_subscription" "current" {}
data "azurerm_client_config" "current" {}

# =============================================================================
# Resource Group
# =============================================================================
resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  
  tags = {
    Environment = var.environment
    Project     = "kiro-v3"
    ManagedBy   = "terraform"
  }
}

# =============================================================================
# Virtual Network
# =============================================================================
resource "azurerm_virtual_network" "main" {
  name                = "${var.cluster_name}-vnet"
  address_space       = ["10.0.0.0/16"]
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  
  tags = {
    Environment = var.environment
    Project     = "kiro-v3"
  }
}

resource "azurerm_subnet" "aks" {
  name                 = "aks-subnet"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.1.0/24"]
  
  # Delegate subnet to AKS
  delegation {
    name = "aks-delegation"
    service_delegation {
      name    = "Microsoft.ContainerService/managedClusters"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "app_gateway" {
  name                 = "appgw-subnet"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.0.2.0/24"]
}

# =============================================================================
# Network Security Group
# =============================================================================
resource "azurerm_network_security_group" "aks" {
  name                = "${var.cluster_name}-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  
  security_rule {
    name                       = "AllowHTTPS"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
  
  security_rule {
    name                       = "AllowHTTP"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "80"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
  
  tags = {
    Environment = var.environment
    Project     = "kiro-v3"
  }
}

resource "azurerm_subnet_network_security_group_association" "aks" {
  subnet_id                 = azurerm_subnet.aks.id
  network_security_group_id = azurerm_network_security_group.aks.id
}

# =============================================================================
# AKS Cluster
# =============================================================================
resource "azurerm_kubernetes_cluster" "main" {
  name                = var.cluster_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = var.cluster_name
  kubernetes_version  = var.kubernetes_version
  
  # Identity
  identity {
    type = "SystemAssigned"
  }
  
  # Default node pool (system workloads)
  default_node_pool {
    name                = "system"
    node_count          = 2
    vm_size             = "Standard_D4s_v3"
    vnet_subnet_id      = azurerm_subnet.aks.id
    type                = "VirtualMachineScaleSets"
    
    node_labels = {
      "node-type" = "system"
    }
    
    tags = {
      Environment = var.environment
      Project     = "kiro-v3"
    }
  }
  
  # Network profile
  network_profile {
    network_plugin    = "azure"
    network_policy    = "calico"
    load_balancer_sku = "standard"
    
    service_cidr   = "10.1.0.0/16"
    dns_service_ip = "10.1.0.10"
  }
  
  # Enable Azure AD integration
  azure_active_directory_role_based_access_control {
    managed                = true
    azure_rbac_enabled     = true
    admin_group_object_ids = [azuread_group.aks_admins.object_id]
  }
  
  # Enable OIDC issuer for Workload Identity
  oidc_issuer_enabled       = true
  workload_identity_enabled = true
  
  # Monitoring
  monitor_metrics {
    annotations_allowed = "*"
    labels_allowed      = "*"
  }
  
  # Maintenance window
  maintenance_window {
    allowed {
      day   = "Sunday"
      hours = [0, 1, 2, 3, 4]
    }
  }
  
  tags = {
    Environment = var.environment
    Project     = "kiro-v3"
  }
  
  depends_on = [azurerm_resource_group.main]
}

# =============================================================================
# GPU Node Pool
# =============================================================================
resource "azurerm_kubernetes_cluster_node_pool" "gpu" {
  name                  = "gpu"
  kubernetes_cluster_id = azurerm_kubernetes_cluster.main.id
  vm_size               = var.node_vm_size
  node_count            = var.node_count
  
  # Autoscaling
  enable_auto_scaling = true
  min_count           = var.min_node_count
  max_count           = var.max_node_count
  
  # Taints for GPU workloads
  node_taints = [
    "nvidia.com/gpu=true:NoSchedule"
  ]
  
  node_labels = {
    "nvidia.com/gpu.present" = "true"
    "node-type"              = "gpu"
    "kiro-v3/node-type"      = "gpu"
  }
  
  tags = {
    Environment = var.environment
    Project     = "kiro-v3"
  }
  
  lifecycle {
    ignore_changes = [node_count]
  }
}

# =============================================================================
# Azure AD Group for AKS Admins
# =============================================================================
resource "azuread_group" "aks_admins" {
  display_name     = "${var.cluster_name}-admins"
  security_enabled = true
  
  members = [
    data.azurerm_client_config.current.object_id
  ]
}

# =============================================================================
# Managed Identity for Workload Identity
# =============================================================================
resource "azurerm_user_assigned_identity" "kiro_v3" {
  name                = "${var.cluster_name}-identity"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  
  tags = {
    Environment = var.environment
    Project     = "kiro-v3"
  }
}

resource "azurerm_federated_identity_credential" "kiro_v3" {
  name                = "${var.cluster_name}-federated-credential"
  resource_group_name = azurerm_resource_group.main.name
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.main.oidc_issuer_url
  parent_id           = azurerm_user_assigned_identity.kiro_v3.id
  subject             = "system:serviceaccount:kiro-v3:kiro-v3"
}

# =============================================================================
# Role Assignments
# =============================================================================
resource "azurerm_role_assignment" "aks_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
}

resource "azurerm_role_assignment" "kiro_v3_storage" {
  scope                = azurerm_storage_account.checkpoints.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.kiro_v3.principal_id
}

resource "azurerm_role_assignment" "kiro_v3_monitoring" {
  scope                = azurerm_resource_group.main.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.kiro_v3.principal_id
}

# =============================================================================
# Container Registry
# =============================================================================
resource "azurerm_container_registry" "main" {
  name                = "${replace(var.cluster_name, "-", "")}acr"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "Standard"
  admin_enabled       = false
  
  tags = {
    Environment = var.environment
    Project     = "kiro-v3"
  }
}

# =============================================================================
# Storage Account for Checkpoints
# =============================================================================
resource "azurerm_storage_account" "checkpoints" {
  name                     = "${replace(var.cluster_name, "-", "")}checkpoints"
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = "GRS"
  min_tls_version          = "TLS1_2"
  
  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 30
    }
    container_delete_retention_policy {
      days = 7
    }
  }
  
  network_rules {
    default_action             = "Deny"
    virtual_network_subnet_ids = [azurerm_subnet.aks.id]
    bypass                     = ["AzureServices"]
  }
  
  tags = {
    Environment = var.environment
    Project     = "kiro-v3"
  }
}

resource "azurerm_storage_container" "checkpoints" {
  name                  = "checkpoints"
  storage_account_name  = azurerm_storage_account.checkpoints.name
  container_access_type = "private"
}

resource "azurerm_storage_container" "lora_output" {
  name                  = "lora-output"
  storage_account_name  = azurerm_storage_account.checkpoints.name
  container_access_type = "private"
}

# =============================================================================
# Log Analytics Workspace
# =============================================================================
resource "azurerm_log_analytics_workspace" "main" {
  name                = "${var.cluster_name}-logs"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  
  tags = {
    Environment = var.environment
    Project     = "kiro-v3"
  }
}

resource "azurerm_monitor_diagnostic_setting" "aks" {
  name                       = "${var.cluster_name}-diagnostics"
  target_resource_id         = azurerm_kubernetes_cluster.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  
  enabled_log {
    category = "kube-apiserver"
  }
  
  enabled_log {
    category = "kube-controller-manager"
  }
  
  enabled_log {
    category = "kube-scheduler"
  }
  
  enabled_log {
    category = "cluster-autoscaler"
  }
  
  metric {
    category = "AllMetrics"
    enabled  = true
  }
}

# =============================================================================
# Application Gateway (Optional - for ingress)
# =============================================================================
resource "azurerm_public_ip" "app_gateway" {
  name                = "${var.cluster_name}-appgw-pip"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  allocation_method   = "Static"
  sku                 = "Standard"
  
  tags = {
    Environment = var.environment
    Project     = "kiro-v3"
  }
}

resource "azurerm_application_gateway" "main" {
  count = 0  # Set to 1 to enable
  
  name                = "${var.cluster_name}-appgw"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  
  sku {
    name     = "WAF_v2"
    tier     = "WAF_v2"
    capacity = 2
  }
  
  gateway_ip_configuration {
    name      = "gateway-ip-config"
    subnet_id = azurerm_subnet.app_gateway.id
  }
  
  frontend_port {
    name = "frontend-port"
    port = 443
  }
  
  frontend_ip_configuration {
    name                 = "frontend-ip-config"
    public_ip_address_id = azurerm_public_ip.app_gateway[0].id
  }
  
  ssl_certificate {
    name     = "kiro-v3-cert"
    data     = filebase64("cert.pfx")
    password = "change-me"
  }
  
  waf_configuration {
    enabled          = true
    firewall_mode    = "Prevention"
    rule_set_type    = "OWASP"
    rule_set_version = "3.2"
  }
  
  tags = {
    Environment = var.environment
    Project     = "kiro-v3"
  }
}

# =============================================================================
# GPU Operator (NVIDIA)
# =============================================================================
resource "helm_release" "gpu_operator" {
  count = var.enable_gpu_operator ? 1 : 0
  
  name       = "gpu-operator"
  repository = "https://nvidia.github.io/gpu-operator"
  chart      = "gpu-operator"
  version    = "v23.9.1"
  namespace  = "gpu-operator"
  
  create_namespace = true
  
  set {
    name  = "driver.enabled"
    value = "true"
  }
  
  set {
    name  = "toolkit.enabled"
    value = "true"
  }
  
  set {
    name  = "devicePlugin.enabled"
    value = "true"
  }
  
  set {
    name  = "dcgmExporter.enabled"
    value = "true"
  }
  
  depends_on = [azurerm_kubernetes_cluster_node_pool.gpu]
}

# =============================================================================
# Outputs
# =============================================================================
output "cluster_name" {
  description = "AKS cluster name"
  value       = azurerm_kubernetes_cluster.main.name
}

output "cluster_id" {
  description = "AKS cluster ID"
  value       = azurerm_kubernetes_cluster.main.id
}

output "kube_config" {
  description = "Kubeconfig raw content"
  value       = azurerm_kubernetes_cluster.main.kube_config_raw
  sensitive   = true
}

output "host" {
  description = "Kubernetes host"
  value       = azurerm_kubernetes_cluster.main.kube_config[0].host
  sensitive   = true
}

output "client_certificate" {
  description = "Kubernetes client certificate"
  value       = base64decode(azurerm_kubernetes_cluster.main.kube_config[0].client_certificate)
  sensitive   = true
}

output "client_key" {
  description = "Kubernetes client key"
  value       = base64decode(azurerm_kubernetes_cluster.main.kube_config[0].client_key)
  sensitive   = true
}

output "cluster_ca_certificate" {
  description = "Kubernetes cluster CA certificate"
  value       = base64decode(azurerm_kubernetes_cluster.main.kube_config[0].cluster_ca_certificate)
  sensitive   = true
}

output "resource_group_name" {
  description = "Resource group name"
  value       = azurerm_resource_group.main.name
}

output "location" {
  description = "Azure region"
  value       = azurerm_resource_group.main.location
}

output "acr_login_server" {
  description = "Container registry login server"
  value       = azurerm_container_registry.main.login_server
}

output "storage_account_name" {
  description = "Storage account for checkpoints"
  value       = azurerm_storage_account.checkpoints.name
}

output "workload_identity_client_id" {
  description = "Workload identity client ID"
  value       = azurerm_user_assigned_identity.kiro_v3.client_id
}

output "oidc_issuer_url" {
  description = "OIDC issuer URL for workload identity"
  value       = azurerm_kubernetes_cluster.main.oidc_issuer_url
}