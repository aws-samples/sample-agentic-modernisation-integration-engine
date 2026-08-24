// Type definitions for Ant-to-Maven Agent

export interface AntToMavenRequest {
  repo_url: string;
  branch?: string;
  pat_token?: string;
}

export interface AntToMavenResponse {
  job_id: string;
  status: string;
  pom_xml?: string;
  analysis?: Record<string, unknown>;
}
