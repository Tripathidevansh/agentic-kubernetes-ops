import React, { useEffect, useRef, useState } from 'react';
import { ApprovalModal } from './components/ApprovalModal';
import { Header } from './components/Header';
import { NewInvestigationModal } from './components/NewInvestigationModal';
import { NavTab, Sidebar } from './components/Sidebar';
import { api } from './services/api';
import {
  ClusterOverviewResponse,
  HealthResponse,
  IncidentItem,
  InvestigationDetailResponse,
  RemediationProposal,
} from './types/api';
import { IncidentsView } from './views/IncidentsView';
import { InvestigationView } from './views/InvestigationView';
import { ObservabilityView } from './views/ObservabilityView';
import { OverviewView } from './views/OverviewView';
import { RemediationsView } from './views/RemediationsView';
import { ResourcesView } from './views/ResourcesView';
import { SettingsView } from './views/SettingsView';
import { VerificationView } from './views/VerificationView';

export const App: React.FC = () => {
  // Navigation & UI state
  const [activeTab, setActiveTab] = useState<NavTab>('overview');
  const [namespace, setNamespace] = useState<string>('k8s-agent-demo');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<number>(0);

  // Modals state
  const [isNewInvestigationOpen, setIsNewInvestigationOpen] = useState(false);
  const [isSubmittingInvestigation, setIsSubmittingInvestigation] = useState(false);
  const [modalProposal, setModalProposal] = useState<RemediationProposal | null>(null);
  const [modalAction, setModalAction] = useState<'approve' | 'reject' | null>(null);
  const [isSubmittingDecision, setIsSubmittingDecision] = useState(false);

  // API Data
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [overview, setOverview] = useState<ClusterOverviewResponse | null>(null);
  const [incidents, setIncidents] = useState<IncidentItem[]>([]);
  const [investigationsList, setInvestigationsList] = useState<InvestigationDetailResponse[]>([]);
  const [activeInvestigationId, setActiveInvestigationId] = useState<string | null>(null);
  const [currentInvestigation, setCurrentInvestigation] = useState<InvestigationDetailResponse | null>(null);
  const [allProposals, setAllProposals] = useState<RemediationProposal[]>([]);
  const [isLoadingInvestigation, setIsLoadingInvestigation] = useState(false);

  // Polling ref
  const pollTimerRef = useRef<number | null>(null);

  // Initial data load
  const loadData = async () => {
    setIsRefreshing(true);
    try {
      const [h, ov, inc, invs, rems] = await Promise.allSettled([
        api.getHealth(),
        api.getClusterOverview(namespace),
        api.getIncidents(namespace),
        api.listInvestigations(),
        api.listRemediations(),
      ]);

      if (h.status === 'fulfilled') setHealth(h.value);
      if (ov.status === 'fulfilled') setOverview(ov.value);
      if (inc.status === 'fulfilled') setIncidents(inc.value.incidents || []);
      if (invs.status === 'fulfilled') {
        setInvestigationsList(invs.value);
        if (!activeInvestigationId && invs.value.length > 0) {
          setActiveInvestigationId(invs.value[0].investigation_id);
          setCurrentInvestigation(invs.value[0]);
        }
      }
      if (rems.status === 'fulfilled') {
        const list = Array.isArray(rems.value) ? rems.value : ((rems.value as any)?.remediations || []);
        setAllProposals(list);
      }

      setLastUpdated(0);
    } catch (err) {
      console.error('Failed to load dashboard data:', err);
    } finally {
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    loadData();
    const interval = setInterval(() => {
      setLastUpdated((prev) => prev + 5);
    }, 5000);
    return () => clearInterval(interval);
  }, [namespace]);

  // Load specific investigation when activeInvestigationId changes
  useEffect(() => {
    if (!activeInvestigationId) return;

    let isMounted = true;
    const fetchInvestigation = async () => {
      try {
        setIsLoadingInvestigation(true);
        const data = await api.getInvestigation(activeInvestigationId);
        if (isMounted) {
          setCurrentInvestigation(data);

          // If still running/queued, poll
          if (data.status === 'RUNNING' || data.status === 'QUEUED') {
            if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
            pollTimerRef.current = window.setTimeout(fetchInvestigation, 3000);
          }
        }
      } catch (err) {
        console.error('Error fetching investigation:', err);
      } finally {
        if (isMounted) setIsLoadingInvestigation(false);
      }
    };

    fetchInvestigation();

    return () => {
      isMounted = false;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, [activeInvestigationId]);

  // Handlers
  const handleInvestigatePod = async (podName: string) => {
    setIsSubmittingInvestigation(true);
    try {
      const res = await api.createInvestigation(namespace, podName, true);
      setActiveInvestigationId(res.investigation_id);
      setActiveTab('investigations');
      loadData();
    } catch (err) {
      alert(`Failed to launch investigation: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setIsSubmittingInvestigation(false);
    }
  };

  const handleStartInvestigation = async (ns: string, podName?: string, useLlm?: boolean) => {
    setIsSubmittingInvestigation(true);
    try {
      const res = await api.createInvestigation(ns, podName, useLlm ?? true);
      setIsNewInvestigationOpen(false);
      setActiveInvestigationId(res.investigation_id);
      setActiveTab('investigations');
      loadData();
    } catch (err) {
      alert(`Failed to launch investigation: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setIsSubmittingInvestigation(false);
    }
  };

  const handleApproveProposal = async (proposalId: string, reviewer: string, comments: string) => {
    setIsSubmittingDecision(true);
    try {
      await api.approveRemediation(proposalId, reviewer, comments);
      setModalProposal(null);
      setModalAction(null);
      // Reload current investigation and proposal list
      if (activeInvestigationId) {
        const inv = await api.getInvestigation(activeInvestigationId);
        setCurrentInvestigation(inv);
      }
      const rems = await api.listRemediations();
      const list = Array.isArray(rems) ? rems : ((rems as any)?.remediations || []);
      setAllProposals(list);
    } catch (err) {
      alert(`Approval failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setIsSubmittingDecision(false);
    }
  };

  const handleRejectProposal = async (proposalId: string, reviewer: string, reason: string) => {
    setIsSubmittingDecision(true);
    try {
      await api.rejectRemediation(proposalId, reviewer, reason);
      setModalProposal(null);
      setModalAction(null);
      // Reload current investigation and proposal list
      if (activeInvestigationId) {
        const inv = await api.getInvestigation(activeInvestigationId);
        setCurrentInvestigation(inv);
      }
      const rems = await api.listRemediations();
      const list = Array.isArray(rems) ? rems : ((rems as any)?.remediations || []);
      setAllProposals(list);
    } catch (err) {
      alert(`Rejection failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setIsSubmittingDecision(false);
    }
  };

  const availablePods = overview?.pods?.map((p) => p.name) || [];
  const proposalsForVerification =
    currentInvestigation?.result?.remediation_proposals || allProposals;

  return (
    <div className="flex bg-surface min-h-screen text-on-surface antialiased font-sans">
      {/* Sidebar Navigation */}
      <Sidebar
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        incidentCount={incidents.length}
        remediationCount={allProposals.filter((p) => p.can_remediate && (p.approval?.status === 'PENDING' || !p.approval)).length}
        clusterConnected={health?.kubernetes_connected ?? true}
      />

      {/* Main View Area (offset left by sidebar width 60 = 240px) */}
      <div className="pl-60 flex-1 flex flex-col min-w-0">
        {/* Top Sticky Header */}
        <Header
          namespace={namespace}
          onSelectNamespace={setNamespace}
          availableNamespaces={overview?.namespaces || [namespace]}
          geminiModel={health?.gemini_model || 'gemini-3.8-flash'}
          isRefreshing={isRefreshing}
          onRefresh={loadData}
          onOpenNewInvestigation={() => setIsNewInvestigationOpen(true)}
          lastUpdatedSeconds={lastUpdated}
        />

        {/* Content View Router */}
        <main className="relative pt-14 bg-surface w-full min-h-screen">
          {activeTab === 'overview' && (
            <OverviewView
              overview={overview}
              incidents={incidents}
              geminiModel={health?.gemini_model || 'gemini-3.8-flash'}
              onInvestigatePod={handleInvestigatePod}
              onViewAllIncidents={() => setActiveTab('incidents')}
              onOpenNewInvestigation={() => setIsNewInvestigationOpen(true)}
            />
          )}

          {activeTab === 'incidents' && (
            <IncidentsView
              incidents={incidents}
              namespace={namespace}
              onInvestigatePod={handleInvestigatePod}
              onBatchInvestigate={() => handleStartInvestigation(namespace, undefined, true)}
            />
          )}

          {activeTab === 'investigations' && (
            <InvestigationView
              investigation={currentInvestigation}
              investigationsList={investigationsList}
              onSelectInvestigation={setActiveInvestigationId}
              onOpenNewInvestigation={() => setIsNewInvestigationOpen(true)}
              onOpenApproveModal={(proposal) => {
                setModalProposal(proposal);
                setModalAction('approve');
              }}
              onOpenRejectModal={(proposal) => {
                setModalProposal(proposal);
                setModalAction('reject');
              }}
              isLoading={isLoadingInvestigation}
            />
          )}

          {activeTab === 'remediations' && (
            <RemediationsView
              proposals={allProposals}
              onOpenApproveModal={(proposal) => {
                setModalProposal(proposal);
                setModalAction('approve');
              }}
              onOpenRejectModal={(proposal) => {
                setModalProposal(proposal);
                setModalAction('reject');
              }}
              onOpenNewInvestigation={() => setIsNewInvestigationOpen(true)}
            />
          )}

          {activeTab === 'verification' && (
            <VerificationView
              proposals={proposalsForVerification}
              investigationId={activeInvestigationId || undefined}
              onOpenNewInvestigation={() => setIsNewInvestigationOpen(true)}
            />
          )}

          {activeTab === 'observability' && (
            <ObservabilityView
              investigation={currentInvestigation}
              geminiModel={health?.gemini_model || 'gemini-3.8-flash'}
            />
          )}

          {activeTab === 'resources' && (
            <ResourcesView
              pods={overview?.pods || []}
              namespace={namespace}
              clusterName={overview?.cluster_name || 'k8s-agent-demo'}
              onInvestigatePod={handleInvestigatePod}
            />
          )}

          {activeTab === 'settings' && (
            <SettingsView health={health} onRefreshHealth={loadData} />
          )}
        </main>
      </div>

      {/* New Investigation Modal */}
      <NewInvestigationModal
        isOpen={isNewInvestigationOpen}
        onClose={() => setIsNewInvestigationOpen(false)}
        onSubmit={handleStartInvestigation}
        availablePods={availablePods}
        defaultNamespace={namespace}
        isSubmitting={isSubmittingInvestigation}
      />

      {/* Human Approval / Rejection Modal */}
      <ApprovalModal
        proposal={modalProposal}
        action={modalAction}
        onClose={() => {
          setModalProposal(null);
          setModalAction(null);
        }}
        onConfirmApprove={handleApproveProposal}
        onConfirmReject={handleRejectProposal}
        isSubmitting={isSubmittingDecision}
      />
    </div>
  );
};
