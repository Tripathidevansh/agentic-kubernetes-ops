import {
  ClusterOverviewResponse,
  HealthResponse,
  IncidentsResponse,
  InvestigationCreateResponse,
  InvestigationDetailResponse,
  InvestigationVerificationResponse,
  RemediationApprovalResponse,
  RemediationProposal,
  RemediationRejectionResponse,
} from '../types/api';

const API_BASE = 'http://localhost:8000';

class ApiService {
  private async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const url = `${API_BASE}${endpoint}`;
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    });

    if (!response.ok) {
      let errorDetail = `HTTP ${response.status}: ${response.statusText}`;
      try {
        const errorJson = await response.json();
        if (errorJson.detail) {
          errorDetail = typeof errorJson.detail === 'string' 
            ? errorJson.detail 
            : JSON.stringify(errorJson.detail);
        }
      } catch {
        // use default error detail
      }
      throw new Error(errorDetail);
    }

    return response.json();
  }

  // Health
  async getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/health');
  }

  // Cluster Overview
  async getClusterOverview(namespace: string = 'k8s-agent-demo'): Promise<ClusterOverviewResponse> {
    return this.request<ClusterOverviewResponse>(`/api/cluster/overview?namespace=${encodeURIComponent(namespace)}`);
  }

  // Incidents
  async getIncidents(namespace: string = 'k8s-agent-demo'): Promise<IncidentsResponse> {
    return this.request<IncidentsResponse>(`/api/incidents?namespace=${encodeURIComponent(namespace)}`);
  }

  // Investigations
  async listInvestigations(): Promise<InvestigationDetailResponse[]> {
    return this.request<InvestigationDetailResponse[]>('/api/investigations');
  }

  async getInvestigation(id: string): Promise<InvestigationDetailResponse> {
    return this.request<InvestigationDetailResponse>(`/api/investigations/${encodeURIComponent(id)}`);
  }

  async createInvestigation(
    namespace: string = 'k8s-agent-demo',
    podName?: string,
    useLlm: boolean = true
  ): Promise<InvestigationCreateResponse> {
    return this.request<InvestigationCreateResponse>('/api/investigations', {
      method: 'POST',
      body: JSON.stringify({
        namespace,
        pod_name: podName || null,
        use_llm: useLlm,
      }),
    });
  }

  async getVerification(id: string): Promise<InvestigationVerificationResponse> {
    return this.request<InvestigationVerificationResponse>(`/api/investigations/${encodeURIComponent(id)}/verification`);
  }

  // Remediations
  async listRemediations(
    investigationId?: string,
    status?: string,
    approvalStatus?: string
  ): Promise<RemediationProposal[]> {
    const params = new URLSearchParams();
    if (investigationId) params.append('investigation_id', investigationId);
    if (status) params.append('status', status);
    if (approvalStatus) params.append('approval_status', approvalStatus);

    const query = params.toString() ? `?${params.toString()}` : '';
    const res = await this.request<any>(`/api/remediations${query}`);
    if (Array.isArray(res)) {
      return res;
    }
    return res?.remediations || [];
  }

  async approveRemediation(
    proposalId: string,
    reviewer: string = 'Alex Vance (SRE)',
    comments?: string
  ): Promise<RemediationApprovalResponse> {
    return this.request<RemediationApprovalResponse>(`/api/remediations/${encodeURIComponent(proposalId)}/approve`, {
      method: 'POST',
      body: JSON.stringify({
        reviewer,
        comments: comments || 'Approved via KafaKube8 Cockpit UI',
      }),
    });
  }

  async rejectRemediation(
    proposalId: string,
    reviewer: string = 'Alex Vance (SRE)',
    reason: string = 'Rejected via KafaKube8 Cockpit'
  ): Promise<RemediationRejectionResponse> {
    return this.request<RemediationRejectionResponse>(`/api/remediations/${encodeURIComponent(proposalId)}/reject`, {
      method: 'POST',
      body: JSON.stringify({
        reviewer,
        reason,
      }),
    });
  }
}

export const api = new ApiService();
