{{- /* vim: set filetype=mustache: */ -}}
{{- /*
Expand the name of the chart.
*/ -}}
{{- define "kiro-v3.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- /*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/ -}}
{{- define "kiro-v3.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- /*
Create chart name and version as used by the chart label.
*/ -}}
{{- define "kiro-v3.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- /*
Common labels
*/ -}}
{{- define "kiro-v3.labels" -}}
helm.sh/chart: {{ include "kiro-v3.chart" . }}
{{ include "kiro-v3.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- if .Values.global.labels }}
{{ toYaml .Values.global.labels }}
{{- end }}
{{- end -}}

{{- /*
Selector labels
*/ -}}
{{- define "kiro-v3.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kiro-v3.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- /*
Create the name of the service account to use
*/ -}}
{{- define "kiro-v3.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "kiro-v3.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- /*
Create the name of the secret to use
*/ -}}
{{- define "kiro-v3.secretName" -}}
{{- default (printf "%s-secrets" (include "kiro-v3.fullname" .)) .Values.existingSecret -}}
{{- end -}}

{{- /*
Create the name of the configmap to use
*/ -}}
{{- define "kiro-v3.configMapName" -}}
{{- default (printf "%s-config" (include "kiro-v3.fullname" .)) .Values.existingConfigMap -}}
{{- end -}}

{{- /*
Image pull secrets
*/ -}}
{{- define "kiro-v3.imagePullSecrets" -}}
{{- if .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range .Values.global.imagePullSecrets }}
  - name: {{ . }}
{{- end }}
{{- else if .Values.image.pullSecrets }}
imagePullSecrets:
{{- range .Values.image.pullSecrets }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end -}}

{{- /*
Priority class name
*/ -}}
{{- define "kiro-v3.priorityClassName" -}}
{{- if .Values.priorityClass.enabled -}}
{{- default (printf "%s-priority" (include "kiro-v3.fullname" .)) .Values.priorityClass.name -}}
{{- end -}}
{{- end -}}