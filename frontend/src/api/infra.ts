import { apiGet } from './client'

export interface InstanceStatus {
  name: string
  reachable: boolean
  version: string | null
  environments: number | null
  stacks: number | null
  checked_at: string
  error: string | null
}

export interface BackupInfo {
  instance: string
  last_backup_at: string | null
  size_bytes: number | null
  age_hours: number | null
  stale: boolean
  checked_at: string
}

export interface Infra {
  instances: InstanceStatus[]
  backups: BackupInfo[]
}

export function getInfra(): Promise<Infra> {
  return apiGet<Infra>('/infra')
}
