# ADR-004: three-way merge with field ownership instead of last-write-wins

Status: accepted

## Context

Technicians work offline in basements and shafts while managers edit the
same assets in the registry. Last-write-wins would silently drop one side.

## Decision

The field app pushes changes with the revision they were based on. The
registry keeps every revision (`entity_history`) and merges per field:
disjoint changes merge, overlapping changes are decided by ownership -
field fields (condition, inspection data, execution status) go to the
technician, office fields (location, ratings, assignment) stay with the
office, shared fields (`notes`) become a conflict resolved by a manager.

## Consequences

- No silent loss of a concurrent edit.
- History grows with every revision; it is also the audit trail.
- The client must fold consecutive local edits into one change and keep
  pending fields when older server state arrives.
- Protocol and worked example: [sync-protocol.md](../sync-protocol.md).
