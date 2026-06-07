terraform {
  required_version = ">= 1.5.0"
  
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.23"
    }
  }
}

# =============================================================================
# Variables
# =============================================================================
variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "cluster_name" {
  description = "GKE cluster name"
  type        = string
  default     = "kiro-v3-cluster"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

variable "node_locations" {
  description = "Zones for node pools"
  type        = list(string)
  default     = ["us-central1-a", "us-central1-b", "us-central1-c"]
}

variable "gpu_machine_type" {
  description = "Machine type for GPU nodes"
  type        = string
  default     = "n1-standard-4"
}

variable "gpu_type" {
  description = "GPU type"
  type        = string
  default     = "nvidia-tesla-t4"
}

variable "gpu_count_per_node" {
  description = "Number of GPUs per node"
  type        = number
  default     = 1
}

variable "node_desired_count" {
  description = "Desired number of GPU nodes"
  type        = number
  default     = 3
}

variable "node_min_count" {
  description = "Minimum number of GPU nodes"
  type        = number
  default     = 2
}

variable "node_max_count" {
  description = "Maximum number of GPU nodes"
  type        = number
  default     = 20
}

# =============================================================================
# Provider
# =============================================================================
provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# =============================================================================
# APIs
# =============================================================================
resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "container.googleapis.com",
    "containerregistry.googleapis.com",
    "artifactregistry.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
    "cloudtrace.googleapis.com",
    "cloudprofiler.googleapis.com",
  ])
  
  service            = each.value
  disable_on_destroy = false
}

# =============================================================================
# VPC
# =============================================================================
resource "google_compute_network" "vpc" {
  name                    = "${var.cluster_name}-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${var.cluster_name}-subnet"
  ip_cidr_range = "10.0.0.0/16"
  network       = google_compute_network.vpc.id
  region        = var.region
  
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.1.0.0/16"
  }
  
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.2.0.0/20"
  }
  
  private_ip_google_access = true
}

resource "google_compute_router" "router" {
  name    = "${var.cluster_name}-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "${var.cluster_name}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# =============================================================================
# GKE Cluster
# =============================================================================
resource "google_container_cluster" "main" {
  name     = var.cluster_name
  location = var.region
  
  network    = google_compute_network.vpc.name
  subnetwork = google_compute_subnetwork.subnet.name
  
  # Enable Autopilot for simplified management (optional)
  # enable_autopilot = true
  
  release_channel {
    channel = "REGULAR"
  }
  
  min_master_version = "1.29"
  
  # IP allocation for pods and services
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }
  
  # Private cluster configuration
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }
  
  # Master authorized networks
  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = "0.0.0.0/0"
      display_name = "All"
    }
  }
  
  # Logging and monitoring
  logging_service    = "logging.googleapis.com/kubernetes"
  monitoring_service = "monitoring.googleapis.com/kubernetes"
  
  # Workload Identity
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }
  
  # Network policy
  network_policy {
    enabled  = true
    provider = "CALICO"
  }
  
  # Remove default node pool (we'll create custom ones)
  remove_default_node_pool = true
  initial_node_count       = 1
  
  depends_on = [
    google_project_service.apis,
    google_compute_subnetwork.subnet,
  ]
  
  resource_labels = {
    environment = var.environment
    project     = "kiro-v3"
  }
}

# =============================================================================
# CPU Node Pool (system workloads)
# =============================================================================
resource "google_container_node_pool" "cpu" {
  name       = "${var.cluster_name}-cpu-pool"
  location   = var.region
  cluster    = google_container_cluster.main.name
  node_count = 2
  
  node_config {
    machine_type = "e2-standard-4"
    
    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
    
    labels = {
      "kiro-v3/node-type" = "cpu"
    }
    
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
    
    tags = ["kiro-v3", "cpu-nodes"]
  }
  
  management {
    auto_repair  = true
    auto_upgrade = true
  }
  
  depends_on = [google_container_cluster.main]
}

# =============================================================================
# GPU Node Pool (Kiro v3 workloads)
# =============================================================================
resource "google_container_node_pool" "gpu" {
  name     = "${var.cluster_name}-gpu-pool"
  location = var.region
  cluster  = google_container_cluster.main.name
  
  autoscaling {
    min_node_count = var.node_min_count
    max_node_count = var.node_max_count
  }
  
  node_count = var.node_desired_count
  
  node_config {
    machine_type = var.gpu_machine_type
    
    guest_accelerator {
      type  = var.gpu_type
      count = var.gpu_count_per_node
      gpu_driver_installation_config {
        gpu_driver_version = "LATEST"
      }
    }
    
    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform"
    ]
    
    labels = {
      "nvidia.com/gpu.present" = "true"
      "kiro-v3/node-type"      = "gpu"
    }
    
    taint {
      key    = "nvidia.com/gpu"
      value  = "true"
      effect = "NO_SCHEDULE"
    }
    
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
    
    tags = ["kiro-v3", "gpu-nodes"]
    
    # Preemptible for cost savings (optional)
    # preemptible = true
  }
  
  management {
    auto_repair  = true
    auto_upgrade = true
  }
  
  depends_on = [google_container_cluster.main]
  
  lifecycle {
    ignore_changes = [node_count]
  }
}

# =============================================================================
# Workload Identity (GCP Service Account binding)
# =============================================================================
resource "google_service_account" "kiro_v3" {
  account_id   = "kiro-v3-${var.environment}"
  display_name = "Kiro v3 Service Account"
  description  = "Service account for Kiro v3 workloads"
}

resource "google_project_iam_member" "kiro_v3_storage" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.kiro_v3.email}"
}

resource "google_project_iam_member" "kiro_v3_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.kiro_v3.email}"
}

resource "google_project_iam_member" "kiro_v3_monitoring" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.kiro_v3.email}"
}

resource "google_project_iam_member" "kiro_v3_trace" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.kiro_v3.email}"
}

# Bind GCP SA to K8s SA
resource "google_service_account_iam_member" "kiro_v3_workload_identity" {
  service_account_id = google_service_account.kiro_v3.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[kiro-v3/kiro-v3]"
}

# =============================================================================
# GCS Bucket for Checkpoints
# =============================================================================
resource "google_storage_bucket" "checkpoints" {
  name          = "${var.project_id}-kiro-v3-checkpoints-${var.environment}"
  location      = var.region
  force_destroy = false
  
  versioning {
    enabled = true
  }
  
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
  
  lifecycle_rule {
    condition {
      num_newer_versions = 5
    }
    action {
      type = "Delete"
    }
  }
  
  uniform_bucket_level_access = true
  
  encryption {
    default_kms_key_name = google_kms_crypto_key.checkpoints.id
  }
  
  labels = {
    environment = var.environment
    project     = "kiro-v3"
  }
}

resource "google_storage_bucket_iam_member" "checkpoints_access" {
  bucket = google_storage_bucket.checkpoints.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.kiro_v3.email}"
}

# =============================================================================
# KMS for Encryption
# =============================================================================
resource "google_kms_key_ring" "kiro_v3" {
  name     = "kiro-v3-${var.environment}"
  location = var.region
}

resource "google_kms_crypto_key" "checkpoints" {
  name            = "checkpoints-key"
  key_ring        = google_kms_key_ring.kiro_v3.id
  rotation_period = "7776000s"  # 90 days
  
  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = "HSM"
  }
}

# =============================================================================
# Cloud Monitoring (Stackdriver)
# =============================================================================
resource "google_monitoring_dashboard" "kiro_v3" {
  dashboard_json = jsonencode({
    displayName = "Kiro v3 Dashboard"
    gridLayout = {
      columns = "2"
      widgets = [
        {
          title = "GPU Utilization"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"k8s_container\" AND metric.type=\"custom.googleapis.com/kiro_gpu_utilization\""
                  aggregation = {
                    alignmentPeriod    = "60s"
                    perSeriesAligner   = "ALIGN_MEAN"
                    crossSeriesReducer = "REDUCE_MEAN"
                    groupByFields      = ["resource.label.pod_name"]
                  }
                }
              }
            }]
          }
        },
        {
          title = "Inference Latency (p95)"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"k8s_container\" AND metric.type=\"custom.googleapis.com/kiro_inference_latency\""
                  aggregation = {
                    alignmentPeriod    = "60s"
                    perSeriesAligner   = "ALIGN_PERCENTILE_95"
                    crossSeriesReducer = "REDUCE_MEAN"
                  }
                }
              }
            }]
          }
        },
        {
          title = "Cache Hit Rate"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"k8s_container\" AND metric.type=\"custom.googleapis.com/kiro_cache_hit_rate\""
                }
              }
            }]
          }
        },
        {
          title = "Actor Message Throughput"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"k8s_container\" AND metric.type=\"custom.googleapis.com/kiro_actor_throughput\""
                }
              }
            }]
          }
        }
      ]
    }
  })
}

# =============================================================================
# Alerting Policies
# =============================================================================
resource "google_monitoring_alert_policy" "high_latency" {
  display_name = "Kiro v3 High Latency Alert"
  combiner     = "OR"
  
  conditions {
    display_name = "Inference latency p95 > 100ms"
    
    condition_threshold {
      filter          = "resource.type=\"k8s_container\" AND metric.type=\"custom.googleapis.com/kiro_inference_latency\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = 0.1
      
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
        cross_series_reducer = "REDUCE_MEAN"
      }
      
      trigger {
        count = 1
      }
    }
  }
  
  notification_channels = [google_monitoring_notification_channel.email.id]
  
  alert_strategy {
    auto_close = "86400s"
  }
  
  severity = "WARNING"
}

resource "google_monitoring_notification_channel" "email" {
  display_name = "Kiro v3 Team"
  type         = "email"
  
  labels = {
    email_address = "team@kiro.ai"
  }
}

# =============================================================================
# Outputs
# =============================================================================
output "cluster_name" {
  description = "GKE cluster name"
  value       = google_container_cluster.main.name
}

output "cluster_endpoint" {
  description = "GKE cluster endpoint"
  value       = google_container_cluster.main.endpoint
}
  
output "cluster_location" {
  description = "GKE cluster location"
  value       = google_container_cluster.main.location
}

output "project_id" {
  description = "GCP project ID"
  value       = var.project_id
}

output "service_account_email" {
  description = "Kiro v3 service account email"
  value       = google_service_account.kiro_v3.email
}

output "gcs_bucket_name" {
  description = "GCS bucket for checkpoints"
  value       = google_storage_bucket.checkpoints.name
}

output "workload_identity_pool" {
  description = "Workload identity pool"
  value       = "${var.project_id}.svc.id.goog"
}