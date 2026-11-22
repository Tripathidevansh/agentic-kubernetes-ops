"""
GitOps Remediation Service for KafaKube8.

Implements the GitOps Remediation Foundation (Phase 7A):
- Validates approved RemediationProposals with strict human-in-the-loop review.
- Validates target resources and container images against trusted configurations.
- Safely modifies Kubernetes YAML manifests preserving formatting, comments, and non-target fields.
- Generates clean unified diffs for review.
- Prepares local Git feature branches and commits strictly locally.
- Enforces ZERO cluster mutations (no kubectl writes) and ZERO remote Git pushes.
"""

import asyncio
from datetime import datetime, timezone
import difflib
import logging
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

import yaml

from app.models.gitops import (
    GitOpsAuditEvent,
    GitOpsChangeRecord,
    GitOpsChangeRequest,
    GitOpsPreviewResponse,
    GitOpsStatus,
    GitOpsTargetConfig,
)
from app.models.remediation import (
    ApprovalStatus,
    ProposalStatus,
    RemediationProposal,
)
from app.validators.safety_validator import (
    SafetyViolationError,
    validate_secret_safety,
)

logger = logging.getLogger(__name__)


class GitOpsError(Exception):
    """Base exception for GitOps service operations."""
    pass


class GitOpsValidationError(GitOpsError):
    """Raised when target configuration, proposal state, or replacement image is invalid."""
    pass


class GitOpsApprovalRequiredError(GitOpsError):
    """Raised when attempting GitOps preparation on a proposal without explicit human approval."""
    pass


class GitOpsDirtyWorkingTreeError(GitOpsError):
    """Raised when the target manifest file has uncommitted working tree changes."""
    pass


class GitOpsManifestSyntaxError(GitOpsError):
    """Raised when manifest YAML syntax is invalid or corrupted."""
    pass


# Default trusted remediation target configurations
DEFAULT_TRUSTED_TARGETS: Dict[Tuple[str, str, str], GitOpsTargetConfig] = {
    ("Deployment", "gitops-controlled-app", "k8s-agent-demo"): GitOpsTargetConfig(
        target_kind="Deployment",
        target_name="gitops-controlled-app",
        target_namespace="k8s-agent-demo",
        container_name="web",
        current_image="nginx:invalid-tag-does-not-exist",
        trusted_replacement_image="nginx:1.27.0-alpine",
        manifest_path="python-v2/manifests/gitops/controlled-imagepull.yaml",
        description="Controlled ImagePullBackOff scenario with verified alpine replacement image",
    ),
}


def safe_replace_container_image(
    original_yaml_content: str,
    target_kind: str,
    target_name: str,
    container_name: str,
    expected_current_image: str,
    new_image: str,
    target_namespace: Optional[str] = None,
) -> str:
    """
    Safely update a specific container image in a Kubernetes YAML manifest.

    Preserves comments, formatting, indentation, and all other resource fields.
    Validates AST before and after replacement to ensure zero unintended mutations.

    Raises:
        GitOpsManifestSyntaxError: If input YAML has syntax errors.
        GitOpsValidationError: If resource kind, name, container, or current image does not match.
        SafetyViolationError: If resulting YAML violates safety constraints.
    """
    # 1. Parse original YAML into AST
    try:
        parsed_doc = yaml.safe_load(original_yaml_content)
    except yaml.YAMLError as exc:
        raise GitOpsManifestSyntaxError(f"Invalid YAML syntax in manifest: {exc}") from exc

    if not isinstance(parsed_doc, dict):
        raise GitOpsValidationError("Manifest must define a YAML dictionary resource.")

    # 2. Validate Resource Kind and Name
    actual_kind = parsed_doc.get("kind")
    if actual_kind != target_kind:
        raise GitOpsValidationError(
            f"Resource kind mismatch: Manifest defines '{actual_kind}', expected '{target_kind}'."
        )

    metadata = parsed_doc.get("metadata", {})
    actual_name = metadata.get("name")
    if actual_name != target_name:
        raise GitOpsValidationError(
            f"Resource name mismatch: Manifest defines '{actual_name}', expected '{target_name}'."
        )

    if target_namespace and metadata.get("namespace"):
        if metadata.get("namespace") != target_namespace:
            raise GitOpsValidationError(
                f"Namespace mismatch: Manifest defines '{metadata.get('namespace')}', expected '{target_namespace}'."
            )

    # 3. Locate Target Container
    spec = parsed_doc.get("spec", {})
    template = spec.get("template", {}) if target_kind in ("Deployment", "StatefulSet", "DaemonSet", "Job") else {}
    pod_spec = template.get("spec", {}) if template else spec

    containers = pod_spec.get("containers", [])
    if not isinstance(containers, list):
        raise GitOpsValidationError(f"Invalid container specification in {target_kind}/{target_name}.")

    matching_container = None
    container_index = -1
    for idx, c in enumerate(containers):
        if isinstance(c, dict) and c.get("name") == container_name:
            matching_container = c
            container_index = idx
            break

    if matching_container is None:
        raise GitOpsValidationError(
            f"Container '{container_name}' not found in {target_kind}/{target_name} manifest."
        )

    actual_current_image = matching_container.get("image")
    if actual_current_image != expected_current_image:
        raise GitOpsValidationError(
            f"Container '{container_name}' has image '{actual_current_image}', expected '{expected_current_image}'."
        )

    # 4. Perform Precise Line-Based Replacement to Preserve Comments & Formatting
    lines = original_yaml_content.splitlines(keepends=True)
    in_containers_block = False
    in_target_container = False
    replaced = False
    new_lines = []

    # Pattern to match container item or name
    container_name_pattern = re.compile(rf"^\s*(?:-\s+)?name:\s*['\"]?{re.escape(container_name)}['\"]?\s*(?:#.*)?$")
    image_pattern = re.compile(rf"^(\s*)image:\s*['\"]?{re.escape(expected_current_image)}['\"]?(\s*(?:#.*)?)$")

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("containers:"):
            in_containers_block = True

        if in_containers_block:
            if container_name_pattern.match(line):
                in_target_container = True
            elif in_target_container and (stripped.startswith("- name:") or (stripped.startswith("-") and "name:" in stripped)):
                # Entered a different container
                in_target_container = False

            if in_target_container and not replaced:
                m = image_pattern.match(line)
                if m:
                    indent = m.group(1)
                    trailing_comment = m.group(2)
                    new_line = f"{indent}image: {new_image}{trailing_comment}"
                    # Ensure newline is preserved
                    if line.endswith("\r\n"):
                        new_line = new_line.rstrip("\r\n") + "\r\n"
                    elif line.endswith("\n"):
                        new_line = new_line.rstrip("\n") + "\n"
                    new_lines.append(new_line)
                    replaced = True
                    continue

        new_lines.append(line)

    if not replaced:
        # Fallback: line-by-line single image replacement if strict container block was indented differently
        direct_pattern = re.compile(rf"^(\s*)image:\s*['\"]?{re.escape(expected_current_image)}['\"]?(\s*(?:#.*)?)$")
        new_lines = []
        for line in lines:
            if not replaced and direct_pattern.match(line):
                m = direct_pattern.match(line)
                indent = m.group(1)
                trailing_comment = m.group(2)
                new_line = f"{indent}image: {new_image}{trailing_comment}"
                if line.endswith("\r\n"):
                    new_line = new_line.rstrip("\r\n") + "\r\n"
                elif line.endswith("\n"):
                    new_line = new_line.rstrip("\n") + "\n"
                new_lines.append(new_line)
                replaced = True
            else:
                new_lines.append(line)

    if not replaced:
        raise GitOpsValidationError(
            f"Failed to cleanly locate and replace image line for container '{container_name}'."
        )

    updated_yaml_content = "".join(new_lines)

    # 5. AST Roundtrip Verification
    try:
        updated_doc = yaml.safe_load(updated_yaml_content)
    except yaml.YAMLError as exc:
        raise GitOpsManifestSyntaxError(f"Updated manifest has invalid YAML syntax: {exc}") from exc

    # Verify only the image field changed in the AST
    updated_spec = updated_doc.get("spec", {})
    updated_template = updated_spec.get("template", {}) if target_kind in ("Deployment", "StatefulSet", "DaemonSet", "Job") else {}
    updated_pod_spec = updated_template.get("spec", {}) if updated_template else updated_spec
    updated_containers = updated_pod_spec.get("containers", [])

    if len(updated_containers) <= container_index:
        raise GitOpsValidationError("AST verification failed: Container list length mismatch.")

    new_actual_image = updated_containers[container_index].get("image")
    if new_actual_image != new_image:
        raise GitOpsValidationError(
            f"AST verification failed: Container image is '{new_actual_image}', expected '{new_image}'."
        )

    # Revert image in AST copy to assert full equality with original
    updated_containers[container_index]["image"] = expected_current_image
    if updated_doc != parsed_doc:
        raise GitOpsValidationError(
            "AST verification failed: Fields other than the target container image were altered."
        )

    # 6. Safety Guardrails Validation
    validate_secret_safety(updated_yaml_content)

    return updated_yaml_content


def generate_unified_diff(
    original_content: str,
    updated_content: str,
    file_path: str,
) -> str:
    """Generate a clean unified diff formatted for Git review."""
    orig_lines = original_content.splitlines(keepends=True)
    upd_lines = updated_content.splitlines(keepends=True)

    rel_name = os.path.basename(file_path)
    diff_lines = list(
        difflib.unified_diff(
            orig_lines,
            upd_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
    )
    return "".join(diff_lines)


class GitOpsService:
    """
    Modular, safe GitOps Remediation Service.

    Orchestrates target resolution, human approval validation, safe YAML updates,
    diff generation, and local Git feature branch preparation.
    """

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        trusted_targets: Optional[Dict[Tuple[str, str, str], GitOpsTargetConfig]] = None,
    ):
        if repo_root is None:
            # Robust upward discovery of the Git repository root (.git directory)
            search_candidates = [
                Path(__file__).resolve().parent,
                Path.cwd(),
            ]
            discovered_root: Optional[Path] = None
            for base in search_candidates:
                for candidate in [base, *base.parents]:
                    if (candidate / ".git").exists():
                        discovered_root = candidate
                        break
                if discovered_root:
                    break
            self.repo_root = discovered_root if discovered_root else Path.cwd()
        else:
            self.repo_root = Path(repo_root).resolve()

        self.trusted_targets = dict(trusted_targets or DEFAULT_TRUSTED_TARGETS)
        self._lock = asyncio.Lock()
        self._change_records: Dict[str, GitOpsChangeRecord] = {}

    def register_target(self, config: GitOpsTargetConfig) -> None:
        """Register or override a trusted remediation target."""
        key = (config.target_kind, config.target_name, config.target_namespace)
        self.trusted_targets[key] = config

    def get_target_config(
        self,
        kind: str,
        name: str,
        namespace: str,
    ) -> Optional[GitOpsTargetConfig]:
        """Look up target configuration by kind, name, and namespace."""
        return self.trusted_targets.get((kind, name, namespace))

    def list_trusted_targets(self) -> List[GitOpsTargetConfig]:
        """Return all configured trusted targets."""
        return list(self.trusted_targets.values())

    def validate_proposal(
        self,
        proposal: RemediationProposal,
        require_approval: bool = True,
    ) -> List[str]:
        """
        Validate proposal compliance with GitOps safety prerequisites.

        Returns list of validation error messages. Empty list indicates full compliance.
        """
        errors = []

        if not proposal.can_remediate:
            errors.append(
                f"Proposal '{proposal.proposal_id}' cannot be remediated: can_remediate=False. "
                f"Reason: {proposal.insufficient_evidence_reason or 'Insufficient evidence'}"
            )

        if proposal.status != ProposalStatus.ACTIONABLE:
            errors.append(
                f"Proposal status is '{proposal.status.value}', expected '{ProposalStatus.ACTIONABLE.value}'."
            )

        if require_approval:
            if proposal.approval is None:
                errors.append("Proposal lacks human approval record (approval is None).")
            elif proposal.approval.status != ApprovalStatus.APPROVED:
                errors.append(
                    f"Proposal requires explicit operator approval before Git preparation. "
                    f"Current status: '{proposal.approval.status.value}'."
                )

        target_key = (proposal.target_kind, proposal.target_name, proposal.target_namespace)
        if target_key not in self.trusted_targets:
            errors.append(
                f"Target {proposal.target_kind}/{proposal.target_name} in namespace '{proposal.target_namespace}' "
                f"is not a configured trusted GitOps remediation target."
            )

        return errors

    def _resolve_manifest_path(self, target_config: GitOpsTargetConfig, custom_path: Optional[str] = None) -> Path:
        """Resolve manifest path on disk relative to repo root with path traversal protection."""
        rel_str = custom_path or target_config.manifest_path
        candidate = Path(rel_str)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self.repo_root / candidate).resolve()
            if not resolved.exists():
                # Check relative to backend or python-v2 if running in subfolder
                alt = (Path.cwd() / candidate).resolve()
                if alt.exists():
                    resolved = alt

        try:
            resolved.relative_to(self.repo_root)
        except ValueError:
            raise GitOpsValidationError(
                f"Manifest path '{rel_str}' resolves outside repository root. Path traversal is rejected."
            )
        return resolved

    def preview_change(
        self,
        proposal: RemediationProposal,
        request: Optional[GitOpsChangeRequest] = None,
    ) -> GitOpsPreviewResponse:
        """
        Generate a unified diff preview and safety evaluation for a proposal without writing to disk or Git.
        """
        validation_errors = self.validate_proposal(proposal, require_approval=False)

        target_key = (proposal.target_kind, proposal.target_name, proposal.target_namespace)
        target_config = self.trusted_targets.get(target_key)

        if not target_config:
            raise GitOpsValidationError(
                f"Target {proposal.target_kind}/{proposal.target_name} in namespace '{proposal.target_namespace}' "
                f"is not a configured trusted GitOps target."
            )

        # Resolve replacement image
        req_image = request.target_image if request else None
        if req_image and req_image != target_config.trusted_replacement_image:
            raise GitOpsValidationError(
                f"Requested image '{req_image}' does not match trusted replacement image "
                f"'{target_config.trusted_replacement_image}'."
            )
        replacement_image = target_config.trusted_replacement_image

        custom_path = request.manifest_path if request else None
        manifest_file = self._resolve_manifest_path(target_config, custom_path)

        if not manifest_file.exists():
            raise GitOpsValidationError(f"Manifest file not found on disk: {manifest_file}")

        try:
            original_content = manifest_file.read_text(encoding="utf-8")
        except Exception as read_err:
            raise GitOpsError(f"Failed to read manifest file '{manifest_file}': {read_err}") from read_err

        # Perform safe container image update
        updated_content = safe_replace_container_image(
            original_yaml_content=original_content,
            target_kind=target_config.target_kind,
            target_name=target_config.target_name,
            container_name=target_config.container_name,
            expected_current_image=target_config.current_image,
            new_image=replacement_image,
            target_namespace=target_config.target_namespace,
        )

        # Relative path for display in diff
        try:
            rel_display_path = str(manifest_file.relative_to(self.repo_root))
        except ValueError:
            rel_display_path = manifest_file.name

        diff = generate_unified_diff(original_content, updated_content, rel_display_path)

        is_approved = (
            proposal.approval is not None
            and proposal.approval.status == ApprovalStatus.APPROVED
        )
        can_prepare = len(validation_errors) == 0 and is_approved

        return GitOpsPreviewResponse(
            proposal_id=proposal.proposal_id,
            target_resource=f"{target_config.target_kind}/{target_config.target_name}",
            target_namespace=target_config.target_namespace,
            target_file=rel_display_path,
            container_name=target_config.container_name,
            current_image=target_config.current_image,
            replacement_image=replacement_image,
            diff=diff,
            is_approved=is_approved,
            can_prepare=can_prepare,
            validation_messages=validation_errors,
        )

    def _run_git_command(self, args: List[str]) -> Tuple[int, str, str]:
        """Execute a git command within the repo_root."""
        # Safety invariant check
        for forbidden in ("push", "remote", "pull"):
            if forbidden in args:
                raise GitOpsValidationError(f"Git command '{forbidden}' is strictly disallowed by safety policy.")

        proc = subprocess.run(
            ["git"] + args,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

    def prepare_git_change(
        self,
        proposal: RemediationProposal,
        request: GitOpsChangeRequest,
    ) -> GitOpsChangeRecord:
        """
        Prepare a local Git feature branch containing the remediated manifest.

        SAFETY INVARIANTS:
        1. Explicit operator approval is MANDATORY (raises GitOpsApprovalRequiredError).
        2. Dirty working tree on target manifest triggers fail-closed abort (raises GitOpsDirtyWorkingTreeError).
        3. Never calls `git push`, never interacts with remote Git repositories.
        4. Never calls `kubectl` or mutates cluster resources.
        5. Switches back to original branch so developer's active branch is preserved.
        """
        # 1. Enforce strict human approval and actionability
        validation_errors = self.validate_proposal(proposal, require_approval=True)
        if validation_errors:
            if proposal.approval is None or proposal.approval.status != ApprovalStatus.APPROVED:
                raise GitOpsApprovalRequiredError(
                    f"Operator approval required before preparing Git branch: {'; '.join(validation_errors)}"
                )
            raise GitOpsValidationError("; ".join(validation_errors))

        # Check for duplicate preparation of the same proposal
        for existing in self._change_records.values():
            if existing.proposal_id == proposal.proposal_id and existing.status == GitOpsStatus.BRANCH_PREPARED:
                raise GitOpsValidationError(
                    f"Proposal '{proposal.proposal_id}' has already been prepared on branch '{existing.branch_name}' "
                    f"(Change ID: '{existing.change_id}'). Duplicate preparation is disallowed."
                )

        # 2. Resolve target configuration
        target_key = (proposal.target_kind, proposal.target_name, proposal.target_namespace)
        target_config = self.trusted_targets.get(target_key)
        if not target_config:
            raise GitOpsValidationError(
                f"Target {proposal.target_kind}/{proposal.target_name} is not a trusted GitOps target."
            )

        replacement_image = target_config.trusted_replacement_image
        if request.target_image and request.target_image != replacement_image:
            raise GitOpsValidationError(
                f"Requested image '{request.target_image}' does not match trusted replacement '{replacement_image}'."
            )

        manifest_file = self._resolve_manifest_path(target_config, request.manifest_path)
        if not manifest_file.exists():
            raise GitOpsValidationError(f"Manifest file not found: {manifest_file}")

        try:
            rel_manifest_path = str(manifest_file.relative_to(self.repo_root))
        except ValueError:
            rel_manifest_path = str(manifest_file)

        # 3. Check for Git Repository
        if not (self.repo_root / ".git").exists():
            raise GitOpsError(f"Target directory '{self.repo_root}' is not a git repository.")

        # 4. Check for Dirty Working Tree & Staged Index
        # First ensure staging area (index) has no unrelated staged changes
        code_cached, staged_out, _ = self._run_git_command(["diff", "--cached", "--name-only"])
        if code_cached == 0 and staged_out:
            raise GitOpsDirtyWorkingTreeError(
                f"Git staging area contains uncommitted staged changes:\n{staged_out}\n"
                f"GitOps preparation aborted to prevent staging pollution."
            )

        # Check for dirty changes on the target manifest file
        code, out, err = self._run_git_command(["status", "--porcelain", rel_manifest_path])
        if code != 0:
            raise GitOpsError(f"Git status failed: {err}")
        if out:
            raise GitOpsDirtyWorkingTreeError(
                f"Target manifest '{rel_manifest_path}' has uncommitted changes in the working tree. "
                f"GitOps preparation aborted to prevent clobbering uncommitted work."
            )

        # 5. Read and transform manifest YAML
        original_content = manifest_file.read_text(encoding="utf-8")
        updated_content = safe_replace_container_image(
            original_yaml_content=original_content,
            target_kind=target_config.target_kind,
            target_name=target_config.target_name,
            container_name=target_config.container_name,
            expected_current_image=target_config.current_image,
            new_image=replacement_image,
            target_namespace=target_config.target_namespace,
        )

        diff = generate_unified_diff(original_content, updated_content, rel_manifest_path)

        # 6. Determine Starting Branch and Starting HEAD SHA
        code, current_branch, _ = self._run_git_command(["rev-parse", "--abbrev-ref", "HEAD"])
        if code != 0 or not current_branch:
            current_branch = "HEAD"

        code_head, starting_head_sha, _ = self._run_git_command(["rev-parse", "HEAD"])
        if code_head != 0 or not starting_head_sha:
            starting_head_sha = "unknown"

        short_id = proposal.proposal_id.replace("-", "")[:8]
        branch_name = request.branch_name or f"gitops/remediation-{short_id}"

        # 7. Check for Branch Collision
        code_branch_check, _, _ = self._run_git_command(["show-ref", "--verify", f"refs/heads/{branch_name}"])
        if code_branch_check == 0:
            raise GitOpsValidationError(
                f"Git feature branch '{branch_name}' already exists. "
                f"Cannot prepare change: branch collision detected."
            )

        audit_events = [
            GitOpsAuditEvent(
                event_type="APPROVAL_VERIFIED",
                operator=request.operator,
                details={
                    "proposal_id": proposal.proposal_id,
                    "reviewer": proposal.approval.reviewer if proposal.approval else None,
                    "reviewed_at": proposal.approval.reviewed_at if proposal.approval else None,
                    "starting_branch": current_branch,
                    "starting_head": starting_head_sha,
                },
            ),
        ]

        # 8. Perform Local Git Operations with Atomic Rollback
        commit_hash = None
        commit_message = (
            f"gitops: remediate {proposal.target_kind}/{proposal.target_name} ({target_config.container_name})\n\n"
            f"Proposal ID: {proposal.proposal_id}\n"
            f"Approved by: {proposal.approval.reviewer if proposal.approval else request.operator}\n"
            f"Image update: {target_config.current_image} -> {replacement_image}\n"
            f"Justification: {proposal.justification}\n"
        )

        branch_created = False
        file_written = False
        file_staged = False

        try:
            # Check out new feature branch
            code, out, err = self._run_git_command(["checkout", "-b", branch_name])
            if code != 0:
                raise GitOpsError(f"Failed to create branch '{branch_name}': {err}")
            branch_created = True

            # Write updated manifest
            manifest_file.write_text(updated_content, encoding="utf-8")
            file_written = True

            # Stage ONLY the target manifest file
            code, _, err = self._run_git_command(["add", rel_manifest_path])
            if code != 0:
                raise GitOpsError(f"Failed to stage manifest '{rel_manifest_path}': {err}")
            file_staged = True

            # Commit locally with explicit author metadata
            operator_name = request.operator or "KafaKube8 GitOps"
            commit_env = {
                "GIT_AUTHOR_NAME": operator_name,
                "GIT_AUTHOR_EMAIL": f"{operator_name.lower().replace(' ', '.')}@kafakube8.local",
                "GIT_COMMITTER_NAME": "KafaKube8 Operator",
                "GIT_COMMITTER_EMAIL": "operator@kafakube8.local",
            }
            proc = subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                env={**os.environ, **commit_env},
            )
            if proc.returncode != 0:
                raise GitOpsError(f"Failed to commit GitOps change: {proc.stderr}")

            # Retrieve commit hash
            code, commit_hash, _ = self._run_git_command(["rev-parse", "HEAD"])
            if code != 0:
                commit_hash = "unknown"

            audit_events.append(
                GitOpsAuditEvent(
                    event_type="BRANCH_PREPARED",
                    operator=request.operator,
                    details={
                        "branch_name": branch_name,
                        "commit_hash": commit_hash,
                        "starting_head": starting_head_sha,
                        "starting_branch": current_branch,
                        "manifest_path": rel_manifest_path,
                    },
                )
            )

        except Exception as exc:
            logger.error("GitOps preparation failed: %s. Initiating transaction rollback...", exc)
            if file_staged:
                self._run_git_command(["reset", "HEAD", "--", rel_manifest_path])
            if file_written:
                try:
                    manifest_file.write_text(original_content, encoding="utf-8")
                except Exception:
                    pass
            if current_branch and current_branch != "HEAD":
                self._run_git_command(["checkout", current_branch])
            if branch_created:
                self._run_git_command(["branch", "-D", branch_name])
            raise GitOpsError(
                f"GitOps preparation failed and was safely rolled back to starting branch '{current_branch}' "
                f"(HEAD: {starting_head_sha[:8]}): {exc}"
            ) from exc

        finally:
            # Always ensure repo is returned to starting branch
            code_curr, active_branch, _ = self._run_git_command(["rev-parse", "--abbrev-ref", "HEAD"])
            if active_branch != current_branch and current_branch != "HEAD":
                self._run_git_command(["checkout", current_branch])

        # 8. Construct Persistent Change Record
        record = GitOpsChangeRecord(
            proposal_id=proposal.proposal_id,
            target_resource=f"{target_config.target_kind}/{target_config.target_name}",
            target_namespace=target_config.target_namespace,
            target_file=rel_manifest_path,
            container_name=target_config.container_name,
            current_image=target_config.current_image,
            replacement_image=replacement_image,
            diff=diff,
            branch_name=branch_name,
            commit_hash=commit_hash,
            commit_message=commit_message,
            status=GitOpsStatus.BRANCH_PREPARED,
            operator=request.operator,
            prepared_at=datetime.now(timezone.utc).isoformat(),
            audit_events=audit_events,
        )

        self._change_records[record.change_id] = record
        return record

    async def get_change_record(self, change_id: str) -> Optional[GitOpsChangeRecord]:
        """Retrieve change record by ID."""
        async with self._lock:
            return self._change_records.get(change_id)

    async def list_change_records(
        self,
        proposal_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[GitOpsChangeRecord]:
        """List all prepared change records with optional filtering."""
        async with self._lock:
            records = list(self._change_records.values())

        if proposal_id:
            records = [r for r in records if r.proposal_id == proposal_id]
        if status:
            records = [r for r in records if r.status.value == status or r.status == status]

        return records


# Global Singleton Service Instance
_gitops_service: Optional[GitOpsService] = None


def get_gitops_service() -> GitOpsService:
    """Retrieve or initialize the global GitOpsService singleton."""
    global _gitops_service
    if _gitops_service is None:
        _gitops_service = GitOpsService()
    return _gitops_service


def reset_gitops_service(service: Optional[GitOpsService] = None) -> None:
    """Reset the global GitOpsService instance (primarily for test teardown)."""
    global _gitops_service
    _gitops_service = service
