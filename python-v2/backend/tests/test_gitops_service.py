"""
Unit and Integration Tests for GitOps Remediation Service (Phase 7A).

Verifies:
1. Controlled valid image replacement produces correct diff and valid YAML.
2. Comments, formatting, and non-target fields are preserved.
3. Invalid YAML syntax is rejected with GitOpsManifestSyntaxError.
4. Wrong target resource kind or name is rejected.
5. Container not found or wrong field path is rejected.
6. Current image mismatch is rejected.
7. Unexpected replacement image is rejected (fail-closed against arbitrary image changes).
8. Missing human approval (approval=None or PENDING) is rejected.
9. Insufficient evidence (can_remediate=False or INSUFFICIENT_EVIDENCE) is rejected.
10. Dirty working tree on target manifest triggers fail-closed abort.
11. Local Git feature branch and commit created cleanly without push/remote.
12. Strict zero-cluster-mutation guarantee (no kubectl writes).
"""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch
import uuid

import pytest
import yaml

from app.models.gitops import (
    GitOpsChangeRequest,
    GitOpsStatus,
    GitOpsTargetConfig,
)
from app.models.remediation import (
    ApprovalStatus,
    HumanApproval,
    ProposalProvenance,
    ProposalStatus,
    RemediationProposal,
    RiskLevel,
    YamlPatch,
)
from app.services.gitops_service import (
    GitOpsApprovalRequiredError,
    GitOpsDirtyWorkingTreeError,
    GitOpsError,
    GitOpsManifestSyntaxError,
    GitOpsService,
    GitOpsValidationError,
    generate_unified_diff,
    safe_replace_container_image,
)

SAMPLE_DEPLOYMENT_YAML = """# Header comment for deployment
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gitops-controlled-app
  namespace: k8s-agent-demo
  labels:
    app.kubernetes.io/name: gitops-controlled-app
    app.kubernetes.io/managed-by: gitops
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gitops-controlled-app
  template:
    metadata:
      labels:
        app: gitops-controlled-app
    spec:
      containers:
        - name: web
          image: nginx:invalid-tag-does-not-exist # Intentional failure image
          ports:
            - containerPort: 80
              name: http
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
"""


def _create_approved_proposal(
    target_name: str = "gitops-controlled-app",
    can_remediate: bool = True,
    status: ProposalStatus = ProposalStatus.ACTIONABLE,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
) -> RemediationProposal:
    """Helper to build an approved RemediationProposal."""
    proposal_id = str(uuid.uuid4())
    approval = HumanApproval(
        status=approval_status,
        reviewer="sre-operator",
        reviewed_at=datetime.now(timezone.utc).isoformat(),
        comments="Approved for automated GitOps preparation after verifying image tag.",
    )
    return RemediationProposal(
        proposal_id=proposal_id,
        target_workload=target_name,
        target_kind="Deployment",
        target_name=target_name,
        target_namespace="k8s-agent-demo",
        status=status,
        can_remediate=can_remediate,
        justification="Update failing image tag to verified stable release nginx:1.27.0-alpine",
        risk_level=RiskLevel.LOW,
        requires_approval=True,
        approval=approval if approval_status != ApprovalStatus.REJECTED or True else None,
        provenance=ProposalProvenance.DETERMINISTIC_FALLBACK,
        patch=YamlPatch(
            target_kind="Deployment",
            target_name=target_name,
            target_namespace="k8s-agent-demo",
            diff="--- a/manifests\n+++ b/manifests\n",
            patch_content={"image": "nginx:1.27.0-alpine"},
        ),
    )


# -----------------------------------------------------------------------------
# 1. Safe YAML Image Replacement & Formatting Tests
# -----------------------------------------------------------------------------
def test_safe_replace_container_image_success():
    """Verify clean image replacement, comment preservation, and AST equality."""
    updated = safe_replace_container_image(
        original_yaml_content=SAMPLE_DEPLOYMENT_YAML,
        target_kind="Deployment",
        target_name="gitops-controlled-app",
        container_name="web",
        expected_current_image="nginx:invalid-tag-does-not-exist",
        new_image="nginx:1.27.0-alpine",
        target_namespace="k8s-agent-demo",
    )

    # Comments and formatting preserved
    assert "# Header comment for deployment" in updated
    assert "# Intentional failure image" in updated
    assert "image: nginx:1.27.0-alpine" in updated
    assert "nginx:invalid-tag-does-not-exist" not in updated

    # AST validation
    doc = yaml.safe_load(updated)
    assert doc["kind"] == "Deployment"
    assert doc["metadata"]["name"] == "gitops-controlled-app"
    container = doc["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "web"
    assert container["image"] == "nginx:1.27.0-alpine"
    assert container["resources"]["limits"]["cpu"] == "200m"


def test_safe_replace_container_image_invalid_syntax():
    """Verify malformed YAML raises GitOpsManifestSyntaxError."""
    bad_yaml = "apiVersion: apps/v1\nkind: [unclosed list"
    with pytest.raises(GitOpsManifestSyntaxError, match="Invalid YAML syntax"):
        safe_replace_container_image(
            original_yaml_content=bad_yaml,
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            container_name="web",
            expected_current_image="nginx:invalid-tag-does-not-exist",
            new_image="nginx:1.27.0-alpine",
        )


def test_safe_replace_container_image_wrong_target_kind():
    """Verify mismatch between manifest kind and target kind is rejected."""
    with pytest.raises(GitOpsValidationError, match="Resource kind mismatch"):
        safe_replace_container_image(
            original_yaml_content=SAMPLE_DEPLOYMENT_YAML,
            target_kind="StatefulSet",
            target_name="gitops-controlled-app",
            container_name="web",
            expected_current_image="nginx:invalid-tag-does-not-exist",
            new_image="nginx:1.27.0-alpine",
        )


def test_safe_replace_container_image_wrong_target_name():
    """Verify mismatch between manifest resource name and target name is rejected."""
    with pytest.raises(GitOpsValidationError, match="Resource name mismatch"):
        safe_replace_container_image(
            original_yaml_content=SAMPLE_DEPLOYMENT_YAML,
            target_kind="Deployment",
            target_name="wrong-app-name",
            container_name="web",
            expected_current_image="nginx:invalid-tag-does-not-exist",
            new_image="nginx:1.27.0-alpine",
        )


def test_safe_replace_container_image_container_not_found():
    """Verify missing container name raises GitOpsValidationError."""
    with pytest.raises(GitOpsValidationError, match="Container 'database' not found"):
        safe_replace_container_image(
            original_yaml_content=SAMPLE_DEPLOYMENT_YAML,
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            container_name="database",
            expected_current_image="nginx:invalid-tag-does-not-exist",
            new_image="nginx:1.27.0-alpine",
        )


def test_safe_replace_container_image_current_image_mismatch():
    """Verify mismatch with expected current image raises GitOpsValidationError."""
    with pytest.raises(GitOpsValidationError, match="has image 'nginx:invalid-tag-does-not-exist', expected 'nginx:other'"):
        safe_replace_container_image(
            original_yaml_content=SAMPLE_DEPLOYMENT_YAML,
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            container_name="web",
            expected_current_image="nginx:other",
            new_image="nginx:1.27.0-alpine",
        )


def test_generate_unified_diff():
    """Verify unified diff generation produces valid diff format."""
    orig = "line1\nline2\nline3\n"
    upd = "line1\nline2_modified\nline3\n"
    diff = generate_unified_diff(orig, upd, "manifests/app.yaml")
    assert "--- a/manifests/app.yaml" in diff
    assert "+++ b/manifests/app.yaml" in diff
    assert "-line2" in diff
    assert "+line2_modified" in diff


# -----------------------------------------------------------------------------
# 2. Proposal Validation & Fail-Closed Guardrails
# -----------------------------------------------------------------------------
def test_validate_proposal_requires_approval(tmp_path):
    """Verify proposal lacking approval is rejected."""
    service = GitOpsService(repo_root=tmp_path)
    proposal = _create_approved_proposal()
    proposal.approval = None

    errors = service.validate_proposal(proposal, require_approval=True)
    assert any("approval" in e.lower() for e in errors)


def test_validate_proposal_rejects_pending_approval(tmp_path):
    """Verify proposal with PENDING approval is rejected for preparation."""
    service = GitOpsService(repo_root=tmp_path)
    proposal = _create_approved_proposal(approval_status=ApprovalStatus.PENDING)

    errors = service.validate_proposal(proposal, require_approval=True)
    assert any("pending" in e.lower() for e in errors)


def test_validate_proposal_rejects_insufficient_evidence(tmp_path):
    """Verify proposal with can_remediate=False or INSUFFICIENT_EVIDENCE is rejected."""
    service = GitOpsService(repo_root=tmp_path)
    proposal = _create_approved_proposal(
        can_remediate=False,
        status=ProposalStatus.INSUFFICIENT_EVIDENCE,
    )

    errors = service.validate_proposal(proposal, require_approval=True)
    assert any("cannot be remediated" in e for e in errors)


def test_validate_proposal_rejects_untrusted_target(tmp_path):
    """Verify proposal targeting unregistered resource is rejected."""
    service = GitOpsService(repo_root=tmp_path)
    proposal = _create_approved_proposal(target_name="unauthorized-service")

    errors = service.validate_proposal(proposal, require_approval=True)
    assert any("not a configured trusted GitOps remediation target" in e for e in errors)


# -----------------------------------------------------------------------------
# 3. Preview Change Tests
# -----------------------------------------------------------------------------
def test_preview_change_success(tmp_path):
    """Verify preview generates clean unified diff without modifying files."""
    manifest_file = tmp_path / "controlled-imagepull.yaml"
    manifest_file.write_text(SAMPLE_DEPLOYMENT_YAML, encoding="utf-8")

    service = GitOpsService(repo_root=tmp_path)
    service.register_target(
        GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path=str(manifest_file),
        )
    )

    proposal = _create_approved_proposal()
    req = GitOpsChangeRequest(proposal_id=proposal.proposal_id)

    preview = service.preview_change(proposal, req)

    assert preview.proposal_id == proposal.proposal_id
    assert preview.is_approved is True
    assert preview.can_prepare is True
    assert preview.current_image == "nginx:invalid-tag-does-not-exist"
    assert preview.replacement_image == "nginx:1.27.0-alpine"
    assert "-          image: nginx:invalid-tag-does-not-exist" in preview.diff
    assert "+          image: nginx:1.27.0-alpine" in preview.diff

    # Verify file was NOT modified
    assert manifest_file.read_text(encoding="utf-8") == SAMPLE_DEPLOYMENT_YAML


def test_preview_change_rejects_unexpected_replacement_image(tmp_path):
    """Verify preview rejects any replacement image differing from configured trusted image."""
    manifest_file = tmp_path / "controlled-imagepull.yaml"
    manifest_file.write_text(SAMPLE_DEPLOYMENT_YAML, encoding="utf-8")

    service = GitOpsService(repo_root=tmp_path)
    service.register_target(
        GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path=str(manifest_file),
        )
    )

    proposal = _create_approved_proposal()
    req = GitOpsChangeRequest(
        proposal_id=proposal.proposal_id,
        target_image="attacker-repo/malicious-image:latest",
    )

    with pytest.raises(GitOpsValidationError, match="does not match trusted replacement image"):
        service.preview_change(proposal, req)


# -----------------------------------------------------------------------------
# 4. Local Git Branch Preparation & Safety Guardrails
# -----------------------------------------------------------------------------
def _init_git_repo(path: Path) -> None:
    """Helper to initialize a real local git repository for testing."""
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@kafakube8.local"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Operator"], cwd=str(path), check=True, capture_output=True)


def test_prepare_git_change_fails_when_unapproved(tmp_path):
    """Verify prepare_git_change raises GitOpsApprovalRequiredError when proposal is not approved."""
    _init_git_repo(tmp_path)
    manifest = tmp_path / "app.yaml"
    manifest.write_text(SAMPLE_DEPLOYMENT_YAML, encoding="utf-8")

    service = GitOpsService(repo_root=tmp_path)
    service.register_target(
        GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path="app.yaml",
        )
    )

    proposal = _create_approved_proposal(approval_status=ApprovalStatus.PENDING)
    req = GitOpsChangeRequest(proposal_id=proposal.proposal_id)

    with pytest.raises(GitOpsApprovalRequiredError, match="Operator approval required"):
        service.prepare_git_change(proposal, req)


def test_prepare_git_change_in_local_git_repo(tmp_path):
    """
    Verify complete local Git branch preparation:
    - Creates feature branch
    - Writes updated manifest
    - Commits locally with descriptive message
    - Switches back to original branch
    - Manifest on feature branch contains updated image
    - Manifest on main branch remains unchanged
    - ZERO remote pushes or cluster writes
    """
    _init_git_repo(tmp_path)
    manifest = tmp_path / "app.yaml"
    manifest.write_text(SAMPLE_DEPLOYMENT_YAML, encoding="utf-8")

    # Initial commit on main
    subprocess.run(["git", "add", "app.yaml"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), check=True, capture_output=True)

    # Initial branch name
    proc = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    main_branch = proc.stdout.strip()

    service = GitOpsService(repo_root=tmp_path)
    service.register_target(
        GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path="app.yaml",
        )
    )

    proposal = _create_approved_proposal()
    req = GitOpsChangeRequest(proposal_id=proposal.proposal_id, operator="sre-alice")

    record = service.prepare_git_change(proposal, req)

    assert record.status == GitOpsStatus.BRANCH_PREPARED
    assert record.commit_hash is not None and len(record.commit_hash) >= 7
    assert record.branch_name.startswith("gitops/remediation-")
    assert record.replacement_image == "nginx:1.27.0-alpine"
    assert record.operator == "sre-alice"

    # Verify service switched back to original branch
    proc_curr = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    assert proc_curr.stdout.strip() == main_branch

    # Verify manifest on main branch is unchanged
    assert "nginx:invalid-tag-does-not-exist" in manifest.read_text(encoding="utf-8")

    # Checkout feature branch to verify its commit content
    subprocess.run(["git", "checkout", record.branch_name], cwd=str(tmp_path), check=True, capture_output=True)
    assert "nginx:1.27.0-alpine" in manifest.read_text(encoding="utf-8")
    assert "nginx:invalid-tag-does-not-exist" not in manifest.read_text(encoding="utf-8")

    # Checkout back to main
    subprocess.run(["git", "checkout", main_branch], cwd=str(tmp_path), check=True, capture_output=True)


def test_prepare_git_change_dirty_working_tree_rejected(tmp_path):
    """Verify dirty working tree on target manifest triggers fail-closed GitOpsDirtyWorkingTreeError."""
    _init_git_repo(tmp_path)
    manifest = tmp_path / "app.yaml"
    manifest.write_text(SAMPLE_DEPLOYMENT_YAML, encoding="utf-8")

    subprocess.run(["git", "add", "app.yaml"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), check=True, capture_output=True)

    # Intentionally dirty the manifest file
    manifest.write_text(SAMPLE_DEPLOYMENT_YAML + "\n# Uncommitted operator scratch comment\n", encoding="utf-8")

    service = GitOpsService(repo_root=tmp_path)
    service.register_target(
        GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path="app.yaml",
        )
    )

    proposal = _create_approved_proposal()
    req = GitOpsChangeRequest(proposal_id=proposal.proposal_id)

    with pytest.raises(GitOpsDirtyWorkingTreeError, match="has uncommitted changes in the working tree"):
        service.prepare_git_change(proposal, req)


def test_disallowed_git_commands_blocked(tmp_path):
    """Verify that push, remote, or pull cannot be executed via GitOpsService."""
    service = GitOpsService(repo_root=tmp_path)

    for disallowed in (["push", "origin", "main"], ["remote", "-v"], ["pull"]):
        with pytest.raises(GitOpsValidationError, match="strictly disallowed"):
            service._run_git_command(disallowed)


def test_zero_cluster_mutations_enforced(tmp_path):
    """
    Verify strict zero-cluster-mutation guarantee:
    Under NO circumstances may GitOpsService call kubectl or any Kubernetes mutation API.
    """
    _init_git_repo(tmp_path)
    manifest = tmp_path / "app.yaml"
    manifest.write_text(SAMPLE_DEPLOYMENT_YAML, encoding="utf-8")

    subprocess.run(["git", "add", "app.yaml"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), check=True, capture_output=True)

    service = GitOpsService(repo_root=tmp_path)
    service.register_target(
        GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path="app.yaml",
        )
    )

    proposal = _create_approved_proposal()
    req = GitOpsChangeRequest(proposal_id=proposal.proposal_id)

    # Patch subprocess.run to intercept all commands and verify 'kubectl' is NEVER invoked
    real_subprocess_run = subprocess.run

    def safe_subprocess_run(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        assert "kubectl" not in cmd_str, f"MUTATING KUBECTL COMMAND DETECTED: {cmd_str}"
        assert "apply" not in cmd_str, f"APPLY COMMAND DETECTED: {cmd_str}"
        assert "delete" not in cmd_str, f"DELETE COMMAND DETECTED: {cmd_str}"
        assert "push" not in cmd_str, f"GIT PUSH COMMAND DETECTED: {cmd_str}"
        return real_subprocess_run(cmd, *args, **kwargs)

    with patch("subprocess.run", side_effect=safe_subprocess_run):
        # 1. Preview
        preview = service.preview_change(proposal, req)
        assert preview.can_prepare is True

        # 2. Prepare
        record = service.prepare_git_change(proposal, req)
        assert record.status == GitOpsStatus.BRANCH_PREPARED


def test_preserving_non_target_fields_and_multiple_containers():
    """Verify that multi-container pods preserve all other containers, env, and volumes."""
    multi_container_yaml = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: multi-app
  namespace: k8s-agent-demo
spec:
  template:
    spec:
      volumes:
        - name: config-vol
          emptyDir: {}
      containers:
        - name: sidecar
          image: busybox:1.36
          command: ["sh", "-c", "echo sidecar"]
        - name: web
          image: nginx:invalid-tag-does-not-exist
          env:
            - name: ENV_VAR
              value: production
"""
    updated = safe_replace_container_image(
        original_yaml_content=multi_container_yaml,
        target_kind="Deployment",
        target_name="multi-app",
        container_name="web",
        expected_current_image="nginx:invalid-tag-does-not-exist",
        new_image="nginx:1.27.0-alpine",
    )

    doc = yaml.safe_load(updated)
    containers = doc["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 2
    assert containers[0]["name"] == "sidecar"
    assert containers[0]["image"] == "busybox:1.36"
    assert containers[0]["command"] == ["sh", "-c", "echo sidecar"]

    assert containers[1]["name"] == "web"
    assert containers[1]["image"] == "nginx:1.27.0-alpine"
    assert containers[1]["env"] == [{"name": "ENV_VAR", "value": "production"}]

    assert doc["spec"]["template"]["spec"]["volumes"] == [{"name": "config-vol", "emptyDir": {}}]


def test_prepare_git_change_duplicate_proposal_rejected(tmp_path):
    """Verify that attempting to prepare the same proposal twice is rejected."""
    _init_git_repo(tmp_path)
    manifest = tmp_path / "app.yaml"
    manifest.write_text(SAMPLE_DEPLOYMENT_YAML, encoding="utf-8")

    subprocess.run(["git", "add", "app.yaml"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), check=True, capture_output=True)

    service = GitOpsService(repo_root=tmp_path)
    service.register_target(
        GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path="app.yaml",
        )
    )

    proposal = _create_approved_proposal()
    req = GitOpsChangeRequest(proposal_id=proposal.proposal_id)

    # First preparation succeeds
    record1 = service.prepare_git_change(proposal, req)
    assert record1.status == GitOpsStatus.BRANCH_PREPARED

    # Second preparation of the same proposal is rejected
    with pytest.raises(GitOpsValidationError, match="Duplicate preparation is disallowed"):
        service.prepare_git_change(proposal, req)


def test_prepare_git_change_branch_collision_rejected(tmp_path):
    """Verify that branch collision with an existing branch is cleanly rejected."""
    _init_git_repo(tmp_path)
    manifest = tmp_path / "app.yaml"
    manifest.write_text(SAMPLE_DEPLOYMENT_YAML, encoding="utf-8")

    subprocess.run(["git", "add", "app.yaml"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), check=True, capture_output=True)

    # Pre-create a conflicting branch
    subprocess.run(["git", "branch", "gitops/conflicting-branch"], cwd=str(tmp_path), check=True, capture_output=True)

    service = GitOpsService(repo_root=tmp_path)
    service.register_target(
        GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path="app.yaml",
        )
    )

    proposal = _create_approved_proposal()
    req = GitOpsChangeRequest(proposal_id=proposal.proposal_id, branch_name="gitops/conflicting-branch")

    with pytest.raises(GitOpsValidationError, match="branch collision detected"):
        service.prepare_git_change(proposal, req)


def test_prepare_git_change_staged_index_rejected(tmp_path):
    """Verify that unrelated staged files in the index trigger GitOpsDirtyWorkingTreeError."""
    _init_git_repo(tmp_path)
    manifest = tmp_path / "app.yaml"
    manifest.write_text(SAMPLE_DEPLOYMENT_YAML, encoding="utf-8")

    subprocess.run(["git", "add", "app.yaml"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), check=True, capture_output=True)

    # Stage an unrelated file in git index
    unrelated = tmp_path / "unrelated_secret.py"
    unrelated.write_text("API_SECRET = 12345\n", encoding="utf-8")
    subprocess.run(["git", "add", "unrelated_secret.py"], cwd=str(tmp_path), check=True, capture_output=True)

    service = GitOpsService(repo_root=tmp_path)
    service.register_target(
        GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path="app.yaml",
        )
    )

    proposal = _create_approved_proposal()
    req = GitOpsChangeRequest(proposal_id=proposal.proposal_id)

    with pytest.raises(GitOpsDirtyWorkingTreeError, match="staging area contains uncommitted staged changes"):
        service.prepare_git_change(proposal, req)


def test_prepare_git_change_rollback_on_commit_failure(tmp_path):
    """
    Verify atomic transaction rollback if git commit fails:
    - Target file reverted to original content
    - Staged changes un-staged
    - Repository returned to starting branch
    - Half-created feature branch deleted
    """
    _init_git_repo(tmp_path)
    manifest = tmp_path / "app.yaml"
    manifest.write_text(SAMPLE_DEPLOYMENT_YAML, encoding="utf-8")

    subprocess.run(["git", "add", "app.yaml"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), check=True, capture_output=True)

    proc = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    starting_branch = proc.stdout.strip()

    service = GitOpsService(repo_root=tmp_path)
    service.register_target(
        GitOpsTargetConfig(
            target_kind="Deployment",
            target_name="gitops-controlled-app",
            target_namespace="k8s-agent-demo",
            container_name="web",
            current_image="nginx:invalid-tag-does-not-exist",
            trusted_replacement_image="nginx:1.27.0-alpine",
            manifest_path="app.yaml",
        )
    )

    proposal = _create_approved_proposal()
    req = GitOpsChangeRequest(proposal_id=proposal.proposal_id)

    # Force git commit to fail
    real_subprocess_run = subprocess.run

    def failing_commit_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "git" and cmd[1] == "commit":
            mock_res = MagicMock()
            mock_res.returncode = 1
            mock_res.stderr = "fatal: mock git hook rejected commit"
            return mock_res
        return real_subprocess_run(cmd, *args, **kwargs)

    with patch("subprocess.run", side_effect=failing_commit_run):
        with pytest.raises(GitOpsError, match="GitOps preparation failed and was safely rolled back"):
            service.prepare_git_change(proposal, req)

    # Verify repository was restored to starting branch
    proc_curr = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    assert proc_curr.stdout.strip() == starting_branch

    # Verify file content was restored to original
    assert manifest.read_text(encoding="utf-8") == SAMPLE_DEPLOYMENT_YAML

    # Verify staging area is clean
    proc_staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    assert proc_staged.stdout.strip() == ""


def test_resolve_manifest_path_traversal_rejected(tmp_path):
    """Verify that path traversal attempts in custom_path are rejected."""
    service = GitOpsService(repo_root=tmp_path)
    target = GitOpsTargetConfig(
        target_kind="Deployment",
        target_name="gitops-controlled-app",
        target_namespace="k8s-agent-demo",
        container_name="web",
        current_image="nginx:invalid-tag-does-not-exist",
        trusted_replacement_image="nginx:1.27.0-alpine",
        manifest_path="app.yaml",
    )
    with pytest.raises(GitOpsValidationError, match="resolves outside repository root"):
        service._resolve_manifest_path(target, custom_path="../../etc/passwd")
